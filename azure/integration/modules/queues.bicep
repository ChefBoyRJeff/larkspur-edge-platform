// Storage account for the sync queues. One queue per environment; each environment's edge
// credential (new-sync-credential.ps1) may only add messages to its own queue.
targetScope = 'resourceGroup'

@description('Azure region')
param location string

@description('Tags for every resource')
param tags object

@description('One queue per environment')
param queueNames array

// Globally unique, stable for this resource group: stlarkint + 13 characters.
var storageAccountName = 'stlarkint${uniqueString(resourceGroup().id)}'

resource account 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // A service SAS is signed with an account key; each SAS is limited to one queue and one
    // permission by its stored access policy.
    allowSharedKeyAccess: true
    allowCrossTenantReplication: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      // The site reaches Azure over the internet from a changing home IP address.
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2024-01-01' = {
  parent: account
  name: 'default'
}

resource queues 'Microsoft.Storage/storageAccounts/queueServices/queues@2024-01-01' = [
  for name in queueNames: {
    parent: queueService
    name: name
  }
]

output storageAccountName string = account.name
output queueEndpoint string = account.properties.primaryEndpoints.queue
output queueNames array = [for (name, i) in queueNames: queues[i].name]
