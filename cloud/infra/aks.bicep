@description('Name of the AKS cluster')
param clusterName string

@description('Azure region')
param location string

@description('DNS prefix for the cluster')
param dnsPrefix string = clusterName

@description('Node VM size - small/cheap, this only hosts the ingestion service')
param nodeVmSize string = 'Standard_B2s'

@description('Node count')
param nodeCount int = 2

@description('Log Analytics workspace resource id, for Container Insights')
param logAnalyticsWorkspaceId string

resource aks 'Microsoft.ContainerService/managedClusters@2024-02-01' = {
  name: clusterName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    dnsPrefix: dnsPrefix
    agentPoolProfiles: [
      {
        name: 'ingestpool'
        count: nodeCount
        vmSize: nodeVmSize
        osType: 'Linux'
        mode: 'System'
        type: 'VirtualMachineScaleSets'
      }
    ]
    addonProfiles: {
      omsagent: {
        enabled: true
        config: {
          logAnalyticsWorkspaceResourceID: logAnalyticsWorkspaceId
        }
      }
    }
  }
}

output clusterName string = aks.name
output kubeletIdentityObjectId string = aks.properties.identityProfile.kubeletidentity.objectId
output clusterId string = aks.id
