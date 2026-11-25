<#
.SYNOPSIS
Restore drill: rebuilds an environment's database from its Azure backups into a NEW cluster,
larkspur-db-restore, next to the running one, measures how long that takes (RTO) and checks that
nothing was lost (RPO). The running database and the app are not touched, apart from a one-row
marker table that is created before the drill and dropped after it.

Steps: count rows in the source; write a marker row and wait until its WAL is in Azure; create
larkspur-db-restore from the backups (with temporary network policies it needs); wait until it is
healthy; compare row counts and the marker; delete the restored cluster, its policies and the
marker. See docs/runbooks/backups.md. Not synced by Argo CD (outside platform/apps and platform/config).
#>
param(
    [ValidateSet('dev', 'prod')][string]$Environment = 'dev',
    # Leave larkspur-db-restore running for inspection (delete it by hand afterwards).
    [switch]$KeepRestored
)
$ErrorActionPreference = 'Stop'
$ns = "larkspur-$Environment"
$restore = 'larkspur-db-restore'
if (kubectl get cluster $restore -n $ns --ignore-not-found -o name) { throw "$restore already exists in $ns; delete it first" }

function Invoke-Sql([string]$Pod, [string]$Sql, [string]$Database = 'larkspur') {
    $result = kubectl exec -n $ns $Pod -c postgres -- psql -U postgres -d $Database -v ON_ERROR_STOP=1 -q -tAc $Sql
    if ($LASTEXITCODE) { throw "SQL failed on $Pod" }
    $result
}
function Get-Primary([string]$Cluster) {
    kubectl get pods -n $ns -l "cnpg.io/cluster=$Cluster,cnpg.io/instanceRole=primary" -o jsonpath='{.items[0].metadata.name}'
}
$countsSql = "select string_agg(t || '=' || n, ' ' order by t) from (select 'audit_log' t, count(*) n from audit_log union all select 'shipment_events', count(*) from shipment_events union all select 'shipments', count(*) from shipments union all select 'users', count(*) from users) c"

# 1. Source: row counts and a marker row whose WAL must reach Azure before the restore starts.
$source = Get-Primary 'larkspur-db'
$image = kubectl get cluster larkspur-db -n $ns -o jsonpath='{.spec.imageName}'
$sourceCounts = Invoke-Sql $source $countsSql
Invoke-Sql $source 'create table if not exists restore_drill_marker (taken_at timestamptz not null)' | Out-Null
$marker = Invoke-Sql $source 'insert into restore_drill_marker values (clock_timestamp()) returning taken_at'
$wal = Invoke-Sql $source 'select pg_walfile_name(pg_switch_wal())' 'postgres'
$t0 = Get-Date
do {
    Start-Sleep -Seconds 2
    $archived = Invoke-Sql $source "select coalesce(last_archived_wal, '') from pg_stat_archiver" 'postgres'
} until ($archived -ge $wal -or ((Get-Date) - $t0).TotalSeconds -gt 300)
if ($archived -lt $wal) { throw "WAL $wal did not reach Azure within 5 minutes; check LarkspurWalArchivingFailing" }
"source $source ($Environment): $sourceCounts"
"marker $marker written; its WAL $wal reached Azure in $([int]((Get-Date) - $t0).TotalSeconds) s"

$ok = $false
try {
    # 2. The restored cluster, with the same network rules the database has (default-deny otherwise).
    $manifest = @"
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: drill-allow-cnpg-operator-to-restore
  namespace: $ns
spec:
  podSelector:
    matchLabels:
      cnpg.io/cluster: $restore
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: cnpg-system
          podSelector:
            matchLabels:
              app.kubernetes.io/name: cloudnative-pg
      ports:
        - { protocol: TCP, port: 8000 }
        - { protocol: TCP, port: 5432 }
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: drill-allow-restore-egress
  namespace: $ns
spec:
  podSelector:
    matchLabels:
      cnpg.io/cluster: $restore
  policyTypes: [Egress]
  egress:
    - to:
        - ipBlock: { cidr: 192.168.1.199/32 }
      ports:
        - { protocol: TCP, port: 6443 }
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16]
      ports:
        - { protocol: TCP, port: 443 }
---
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: $restore
  namespace: $ns
spec:
  instances: 1
  imageName: $image
  enablePDB: false
  bootstrap:
    recovery:
      source: origin
  externalClusters:
    - name: origin
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters:
          barmanObjectName: larkspur-db-backup
          serverName: larkspur-db
  storage:
    storageClass: local-path
    size: 2Gi
  resources:
    requests: { cpu: 100m, memory: 256Mi }
    limits: { memory: 512Mi }
"@
    $start = Get-Date
    $manifest | kubectl apply -f - | Out-Null
    if ($LASTEXITCODE) { throw 'could not create the restore cluster' }
    "restore started $($start.ToString('hh:mm:ss tt'))"

    # 3. Wait until the restored cluster is healthy (up to 30 minutes).
    do {
        Start-Sleep -Seconds 10
        $c = kubectl get cluster $restore -n $ns -o json | ConvertFrom-Json
    } until (($c.status.phase -eq 'Cluster in healthy state' -and $c.status.readyInstances -eq 1) -or ((Get-Date) - $start).TotalMinutes -gt 30)
    $rto = [int]((Get-Date) - $start).TotalSeconds
    "restored cluster: $($c.status.phase), ready instances $($c.status.readyInstances), after $rto s"

    # 4. Compare.
    if ($c.status.readyInstances -eq 1) {
        $restored = Get-Primary $restore
        $restoredCounts = Invoke-Sql $restored $countsSql
        $restoredMarker = Invoke-Sql $restored "select coalesce(max(taken_at)::text, 'missing') from restore_drill_marker where taken_at = '$marker'"
        "restored $restored ($Environment): $restoredCounts"
        "marker in the restore: $restoredMarker"
        $ok = ($restoredCounts -eq $sourceCounts) -and ($restoredMarker -ne 'missing')
    }

}
finally {
    # 5. Clean up, also after a failure.
    Invoke-Sql $source 'drop table if exists restore_drill_marker' | Out-Null
    if (-not $KeepRestored) {
        kubectl delete cluster $restore -n $ns --ignore-not-found --wait=true --timeout=120s | Out-Null
        kubectl delete networkpolicy -n $ns drill-allow-cnpg-operator-to-restore drill-allow-restore-egress --ignore-not-found | Out-Null
        "cleaned up: $restore and its policies deleted; marker table dropped"
    } else { "kept $restore; marker table dropped from the source" }

}

if ($ok) { "RESULT: PASS. Restore of $Environment took $rto s (target 1800 s); counts match; the marker written just before the drill was recovered." }
else { throw "RESULT: FAIL. See the lines above (phase, counts, marker)." }
