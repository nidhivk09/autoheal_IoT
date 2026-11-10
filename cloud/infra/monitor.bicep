@description('Name of the Log Analytics workspace')
param workspaceName string

@description('Azure region')
param location string

@description('Resource id of the IoT Hub to monitor')
param iotHubId string

@description('Webhook URL for the remediation Azure Function (populated after the Function is deployed - leave blank on first deploy)')
param remediationFunctionWebhookUrl string = ''

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// diagnosticSettings needs a resource reference (not an id string) as scope
resource iotHub 'Microsoft.Devices/IotHubs@2023-06-30' existing = {
  name: last(split(iotHubId, '/'))
}

resource iotHubDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'iothub-to-loganalytics'
  scope: iotHub
  properties: {
    workspaceId: workspace.id
    logs: [
      { category: 'Connections', enabled: true }
      { category: 'DeviceTelemetry', enabled: true }
      { category: 'C2DCommands', enabled: true }
      { category: 'DirectMethods', enabled: true }
      { category: 'Configurations', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(remediationFunctionWebhookUrl)) {
  name: 'ag-remediation-dispatcher'
  location: 'global'
  properties: {
    groupShortName: 'autoheal'
    enabled: true
    webhookReceivers: [
      {
        name: 'remediation-dispatcher'
        serviceUri: remediationFunctionWebhookUrl
        useCommonAlertSchema: true
      }
    ]
  }
}

// Per-device failures (no telemetry, low heap, floods, config drift) are
// detected by the ingestion service, which knows which device each message
// came from - IoT Hub metrics don't, so a metric alert can't tell devices in
// a fleet apart. Only hub-level conditions are alerted on here.

// IoT Hub throttling: fires when throttle errors are observed at all.
resource throttlingAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (!empty(remediationFunctionWebhookUrl)) {
  name: 'alert-iothub-throttling'
  location: 'global'
  properties: {
    severity: 2
    enabled: true
    scopes: [iotHubId]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'throttle-errors'
          metricName: 'd2c.telemetry.ingress.sendThrottleErrors'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      { actionGroupId: actionGroup.id }
    ]
  }
}

// AKS pod crash-loop alerting is provided by the Container Insights addon
// (enabled in aks.bicep) via its built-in "Container restarting" recommended
// alert rule - enable that from the Portal or `az monitor alert-processing-rule`
// once the cluster exists, pointing at the same action group above.

output workspaceId string = workspace.id
output workspaceCustomerId string = workspace.properties.customerId
