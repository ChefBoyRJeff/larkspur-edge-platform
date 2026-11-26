<#
.SYNOPSIS
Uplink-cut drill: proves that shipment events made while the site has no link to Azure are kept and
sent once the link returns, with nothing lost, even if the sync worker restarts during the outage.

Steps: pause Argo CD self-heal (root and the environment's app); delete the sync worker's egress
policy (allow-larkspur-track-sync-to-azure), which cuts it off from Azure; create drill shipments
through the app's own service code; check they stay unsent and the worker records failures; restart
the worker mid-outage and add one more shipment; turn self-heal back on, which re-creates the policy
from Git; time the catch-up; drain the environment's queue and check every drill event arrived.

Argo CD self-heal is always turned back on (finally), even if the drill fails. The drill shipments
(tracking numbers DRILL-<time>-<n>) stay in the database. Draining deletes every message in the
queue: run it against dev only. See docs/runbooks/sync.md. Not synced by Argo CD.
#>
param(
    [ValidateSet('dev')][string]$Environment = 'dev',
    # Longest wait for the worker to catch up once the link is back (its backoff tops out at 300 s).
    [int]$CatchUpTimeoutSeconds = 480
)
$ErrorActionPreference = 'Stop'
$ns = "larkspur-$Environment"
$app = "larkspur-track-$Environment"
$policy = 'allow-larkspur-track-sync-to-azure'
$queue = "larkspur-events-$Environment"
$site = $ns

function Invoke-Sql([string]$Sql) {
    $pod = kubectl get pods -n $ns -l 'cnpg.io/cluster=larkspur-db,cnpg.io/instanceRole=primary' -o jsonpath='{.items[0].metadata.name}'
    $result = kubectl exec -n $ns $pod -c postgres -- psql -U postgres -d larkspur -v ON_ERROR_STOP=1 -q -tAc $Sql
    if ($LASTEXITCODE) { throw "SQL failed on $pod" }
    $result
}
function Get-SyncPolicy([string]$Name) {
    (kubectl get application $Name -n argocd -o json | ConvertFrom-Json).spec.syncPolicy
}
function Set-SyncPolicy([string]$Name, [string]$PatchJson) {
    $file = Join-Path ([IO.Path]::GetTempPath()) "sync-drill-$Name.json"
    [IO.File]::WriteAllText($file, $PatchJson)
    try { kubectl patch application $Name -n argocd --type merge --patch-file $file | Out-Null; if ($LASTEXITCODE) { throw "could not patch $Name" } }
    finally { Remove-Item $file -ErrorAction SilentlyContinue }
}
function Get-SyncReady { kubectl get deployment larkspur-track-sync -n $ns -o jsonpath='{.status.availableReplicas}' }
function Wait-Until([scriptblock]$Condition, [int]$TimeoutSeconds, [int]$Every = 5) {
    $t0 = Get-Date
    while (-not (& $Condition)) {
        if (((Get-Date) - $t0).TotalSeconds -gt $TimeoutSeconds) { return $false }
        Start-Sleep -Seconds $Every
    }
    $true
}
# Creates shipments with the app's own service code inside the web container (same transactions,
# same outbox rows as the web pages). Prints the tracking numbers.
$python = @'
import sys
from sqlalchemy.orm import Session
from larkspur_track.db import get_engine
from larkspur_track.services import advance_shipment, create_shipment
from larkspur_track.workflow import ShipmentStatus

prefix, first, count, advance = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] == "1"
made = []
with Session(get_engine()) as session:
    for n in range(first, first + count):
        shipment = create_shipment(
            session,
            tracking_number=f"{prefix}-{n}",
            consignee="Uplink drill",
            destination="Drill bay",
            note="uplink-cut drill",
        )
        session.commit()
        if advance:
            advance_shipment(session, shipment.id, ShipmentStatus.STORED, note="uplink-cut drill")
            session.commit()
        made.append(shipment.tracking_number)
