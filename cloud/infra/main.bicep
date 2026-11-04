@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Short project prefix used to derive resource names')
param namePrefix string = 'selfheal'

@description('PostgreSQL administrator login')
param postgresAdminLogin string = 'autoheal_admin'

@secure()
@description('PostgreSQL administrator password')
param postgresAdminPassword string

@description('Webhook URL for the remediation Function - leave blank on first deploy, redeploy monitor once the Function exists')
param remediationFunctionWebhookUrl string = ''

var iotHubName = '${namePrefix}-iothub'
var postgresServerName = '${namePrefix}-pg'
var aksClusterName = '${namePrefix}-aks'
var workspaceName = '${namePrefix}-logs'
// storage account names: 3-24 lowercase alphanumerics, globally unique
var firmwareStorageName = toLower(take('${replace(namePrefix, '-', '')}fw${uniqueString(resourceGroup().id)}', 24))

module monitor 'monitor.bicep' = {
  name: 'deploy-monitor'
  params: {
    workspaceName: workspaceName
    location: location
    iotHubId: iotHub.outputs.iotHubId
    remediationFunctionWebhookUrl: remediationFunctionWebhookUrl
  }
}

module iotHub 'iothub.bicep' = {
  name: 'deploy-iothub'
  params: {
    iotHubName: iotHubName
    location: location
  }
}

module postgres 'postgres.bicep' = {
  name: 'deploy-postgres'
  params: {
    serverName: postgresServerName
    location: location
    administratorLogin: postgresAdminLogin
    administratorPassword: postgresAdminPassword
  }
}

module firmwareStorage 'storage.bicep' = {
  name: 'deploy-firmware-storage'
  params: {
    storageAccountName: firmwareStorageName
    location: location
  }
}

module aks 'aks.bicep' = {
  name: 'deploy-aks'
  params: {
    clusterName: aksClusterName
    location: location
    logAnalyticsWorkspaceId: monitor.outputs.workspaceId
  }
}

output iotHubName string = iotHub.outputs.iotHubName
output iotHubHostName string = iotHub.outputs.iotHubHostName
output eventHubCompatibleEndpoint string = iotHub.outputs.eventHubCompatibleEndpoint
output postgresServerFqdn string = postgres.outputs.fullyQualifiedDomainName
output postgresDatabaseName string = postgres.outputs.databaseName
output aksClusterName string = aks.outputs.clusterName
output firmwareStorageAccountName string = firmwareStorage.outputs.storageAccountName
output firmwareContainerName string = firmwareStorage.outputs.containerName
output logAnalyticsWorkspaceId string = monitor.outputs.workspaceId
