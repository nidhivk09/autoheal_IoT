@description('Storage account for firmware images (globally unique, 3-24 lowercase alphanumerics)')
param storageAccountName string

@description('Azure region')
param location string

@description('Blob container holding OTA images')
param containerName string = 'firmware'

// Private: devices download images through short-lived read-only SAS URLs
// generated per OTA call (controller/firmware_store.py), never with keys.
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storage
  name: 'default'
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

output storageAccountName string = storage.name
output containerName string = container.name
