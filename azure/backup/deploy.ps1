<#
.SYNOPSIS
Deploys the backup storage and its budget (azure/backup/main.bicep) to the signed-in subscription:
lint, validate (which checks Azure Policy), what-if, a confirmation, deploy, then a second what-if
that should report no changes. Needs azure/backup/main.local.bicepparam (copy of the example file
with your email address). See docs/runbooks/backups.md.
#>
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$params = Join-Path $here 'main.local.bicepparam'
if (-not (Test-Path $params)) { throw "Copy main.example.bicepparam to main.local.bicepparam and set alertEmail first" }
$common = @('--location', 'eastus', '--template-file', (Join-Path $here 'main.bicep'), '--parameters', $params, '--name', 'lark-edge-backup')

az bicep lint --file (Join-Path $here 'main.bicep')
if ($LASTEXITCODE) { throw 'lint failed' }
az deployment sub validate @common -o none
if ($LASTEXITCODE) { throw 'validation failed (see the error above; RequestDisallowedByPolicy names the policy)' }
az deployment sub what-if @common
if ($LASTEXITCODE) { throw 'what-if failed' }
if ((Read-Host 'Deploy these changes? Type yes to continue') -ne 'yes') { 'Nothing deployed.'; return }
$result = az deployment sub create @common -o json | ConvertFrom-Json
if ($LASTEXITCODE -or $result.properties.provisioningState -ne 'Succeeded') { throw 'deployment failed' }
$result.properties.outputs | ConvertTo-Json -Depth 4
'--- second what-if (expect no changes) ---'
az deployment sub what-if @common --result-format ResourceIdOnly