print(" ".join(made))
'@
function New-DrillShipments([int]$First, [int]$Count, [bool]$Advance) {
    $made = $python | kubectl exec -i -n $ns deploy/larkspur-track -c web -- python - $prefix $First $Count ([int]$Advance)
    if ($LASTEXITCODE) { throw 'creating drill shipments failed' }
    ($made | Select-Object -Last 1)
}
$unsentSql = 'select count(*) from outbox where sent_at is null'

# 0. Preconditions: worker running, nothing waiting, self-heal on.
if ((Get-SyncReady) -ne '1') { throw "larkspur-track-sync is not ready in $ns" }
if ((Invoke-Sql $unsentSql) -ne '0') { throw "the $ns outbox has unsent events; let the worker catch up first" }
$original = @{}
foreach ($name in 'root', $app) {
    $sp = Get-SyncPolicy $name
    if (-not $sp.automated) { throw "Argo CD automated sync is already off for $name; turn it back on first (kubectl apply -f platform/bootstrap/root-app.yaml)" }
    $original[$name] = $sp | ConvertTo-Json -Depth 10 -Compress
}
if (-not (kubectl get networkpolicy $policy -n $ns --ignore-not-found -o name)) { throw "$policy is missing in $ns before the drill" }
$baseline = Invoke-Sql 'select coalesce(max(id), 0) from outbox'
$prefix = "DRILL-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmm'))"

$restored = $false
try {
    # 1. Cut the link.
    foreach ($name in 'root', $app) { Set-SyncPolicy $name '{"spec":{"syncPolicy":{"automated":null}}}' }
    "Argo CD self-heal paused for root and $app"
    kubectl delete networkpolicy $policy -n $ns | Out-Null
    $cutAt = Get-Date
    "link cut at $($cutAt.ToString('hh:mm:ss tt')): $policy deleted"

    # 2. Work goes on during the outage.
    $madeA = New-DrillShipments 1 3 $true
    "created during the outage: $madeA (received, then stored: 6 events)"
    $failed = Wait-Until { [int](Invoke-Sql "select count(*) from outbox where id > $baseline and last_error is not null") -gt 0 } 90
    if (-not $failed) { throw 'the worker did not record a failed send within 90 s; is the link really cut?' }
    Start-Sleep -Seconds 30
    "outbox during the outage: $(Invoke-Sql "select count(*) || ' drill events, ' || count(*) filter (where sent_at is null) || ' unsent, ' || count(last_error) || ' with an error' from outbox where id > $baseline"); oldest: $(Invoke-Sql "select 'attempts ' || attempts || ', ' || coalesce(last_error, 'no error') from outbox where id > $baseline order by id limit 1")"

    # 3. Restart the worker mid-outage; the events must still be waiting.
    $attemptsBefore = [int](Invoke-Sql "select attempts from outbox where id > $baseline order by id limit 1")
    kubectl rollout restart deployment/larkspur-track-sync -n $ns | Out-Null
    kubectl rollout status deployment/larkspur-track-sync -n $ns --timeout=180s | Out-Null
    if ($LASTEXITCODE) { throw 'the worker did not come back after the restart' }
    $retried = Wait-Until { [int](Invoke-Sql "select attempts from outbox where id > $baseline order by id limit 1") -gt $attemptsBefore } 90
    $madeB = New-DrillShipments 4 1 $false
    "worker restarted mid-outage; new pod retried: $retried; created after the restart: $madeB (1 event)"
    "unsent before the link returns: $(Invoke-Sql "select count(*) from outbox where id > $baseline and sent_at is null") of $(Invoke-Sql "select count(*) from outbox where id > $baseline")"

    # 4. Restore the link the GitOps way: self-heal back on re-creates the policy from Git.
    foreach ($name in $app, 'root') { Set-SyncPolicy $name "{""spec"":{""syncPolicy"":$($original[$name])}}" }
    $restored = $true
    kubectl -n argocd annotate application $app argocd.argoproj.io/refresh=hard --overwrite | Out-Null
    $back = Wait-Until { [bool](kubectl get networkpolicy $policy -n $ns --ignore-not-found -o name) } 180 2
    if (-not $back) { throw "Argo CD did not re-create $policy within 180 s" }
    $linkAt = Get-Date
    "self-heal back on; Argo CD re-created $policy at $($linkAt.ToString('hh:mm:ss tt')) (outage $([int]($linkAt - $cutAt).TotalSeconds) s)"
    $caughtUp = Wait-Until { (Invoke-Sql "select count(*) from outbox where id > $baseline and sent_at is null") -eq '0' } $CatchUpTimeoutSeconds
    if (-not $caughtUp) { throw "the worker did not catch up within $CatchUpTimeoutSeconds s of the link returning" }
    $catchUp = [int]((Get-Date) - $linkAt).TotalSeconds
    "caught up $catchUp s after the link returned"
    "outbox after: $(Invoke-Sql "select count(*) || ' drill events, ' || count(sent_at) || ' sent, max attempts ' || max(attempts) || ', errors left ' || count(last_error) from outbox where id > $baseline")"
    'worker log during the drill:'
    kubectl logs -n $ns deploy/larkspur-track-sync --since=15m | Select-String 'paused|resumed|sent|started' | ForEach-Object { "   $_" }
}
finally {
    if (-not $restored) {
        foreach ($name in $app, 'root') { if ($original[$name]) { try { Set-SyncPolicy $name "{""spec"":{""syncPolicy"":$($original[$name])}}" } catch { "WARNING: could not turn self-heal back on for $name; run: kubectl apply -f platform/bootstrap/root-app.yaml" } } }
        'Argo CD self-heal turned back on after a failure'
    }
}

