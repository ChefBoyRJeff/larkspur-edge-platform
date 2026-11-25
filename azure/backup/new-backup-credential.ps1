<#
.SYNOPSIS
Creates (or replaces) the backup credential for one environment and seals it for the cluster.
The credential is a SAS token for that environment's container only, bound to a stored access
policy named "barman" (read, add, create, write, delete, list) that expires after a year.
Deleting or replacing the policy revokes every SAS issued from it. The token is tested against
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

$existing = az storage container policy list --container-name $container @auth -o json | ConvertFrom-Json
if ($existing.PSObject.Properties.Name -contains 'barman') {
    az storage container policy update --container-name $container --name barman --permissions racwdl --expiry $expiry @auth -o none
} else {
    az storage container policy create --container-name $container --name barman --permissions racwdl --expiry $expiry @auth -o none
}
if ($LASTEXITCODE) { throw 'could not set the stored access policy' }
$sas = az storage container generate-sas --name $container --policy-name barman --https-only @auth -o tsv
Remove-Variable key, auth
if (-not $sas -or $sas -notmatch 'si=barman') { throw 'the SAS token was not issued from the barman policy; nothing sealed' }

# Prove the token works on its own container (write, list, delete) and not on the other one.
$other = if ($Environment -eq 'dev') { 'larkspur-backups-prod' } else { 'larkspur-backups-dev' }
$probe = New-TemporaryFile
try {
    Set-Content -NoNewline $probe.FullName 'credential check'
    az storage blob upload --account-name $account --container-name $container --name credential-check.txt --file $probe.FullName --sas-token $sas --overwrite -o none --only-show-errors
    if ($LASTEXITCODE) { throw 'the SAS token cannot write to its container; nothing sealed' }
    $listed = az storage blob list --account-name $account --container-name $container --prefix credential-check --sas-token $sas --query '[].name' -o tsv --only-show-errors
    az storage blob delete --account-name $account --container-name $container --name credential-check.txt --sas-token $sas -o none --only-show-errors
    if ($listed -ne 'credential-check.txt' -or $LASTEXITCODE) { throw 'the SAS token cannot list or delete in its container; nothing sealed' }
    az storage blob list --account-name $account --container-name $other --sas-token $sas -o none --only-show-errors 2>$null
    if (-not $LASTEXITCODE) { throw "the SAS token can read $other; nothing sealed" }
}
finally { Remove-Item $probe.FullName -Force }

$dir = New-Item -ItemType Directory (Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid()))
try {
    Set-Content -NoNewline (Join-Path $dir 'AZURE_STORAGE_ACCOUNT') $account
    Set-Content -NoNewline (Join-Path $dir 'AZURE_STORAGE_SAS_TOKEN') $sas
    kubectl create secret generic larkspur-db-backup-credentials -n $namespace --from-file=$dir --dry-run=client -o json |
        & $kubeseal --format yaml @controller | Out-File -Encoding utf8 $target
}
finally { Remove-Item -Recurse -Force $dir; Remove-Variable sas }
Get-Content $target | & $kubeseal --validate @controller
if ($LASTEXITCODE) { throw 'the cluster cannot decrypt the sealed file' }
"Sealed $Environment credential for $account/$container; it expires $expiry UTC. Commit $([IO.Path]::GetRelativePath($repo, $target))."
