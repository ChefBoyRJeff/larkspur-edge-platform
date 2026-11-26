<#
.SYNOPSIS
Creates (or replaces) the sync worker's credential for one environment and seals it for the
cluster. The credential is a SAS token that can only ADD messages to that environment's queue,
bound to a new stored access policy named sync-<UTC timestamp> that expires after a year. After
the new token is sealed, the queue's other policies are deleted, which revokes every older token
(run it again at once if a token is ever exposed). The token is tested (it can add to its own
queue, and cannot read it or add to the other queue), never printed, never passed on a command
line, and sealed into
platform/config/sync/larkspur-track-sync-<environment>-credentials.sealedsecret.yaml.
See docs/runbooks/sync.md.
#>
param(
    [Parameter(Mandatory)][ValidateSet('dev', 'prod')][string]$Environment
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$group = 'rg-lark-edge-integration'
$queue = "larkspur-events-$Environment"
$other = if ($Environment -eq 'dev') { 'larkspur-events-prod' } else { 'larkspur-events-dev' }
$namespace = "larkspur-$Environment"
$target = Join-Path $repo "platform/config/sync/larkspur-track-sync-$Environment-credentials.sealedsecret.yaml"
$kubeseal = (Get-Command kubeseal -ErrorAction SilentlyContinue).Source
if (-not $kubeseal) { $kubeseal = Join-Path $env:LOCALAPPDATA 'Programs\kubeseal\kubeseal.exe' }
if (-not (Test-Path $kubeseal)) { throw 'kubeseal was not found' }
$controller = @('--controller-name', 'sealed-secrets-controller', '--controller-namespace', 'kube-system')

$account = az storage account list -g $group --query '[0].name' -o tsv
if (-not $account) { throw "no storage account in $group; run azure/integration/deploy.ps1 first" }
$key = az storage account keys list -g $group -n $account --query '[0].value' -o tsv
$auth = @('--account-name', $account, '--account-key', $key)
$expiry = (Get-Date).ToUniversalTime().AddYears(1).ToString('yyyy-MM-ddTHH:mmZ')

$policy = "sync-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmm'))"
az storage queue policy create --queue-name $queue --name $policy --permissions a --expiry $expiry @auth -o none --only-show-errors
if ($LASTEXITCODE) { throw 'could not create the stored access policy' }
$sas = (az storage queue generate-sas --name $queue --policy-name $policy --https-only @auth -o tsv --only-show-errors) -join ''
if (-not $sas -or $sas -notmatch "si=$policy") { throw 'the SAS token was not issued from the new policy; nothing sealed' }
"SAS token fields: $((($sas -split '&') | ForEach-Object { ($_ -split '=')[0] }) -join ', ')"

# Test over the Queue REST API (never through az, which would let cmd.exe split the token at "&").
# The test message expires after 1 second, so it never reaches a consumer.
$base = "https://$account.queue.core.windows.net"
function Invoke-Sas([string]$Method, [string]$Path, [string]$Query, [string]$Body) {
    $uri = "$base/$Path`?$(if ($Query) { "$Query&" })$sas"
    try {
        $p = @{ Method = $Method; Uri = $uri; Headers = @{ 'x-ms-version' = '2023-11-03' }; SkipHttpErrorCheck = $true }
        if ($Body) { $p.Body = $Body; $p.ContentType = 'application/xml' }
        Invoke-WebRequest @p
    }
    catch { throw "request $Method /$Path failed: $($_.Exception.Message.Replace($sas, '<sas>'))" }
}
$message = '<QueueMessage><MessageText>Y3JlZGVudGlhbCBjaGVjaw==</MessageText></QueueMessage>'
$add = Invoke-Sas Post "$queue/messages" 'messagettl=1' $message
if ($add.StatusCode -ne 201) { throw "the SAS token cannot add to its queue (HTTP $($add.StatusCode)); nothing sealed" }
$read = Invoke-Sas Get "$queue/messages" 'peekonly=true'
if ($read.StatusCode -ne 403) { throw "the SAS token can read its queue (HTTP $($read.StatusCode)); it must be send-only; nothing sealed" }
$cross = Invoke-Sas Post "$other/messages" 'messagettl=1' $message
if ($cross.StatusCode -ne 403) { throw "the SAS token can add to $other (HTTP $($cross.StatusCode)); nothing sealed" }

$dir = New-Item -ItemType Directory (Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid()))
try {
    [IO.File]::WriteAllText((Join-Path $dir.FullName 'AZURE_QUEUE_URL'), "$base/$queue")
    [IO.File]::WriteAllText((Join-Path $dir.FullName 'AZURE_QUEUE_SAS'), $sas)
    New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
    kubectl create secret generic larkspur-track-sync-credentials -n $namespace --from-file=$($dir.FullName) --dry-run=client -o json |
        & $kubeseal --format yaml @controller | Out-File -Encoding utf8 $target
}
finally { Remove-Item -Recurse -Force $dir.FullName; Remove-Variable sas }
Get-Content $target | & $kubeseal --validate @controller
if ($LASTEXITCODE) { throw 'the cluster cannot decrypt the sealed file' }

$raw = az storage queue policy list --queue-name $queue @auth -o json --only-show-errors | ConvertFrom-Json
$names = if ($raw -is [array]) { $raw | ForEach-Object { $_.id ?? $_.name } } else { $raw.PSObject.Properties.Name }
foreach ($old in $names | Where-Object { $_ -ne $policy }) {
    az storage queue policy delete --queue-name $queue --name $old @auth -o none --only-show-errors
    if ($LASTEXITCODE) { throw "could not delete the old policy $old" }
    "Revoked tokens from policy $old"
}
Remove-Variable key, auth
"Sealed $Environment sync credential (policy $policy, add only) for $account/$queue; it expires $expiry UTC. Commit $([IO.Path]::GetRelativePath($repo, $target))."