# 5. Drain the queue and check every drill event arrived (Azure needs no order; duplicates are
#    allowed by design and removed by event_id).
$expected = @(Invoke-Sql "select event_id from outbox where id > $baseline order by id")
$account = az storage account list -g rg-lark-edge-integration --query '[0].name' -o tsv
$key = az storage account keys list -g rg-lark-edge-integration -n $account --query '[0].value' -o tsv
$arrived = [Collections.Generic.List[string]]::new()
$other = 0
try {
    foreach ($round in 1..100) {
        $batch = @(az storage message get --queue-name $queue --account-name $account --account-key $key --num-messages 32 --visibility-timeout 600 -o json --only-show-errors | ConvertFrom-Json)
        if (-not $batch.Count) { break }
        foreach ($m in $batch) {
            $body = $m.content
            try { $body = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($body)) } catch { }
            try { $msg = $body | ConvertFrom-Json } catch { $msg = $null }
            if ($msg -and $msg.site -eq $site -and $msg.event.event_id) { $arrived.Add([string]$msg.event.event_id) } else { $other++ }
        }
    }
    az storage message clear --queue-name $queue --account-name $account --account-key $key --only-show-errors | Out-Null
}
finally { Remove-Variable key }
$drill = @($arrived | Where-Object { $expected -contains $_ })
$missing = @($expected | Where-Object { $arrived -notcontains $_ })
$dupes = @($drill | Group-Object | Where-Object Count -gt 1)
$inOrder = (($drill | Select-Object -Unique) -join ',') -eq ($expected -join ',')
"queue $queue drained: $($arrived.Count) $site messages ($($drill.Count) from this drill), $other other"
"drill events expected $($expected.Count), arrived $(@($drill | Select-Object -Unique).Count), missing $($missing.Count), duplicated $($dupes.Count); arrival in outbox order: $inOrder"
if ($missing.Count -eq 0 -and $expected.Count -eq 7) {
    "RESULT: PASS. $($expected.Count) events made during a $([int]($linkAt - $cutAt).TotalSeconds) s outage (with a worker restart) all reached Azure; caught up $catchUp s after the link returned."
}
else { "RESULT: FAIL. expected 7 drill events, missing $($missing.Count)." }
