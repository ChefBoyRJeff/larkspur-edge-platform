<#
.SYNOPSIS
Creates (or replaces) the backup credential for one environment and seals it for the cluster.
The credential is a SAS token for that environment's container only, bound to a new stored access
policy named barman-<UTC timestamp> (read, add, create, write, delete, list) that expires after a
year. After the new token is sealed, every other policy on the container is deleted, which revokes
every older token (run it again at once if a token is ever exposed). The token is tested against
the container, sealed into
platform/config/databases/larkspur-db-<environment>-backup-credentials.sealedsecret.yaml,
and never printed or written to the repository. See docs/runbooks/backups.md.
#>
param(
    [Parameter(Mandatory)][ValidateSet('dev', 'prod')][string]$Environment
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$group = 'rg-lark-edge-backup'
$container = "larkspur-backups-$Environment"
$namespace = "larkspur-$Environment"
$target = Join-Path $repo "platform/config/databases/larkspur-db-$Environment-backup-credentials.sealedsecret.yaml"
$kubeseal = (Get-Command kubeseal -ErrorAction SilentlyContinue).Source
if (-not $kubeseal) { $kubeseal = Join-Path $env:LOCALAPPDATA 'Programs\kubeseal\kubeseal.exe' }
if (-not (Test-Path $kubeseal)) { throw 'kubeseal was not found' }
$controller = @('--controller-name', 'sealed-secrets-controller', '--controller-namespace', 'kube-system')

$account = az storage account list -g $group --query '[0].name' -o tsv
if (-not $account) { throw "no storage account in $group; run azure/backup/deploy.ps1 first" }
$key = az storage account keys list -g $group -n $account --query '[0].value' -o tsv
$auth = @('--account-name', $account, '--account-key', $key)
$expiry = (Get-Date).ToUniversalTime().AddYears(1).ToString('yyyy-MM-ddTHH:mmZ')

$policy = "barman-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmm'))"
az storage container policy create --container-name $container --name $policy --permissions racwdl --expiry $expiry @auth -o none --only-show-errors
if ($LASTEXITCODE) { throw 'could not create the stored access policy' }
$sas = (az storage container generate-sas --name $container --policy-name $policy --https-only @auth -o tsv --only-show-errors) -join ''
if (-not $sas -or $sas -notmatch "si=$policy") { throw 'the SAS token was not issued from the new policy; nothing sealed' }
# Field names only (never values), to diagnose a token Azure rejects.
"SAS token fields: $((($sas -split '&') | ForEach-Object { ($_ -split '=')[0] }) -join ', ')"

# Prove the token works on its own container (write, list, delete) and not on the other one.
# These calls go straight to the Blob REST API: passing the token to az (a .cmd file on Windows)
# would let cmd.exe split it at every "&". Errors never include the token.
$other = if ($Environment -eq 'dev') { 'larkspur-backups-prod' } else { 'larkspur-backups-dev' }
$base = "https://$account.blob.core.windows.net"
function Invoke-Sas([string]$Method, [string]$Path, [string]$Query, [hashtable]$Headers = @{}, [string]$Body) {
    $h = @{ 'x-ms-version' = '2023-11-03' } + $Headers
    $uri = "$base/$Path`?$(if ($Query) { "$Query&" })$sas"
    try {
        if ($Body) { Invoke-WebRequest -Method $Method -Uri $uri -Headers $h -Body $Body -SkipHttpErrorCheck }
        else { Invoke-WebRequest -Method $Method -Uri $uri -Headers $h -SkipHttpErrorCheck }
    }
    catch { throw "request $Method /$Path failed: $($_.Exception.Message.Replace($sas, '<sas>'))" }
}
$put = Invoke-Sas Put "$container/credential-check.txt" '' @{ 'x-ms-blob-type' = 'BlockBlob' } 'credential check'
if ($put.StatusCode -ne 201) { throw "the SAS token cannot write to its container (HTTP $($put.StatusCode)); nothing sealed" }
$list = Invoke-Sas Get $container 'restype=container&comp=list&prefix=credential-check'
$del = Invoke-Sas Delete "$container/credential-check.txt"
if ($list.StatusCode -ne 200 -or $list.Content -notmatch '<Name>credential-check.txt</Name>') { throw "the SAS token cannot list its container (HTTP $($list.StatusCode)); nothing sealed" }
if ($del.StatusCode -ne 202) { throw "the SAS token cannot delete in its container (HTTP $($del.StatusCode)); nothing sealed" }
$cross = Invoke-Sas Get $other 'restype=container&comp=list'
if ($cross.StatusCode -ne 403) { throw "the SAS token can read $other (HTTP $($cross.StatusCode)); nothing sealed" }

$dir = New-Item -ItemType Directory (Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid()))
try {
    [IO.File]::WriteAllText((Join-Path $dir.FullName 'AZURE_STORAGE_ACCOUNT'), $account)
    [IO.File]::WriteAllText((Join-Path $dir.FullName 'AZURE_STORAGE_SAS_TOKEN'), $sas)
    kubectl create secret generic larkspur-db-backup-credentials -n $namespace --from-file=$($dir.FullName) --dry-run=client -o json |
        & $kubeseal --format yaml @controller | Out-File -Encoding utf8 $target
}
finally { Remove-Item -Recurse -Force $dir.FullName; Remove-Variable sas }
Get-Content $target | & $kubeseal --validate @controller
if ($LASTEXITCODE) { throw 'the cluster cannot decrypt the sealed file' }

# Revoke every older token for this container by deleting the other policies.
$raw = az storage container policy list --container-name $container @auth -o json --only-show-errors | ConvertFrom-Json
$names = if ($raw -is [array]) { $raw | ForEach-Object { $_.id ?? $_.name } } else { $raw.PSObject.Properties.Name }
foreach ($old in $names | Where-Object { $_ -ne $policy }) {
    az storage container policy delete --container-name $container --name $old @auth -o none --only-show-errors
    if ($LASTEXITCODE) { throw "could not delete the old policy $old" }
    "Revoked tokens from policy $old"
}
Remove-Variable key, auth
"Sealed $Environment credential (policy $policy) for $account/$container; it expires $expiry UTC. Commit $([IO.Path]::GetRelativePath($repo, $target))."
