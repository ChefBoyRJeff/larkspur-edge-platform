// Off-site backups for the Larkspur Track databases (Data module, ADR-0008).
// A resource group with one storage account (a private container per environment) and a
// monthly budget filtered to that group. Deploy with azure/backup/deploy.ps1.
targetScope = 'subscription'

@description('Azure region for the resource group and the storage account')
param location string

@description('Tags for the resource group and every resource in it')
param tags object

@description('Monthly budget in USD for the resource group')
@minValue(1)
param budgetAmount int = 5

@description('First day of the budget period (must be the first of a month)')
param budgetStartDate string = '2026-11-01T00:00:00Z'

@description('Email for budget alerts; from the gitignored main.local.bicepparam')
@secure()
param alertEmail string

var resourceGroupName = 'rg-lark-edge-backup'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module storage 'modules/storage.bicep' = {
  name: 'lark-edge-backup-storage'
  scope: rg
  params: {
    location: location
    tags: tags
    containerNames: [
      'larkspur-backups-dev'
      'larkspur-backups-prod'
    ]
  }
}

// Fixed dates, not utcNow(): what-if can't evaluate utcNow(), and a later deployment would try to
// move an existing budget's start date. The end date is Azure's default (start + 10 years).
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'budget-larkspur-edge-backup'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
      endDate: dateTimeAdd(budgetStartDate, 'P10Y')
    }
    filter: {
      dimensions: {
        name: 'ResourceGroupName'
        operator: 'In'
        values: [
          resourceGroupName
        ]
      }
    }
    notifications: {
      Actual_50: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        thresholdType: 'Actual'
        contactEmails: [
          alertEmail
        ]
      }
      Actual_100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: [
          alertEmail
        ]
      }
      Forecasted_100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: [
          alertEmail
        ]
      }
    }
  }
  dependsOn: [
    rg
  ]
}

output resourceGroupName string = rg.name
output storageAccountName string = storage.outputs.storageAccountName
output blobEndpoint string = storage.outputs.blobEndpoint
output containerNames array = storage.outputs.containerNames
