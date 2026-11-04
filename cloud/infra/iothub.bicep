@description('Name of the IoT Hub')
param iotHubName string

@description('Azure region')
param location string

@description('SKU - S1 minimum, required for direct methods and message routing')
param skuName string = 'S1'

@description('Number of IoT Hub units (scaled up by the remediation function on throttling)')
param skuCapacity int = 1

resource iotHub 'Microsoft.Devices/IotHubs@2023-06-30' = {
  name: iotHubName
  location: location
  sku: {
    name: skuName
    capacity: skuCapacity
  }
  properties: {
    eventHubEndpoints: {
      events: {
        retentionTimeInDays: 1
        partitionCount: 4
      }
    }
    cloudToDevice: {
      maxDeliveryCount: 10
      defaultTtlAsIso8601: 'PT1H'
    }
  }
}

// Device identities for the ESP32 fleet are created out-of-band, one per board:
//   az iot hub device-identity create --hub-name <hub> --device-id esp32-01
// (Bicep does not manage individual device identities.)

output iotHubName string = iotHub.name
output iotHubHostName string = iotHub.properties.hostName
output eventHubCompatibleEndpoint string = iotHub.properties.eventHubEndpoints.events.endpoint
output eventHubCompatiblePath string = iotHub.properties.eventHubEndpoints.events.path
output iotHubId string = iotHub.id
