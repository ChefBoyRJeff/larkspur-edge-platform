// Copy to main.local.bicepparam (gitignored) and put your email address in alertEmail.
using 'main.bicep'

param location = 'eastus'
param tags = {
  company: 'Larkspur Logistics (fictional)'
  costCenter: 'CC-LARK-01'
  environment: 'prod'
  owner: 'platform'
  project: 'larkspur-edge-platform'
}
param alertEmail = 'you@example.com'
