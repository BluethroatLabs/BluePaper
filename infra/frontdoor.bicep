@description('Public hostname of the BluePaper API container')
param originHostName string

var profileName = 'bluepaper-fd'
var endpointName = 'bluepaper-wehi'

resource frontDoor 'Microsoft.Cdn/profiles@2024-02-01' = {
  name: profileName
  location: 'global'
  sku: {
    name: 'Standard_AzureFrontDoor'
  }
}

resource frontDoorEndpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-02-01' = {
  parent: frontDoor
  name: endpointName
  location: 'global'
  properties: {
    enabledState: 'Enabled'
  }
}

resource apiOriginGroup 'Microsoft.Cdn/profiles/originGroups@2024-02-01' = {
  parent: frontDoor
  name: 'api'
  properties: {
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
    }
    healthProbeSettings: {
      probePath: '/healthz'
      probeRequestType: 'GET'
      probeProtocol: 'Https'
      probeIntervalInSeconds: 60
    }
  }
}

resource apiOrigin 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: apiOriginGroup
  name: 'api'
  properties: {
    hostName: originHostName
    httpPort: 80
    httpsPort: 443
    originHostHeader: originHostName
    priority: 1
    weight: 1000
    enabledState: 'Enabled'
    enforceCertificateNameCheck: true
  }
}

// Created outside this file. A route write replaces customDomains, so a deploy
// that omits this association unbinds the hostname and Front Door serves the
// *.azureedge.net certificate instead.
resource bluepaperDomain 'Microsoft.Cdn/profiles/customDomains@2024-02-01' existing = {
  parent: frontDoor
  name: 'bluepaper'
}

resource apiRoute 'Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01' = {
  parent: frontDoorEndpoint
  name: 'default'
  dependsOn: [
    apiOrigin
  ]
  properties: {
    originGroup: {
      id: apiOriginGroup.id
    }
    customDomains: [
      {
        id: bluepaperDomain.id
      }
    ]
    supportedProtocols: [
      'Http'
      'Https'
    ]
    patternsToMatch: [
      '/*'
    ]
    forwardingProtocol: 'HttpsOnly'
    linkToDefaultDomain: 'Enabled'
    httpsRedirect: 'Enabled'
    enabledState: 'Enabled'
  }
}

output frontDoorHostName string = frontDoorEndpoint.properties.hostName
