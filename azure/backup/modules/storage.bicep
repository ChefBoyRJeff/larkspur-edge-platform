// Storage account for database backups. One private container per environment, so each
// environment's credential (a SAS bound to its container's stored access policy, created by
// new-backup-credential.ps1) cannot read or delete the other environment's backups.
targetScope = 'resourceGroup'

@description('Azure region')
param location string

@description('Tags for every resource')
param tags object

@description('One private container per environment')
param containerNames array

// Globally unique, stable for this resource group: stlarkbkp + 13 characters.
var storageAccountName = 'stlarkbkp${uniqueString(resourceGroup().id)}'

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
    // Shared key stays on: a service SAS is signed with an account key. The SAS is limited to one
    // container by its stored access policy and can be revoked by deleting that policy.
    allowSharedKeyAccess: true
    allowCrossTenantReplication: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      // The site reaches Azure over the internet from a changing home IP address.
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    encryption: {
      keySource: 'Microsoft.Storage'
      services: {
        blob: {
          enabled: true
          keyType: 'Account'
        }
      }
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: account
  name: 'default'
  properties: {
    // Deleted or overwritten backups can be recovered for 7 days (ransomware or a bad retention run).
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = [
  for name in containerNames: {
    parent: blobService
    name: name
    properties: {
      publicAccess: 'None'
    }
  }
]

output storageAccountName string = account.name
output blobEndpoint string = account.properties.primaryEndpoints.blob
output containerNames array = [for (name, i) in containerNames: containers[i].name]
