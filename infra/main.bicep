@description('Azure region')
param location string = resourceGroup().location

@description('Name prefix for Azure resources')
param prefix string = 'bluepaper'

@secure()
@description('Bearer API key for the BluePaper HTTP API')
param apiKey string

@description('API container image')
param apiImage string

@description('Worker container image')
param workerImage string

@description('Assign storage data-plane roles to the API and worker. Requires permission to assign Blob/Queue/Table Data Contributor.')
param assignRoles bool = false

@description('Create the preview sandbox group via ARM. If this fails, use `aca sandboxgroup create` instead.')
param deploySandboxGroup bool = false

var storageName = toLower(take('${prefix}st${uniqueString(resourceGroup().id)}', 24))
var envName = '${prefix}-env'
var apiName = '${prefix}-api'
var workerName = '${prefix}-worker'
var sandboxGroupName = '${prefix}-sandboxes'
var workspaceName = '${prefix}-logs'
var acrServer = split(apiImage, '/')[0]

var blobContributor = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)
var queueContributor = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
)
var tableContributor = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
)

resource workspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: workspaceName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'bluepaper'
}

resource queueServices 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource conversionsQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueServices
  name: 'conversions'
}

resource tableServices 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource conversionsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableServices
  name: 'conversions'
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}

resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: apiName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
      }
      secrets: [
        { name: 'api-key', value: apiKey }
      ]
      registries: [
        {
          server: acrServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          env: [
            { name: 'BLUEPAPER_API_KEY', secretRef: 'api-key' }
            { name: 'BLUEPAPER_STORAGE_BACKEND', value: 'azure' }
            { name: 'BLUEPAPER_AZURE_STORAGE_ACCOUNT', value: storageAccount.name }
            { name: 'PORT', value: '8080' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8080 }
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
      }
    }
  }
}

resource workerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: workerName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      secrets: [
        { name: 'api-key', value: apiKey }
      ]
      registries: [
        {
          server: acrServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: workerImage
          env: [
            { name: 'BLUEPAPER_API_KEY', secretRef: 'api-key' }
            { name: 'BLUEPAPER_STORAGE_BACKEND', value: 'azure' }
            { name: 'BLUEPAPER_ISOLATION', value: 'aca' }
            { name: 'BLUEPAPER_AZURE_STORAGE_ACCOUNT', value: storageAccount.name }
            { name: 'BLUEPAPER_AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
            { name: 'BLUEPAPER_AZURE_RESOURCE_GROUP', value: resourceGroup().name }
            { name: 'BLUEPAPER_AZURE_REGION', value: location }
            { name: 'BLUEPAPER_SANDBOX_GROUP', value: sandboxGroupName }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 8
        rules: [
          {
            name: 'queue'
            custom: {
              type: 'azure-queue'
              metadata: {
                accountName: storageAccount.name
                queueName: 'conversions'
                queueLength: '1'
              }
            }
          }
        ]
      }
    }
  }
}

// Preview ARM type. If deploy fails, create the group with `aca sandboxgroup create`.
resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-02-01-preview' = if (deploySandboxGroup) {
  name: sandboxGroupName
  location: location
}

resource apiBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (assignRoles) {
  name: guid(storageAccount.id, apiApp.id, 'blob')
  scope: storageAccount
  properties: {
    roleDefinitionId: blobContributor
    principalId: apiApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource apiQueueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (assignRoles) {
  name: guid(storageAccount.id, apiApp.id, 'queue')
  scope: storageAccount
  properties: {
    roleDefinitionId: queueContributor
    principalId: apiApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource apiTableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (assignRoles) {
  name: guid(storageAccount.id, apiApp.id, 'table')
  scope: storageAccount
  properties: {
    roleDefinitionId: tableContributor
    principalId: apiApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource workerBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (assignRoles) {
  name: guid(storageAccount.id, workerApp.id, 'blob')
  scope: storageAccount
  properties: {
    roleDefinitionId: blobContributor
    principalId: workerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource workerQueueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (assignRoles) {
  name: guid(storageAccount.id, workerApp.id, 'queue')
  scope: storageAccount
  properties: {
    roleDefinitionId: queueContributor
    principalId: workerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource workerTableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (assignRoles) {
  name: guid(storageAccount.id, workerApp.id, 'table')
  scope: storageAccount
  properties: {
    roleDefinitionId: tableContributor
    principalId: workerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output storageAccountName string = storageAccount.name
output apiFqdn string = apiApp.properties.configuration.ingress.fqdn
output sandboxGroupName string = sandboxGroupName
output workerPrincipalId string = workerApp.identity.principalId
