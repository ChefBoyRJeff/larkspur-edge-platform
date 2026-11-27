<#
.SYNOPSIS
Node-loss drill: shuts a worker node down without draining it, the way a power cut or a dead
SD card would take it away, and records what users and the cluster see until it is back.

Steps: probe both environments' /readyz (it needs the database) once a second from this PC; shut
the node down over SSH (it asks for the sudo password); for -DownMinutes, log every change in the
node's state, the pods still bound to it, CoreDNS, Alertmanager and the probes; ask for the node to
be powered on again; wait until it is Ready and both environments answer; summarise outages and
alerts. Pods are not moved back afterwards; Kubernetes leaves them where they were rescheduled.
Only for worker nodes (never the control plane). See docs/runbooks/replace-a-node.md.
Not synced by Argo CD.
#>
param(
    [string]$Node = 'lark-k3s-02',
    # Long enough for Kubernetes to give up on the node (5 minutes) and reschedule its pods.
    [int]$DownMinutes = 8
)
$ErrorActionPreference = 'Stop'
$sites = [ordered]@{ prod = 'https://track.larkspur.home.arpa/readyz'; dev = 'https://track-dev.larkspur.home.arpa/readyz' }
$prom = '/api/v1/namespaces/monitoring/services/http:kube-prometheus-stack-prometheus:9090/proxy/api/v1'

$n = kubectl get node $Node -o json | ConvertFrom-Json
if ($n.metadata.labels.'node-role.kubernetes.io/control-plane') { throw "$Node is the control plane; this drill is for worker nodes" }
if (($n.status.conditions | Where-Object type -eq 'Ready').status -ne 'True') { throw "$Node is not Ready before the drill" }
foreach ($s in $sites.GetEnumerator()) { if ((Invoke-WebRequest -Uri $s.Value -TimeoutSec 5 -SkipHttpErrorCheck).StatusCode -ne 200) { throw "$($s.Key) is not answering before the drill" } }

function Get-State {
    $node = kubectl get node $Node -o json | ConvertFrom-Json
    $ready = ($node.status.conditions | Where-Object type -eq 'Ready').status
    $pods = @((kubectl get pods -A --field-selector "spec.nodeName=$Node" -o json | ConvertFrom-Json).items | Where-Object { $_.status.phase -ne 'Succeeded' })
    $terminating = @($pods | Where-Object { $_.metadata.deletionTimestamp }).Count
    $dns = kubectl get deployment coredns -n kube-system -o jsonpath='{.status.readyReplicas}'
    $am = kubectl get statefulset alertmanager-kube-prometheus-stack-alertmanager -n monitoring -o jsonpath='{.status.readyReplicas}'
    $web = foreach ($e in 'prod', 'dev') { "$e web $([int](kubectl get deployment larkspur-track -n "larkspur-$e" -o jsonpath='{.status.readyReplicas}'))/2" }
    "node Ready=$ready, taints [$(($node.spec.taints | ForEach-Object { $_.key -replace '^node.kubernetes.io/', '' }) -join ',')]; pods on it $($pods.Count) ($terminating terminating); CoreDNS ready $([int]$dns); Alertmanager ready $([int]$am); $($web -join ', ')"
}

# Users' view of each environment, once a second.
$probes = [ordered]@{}
$jobs = foreach ($s in $sites.GetEnumerator()) {
    $p = [hashtable]::Synchronized(@{ Stop = $false; Ok = 0; Fail = 0; Last = 'ok'; Changes = [Collections.ArrayList]::Synchronized([Collections.ArrayList]::new()) })
    $probes[$s.Key] = $p
    Start-ThreadJob -ArgumentList $p, $s.Value -ScriptBlock {
        param($p, $u)
        while (-not $p.Stop) {
            $t = Get-Date
            try { $code = (Invoke-WebRequest -Uri $u -TimeoutSec 5 -SkipHttpErrorCheck).StatusCode; $now = if ($code -eq 200) { 'ok' } else { "HTTP $code" } }
            catch { $now = 'no answer' }
            if ($now -eq 'ok') { $p.Ok++ } else { $p.Fail++ }
            if ($now -ne $p.Last) { [void]$p.Changes.Add("$($t.ToString('hh:mm:ss tt')) $now") }
            $p.Last = $now
            Start-Sleep -Seconds 1
        }
    }
}
try {
    "before: $(Get-State)"
    "shutting $Node down (sudo password for larkadmin on $Node)..."
    # Start-Process gives ssh the console itself, so the sudo prompt shows even when this
    # script's output is piped to a log.
    Start-Process ssh -ArgumentList '-t', "larkadmin@$Node.local", 'sudo shutdown -h now' -NoNewWindow -Wait
    $downAt = Get-Date
    "$Node shutdown sent at $($downAt.ToString('hh:mm:ss tt'))"
    $last = ''
    while (((Get-Date) - $downAt).TotalMinutes -lt $DownMinutes) {
        Start-Sleep -Seconds 10
        $state = Get-State
        if ($state -ne $last) { "   +$([int]((Get-Date) - $downAt).TotalSeconds) s: $state"; $last = $state }
    }
    $Host.UI.RawUI.FlushInputBuffer()
    Read-Host "Power $Node back on (press its power button, or unplug and reconnect its power), then press Enter" | Out-Null
    $onAt = Get-Date
    do {
        Start-Sleep -Seconds 10
        $state = Get-State
        if ($state -ne $last) { "   +$([int]((Get-Date) - $downAt).TotalSeconds) s: $state"; $last = $state }
    } until (($state -like 'node Ready=True*' -and @($probes.Values | Where-Object { $_.Last -ne 'ok' }).Count -eq 0) -or ((Get-Date) - $onAt).TotalMinutes -gt 10)
    "$Node Ready again $([int]((Get-Date) - $onAt).TotalSeconds) s after power-on was confirmed"
}
finally {
    Start-Sleep -Seconds 5
    foreach ($p in $probes.Values) { $p.Stop = $true }
    $jobs | Wait-Job -Timeout 15 | Out-Null
    $jobs | Remove-Job -Force
}
foreach ($k in $probes.Keys) { $p = $probes[$k]; "$k /readyz: $($p.Ok) ok, $($p.Fail) failed; changes: [$($p.Changes -join '; ')]" }
$window = [int][Math]::Ceiling(((Get-Date) - $downAt).TotalMinutes) + 2
$expr = 'max by (alertname, alertstate) (max_over_time(ALERTS{alertname!~"Watchdog|InfoInhibitor"}[WINDOWm]))'.Replace('WINDOW', "$window")
$fired = @((kubectl get --raw "$prom/query?query=$([uri]::EscapeDataString($expr))" | ConvertFrom-Json).data.result)
"alerts pending or firing during the drill: [$(($fired | ForEach-Object { "$($_.metric.alertname)/$($_.metric.alertstate)" }) -join ', ')]"
"after: $(Get-State)"
