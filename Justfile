# BluePaper Azure deploy
#
# Target:
#   /subscriptions/1e238434-310b-4bcd-ab1f-a9381d170243/resourceGroups/rg-bluepaper-wehi-sandbox
#
# Prereqs: `az login`, Podman, `BLUEPAPER_API_KEY` (env or `.env`).
# Images go to ACR. Pass `acr=myregistry.azurecr.io` or set `ACR` if the RG has none.
#
#   just              # this list
#   just use          # select the subscription
#   just deploy       # build, push, bicep, worker sandbox role
#   just openapi      # write docs/openapi.json
#   just disk         # bake the Dangerzone conversion disk (`aca` CLI)
#   just smoke        # GET /healthz and /openapi.json

set dotenv-load := true
set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

subscription := "1e238434-310b-4bcd-ab1f-a9381d170243"
rg := "rg-bluepaper-wehi-sandbox"
edge_rg := "rg-blueskills-wehi-aci-sandbox"
api_name := "bluepaper-api"
worker_name := "bluepaper-worker"
front_door := "bluepaper-fd"
front_door_endpoint := "bluepaper-wehi"
sandbox_group := "bluepaper-sandboxes"
dangerzone_image := "ghcr.io/freedomofpress/dangerzone/v1:latest"
tag := env("TAG", "latest")
acr := env("ACR", "bsspike08250621.azurecr.io")

export AZURE_SUBSCRIPTION_ID := subscription
export BLUEPAPER_AZURE_SUBSCRIPTION_ID := subscription
export BLUEPAPER_AZURE_RESOURCE_GROUP := rg
export BLUEPAPER_SANDBOX_GROUP := sandbox_group

# Show the deploy target and recipes.
default:
    @echo "BluePaper → /subscriptions/{{ subscription }}/resourceGroups/{{ rg }}"
    @echo
    @just --list

# Select the BluePaper sandbox subscription and show the resource group.
use:
    az account set --subscription "{{ subscription }}"
    az group show --name "{{ rg }}" --output table

# Build linux/amd64 API and worker images and push them to ACR.
build:
    #!/usr/bin/env bash
    set -euo pipefail
    az account set --subscription "{{ subscription }}"
    acr="$(just acr='{{ acr }}' _acr)"
    token="$(az acr login --name "${acr%%.*}" --expose-token --output tsv --query accessToken)"
    printf '%s\n' "$token" | podman login "$acr" \
      --username 00000000-0000-0000-0000-000000000000 \
      --password-stdin
    podman build --platform linux/amd64 -f Dockerfile.api \
      -t "$acr/{{ api_name }}:{{ tag }}" .
    podman build --platform linux/amd64 -f Dockerfile.worker \
      -t "$acr/{{ worker_name }}:{{ tag }}" .
    podman push "$acr/{{ api_name }}:{{ tag }}"
    podman push "$acr/{{ worker_name }}:{{ tag }}"

# Deploy Container Apps, storage, and (if the preview type works) the sandbox group.
infra:
    #!/usr/bin/env bash
    set -euo pipefail
    : "${BLUEPAPER_API_KEY:?Set BLUEPAPER_API_KEY in the environment or .env}"
    : "${TURNSTILE_SECRET:?Set TURNSTILE_SECRET in the environment or .env}"
    az account set --subscription "{{ subscription }}"
    acr="$(just acr='{{ acr }}' _acr)"
    turnstile_host="$(az afd endpoint show \
      --subscription "{{ subscription }}" \
      --resource-group "{{ edge_rg }}" \
      --profile-name "{{ front_door }}" \
      --endpoint-name "{{ front_door_endpoint }}" \
      --query hostName -o tsv 2>/dev/null || true)"
    if [[ -z "$turnstile_host" ]]; then
      turnstile_host="$(az containerapp show \
        --subscription "{{ subscription }}" \
        --resource-group "{{ rg }}" \
        --name "{{ api_name }}" \
        --query properties.configuration.ingress.fqdn -o tsv)"
    fi
    az deployment group create \
      --subscription "{{ subscription }}" \
      --resource-group "{{ rg }}" \
      --template-file infra/main.bicep \
      --parameters infra/parameters.json \
      --parameters \
        apiKey="$BLUEPAPER_API_KEY" \
        turnstileSecret="$TURNSTILE_SECRET" \
        turnstileHostnames="$turnstile_host" \
        apiImage="$acr/{{ api_name }}:{{ tag }}" \
        workerImage="$acr/{{ worker_name }}:{{ tag }}"

# Create the sandbox group with `aca` when the Bicep preview resource is unavailable.
sandbox-group:
    #!/usr/bin/env bash
    set -euo pipefail
    az account set --subscription "{{ subscription }}"
    if az resource list \
         --subscription "{{ subscription }}" \
         --resource-group "{{ rg }}" \
         --query "[?name=='{{ sandbox_group }}'].id" -o tsv | grep -q .; then
      echo "sandbox group {{ sandbox_group }} already exists"
      exit 0
    fi
    location="$(az group show --name "{{ rg }}" --query location -o tsv)"
    aca sandboxgroup create \
      --name "{{ sandbox_group }}" \
      --location "$location" \
      --subscription "{{ subscription }}" \
      --resource-group "{{ rg }}" \
      --set-config

# Grant the worker SandboxGroup Data Owner, AcrPull, and ACR on both apps.
worker-role:
    #!/usr/bin/env bash
    set -euo pipefail
    az account set --subscription "{{ subscription }}"
    acr="$(just acr='{{ acr }}' _acr)"
    acr_id="$(az acr show --name "${acr%%.*}" --query id -o tsv)"
    api_id="$(az containerapp show -g "{{ rg }}" -n "{{ api_name }}" --query identity.principalId -o tsv)"
    worker_id="$(az containerapp show -g "{{ rg }}" -n "{{ worker_name }}" --query identity.principalId -o tsv)"
    sandbox_id="$(az resource list \
      --subscription "{{ subscription }}" \
      --resource-group "{{ rg }}" \
      --query "[?name=='{{ sandbox_group }}'].id" -o tsv)"
    az role assignment create --assignee "$api_id" --role AcrPull --scope "$acr_id" || true
    az role assignment create --assignee "$worker_id" --role AcrPull --scope "$acr_id" || true
    bind_registry() {
      local name="$1"
      local current
      current="$(az containerapp show -g "{{ rg }}" -n "$name" \
        --query "properties.configuration.registries[?server=='$acr'].identity | [0]" -o tsv)"
      if [[ "$current" == "system" ]]; then
        echo "$name already pulls $acr with system identity"
        return 0
      fi
      az containerapp registry set -g "{{ rg }}" -n "$name" --server "$acr" --identity system
    }
    bind_registry "{{ api_name }}"
    bind_registry "{{ worker_name }}"
    if [[ -z "$sandbox_id" ]]; then
      echo "No sandbox group yet. Run: just sandbox-group && just worker-role" >&2
      exit 1
    fi
    az role assignment create \
      --assignee "$worker_id" \
      --role "Container Apps SandboxGroup Data Owner" \
      --scope "$sandbox_id" || true

# Bake a clean Dangerzone disk and set BLUEPAPER_SANDBOX_DISK_ID on the worker.
disk:
    #!/usr/bin/env bash
    set -euo pipefail
    az account set --subscription "{{ subscription }}"
    just sandbox-group
    aca auth login
    aca doctor
    aca sandboxgroup disk create \
      --image "{{ dangerzone_image }}" \
      --name dangerzone-doc-to-pixels
    echo "Copy the disk id from the command above, then:"
    echo "  az containerapp update -g {{ rg }} -n {{ worker_name }} --set-env-vars BLUEPAPER_SANDBOX_DISK_ID=<disk-id>"

# Front Door in the BlueSkills sandbox. The BluePaper group denies Microsoft.Cdn,
# and this account cannot create resource groups or edit that policy.
front-door:
    #!/usr/bin/env bash
    set -euo pipefail
    az account set --subscription "{{ subscription }}"
    origin="$(az containerapp show -g "{{ rg }}" -n "{{ api_name }}" --query properties.configuration.ingress.fqdn -o tsv)"
    az deployment group create \
      --subscription "{{ subscription }}" \
      --resource-group "{{ edge_rg }}" \
      --template-file infra/frontdoor.bicep \
      --parameters originHostName="$origin"
    host="$(az afd endpoint show \
      -g "{{ edge_rg }}" \
      --profile-name "{{ front_door }}" \
      --endpoint-name "{{ front_door_endpoint }}" \
      --query hostName -o tsv)"
    az containerapp update \
      -g "{{ rg }}" \
      -n "{{ api_name }}" \
      --set-env-vars "TURNSTILE_HOSTNAMES=$host" \
      -o none
    echo "Front Door: https://$host"

# Build, push, deploy infra, ensure sandbox group, grant worker roles, put Front Door in front.
deploy: use build infra sandbox-group worker-role front-door
    @echo
    @echo "API: https://$(az afd endpoint show -g "{{ edge_rg }}" --profile-name "{{ front_door }}" --endpoint-name "{{ front_door_endpoint }}" --query hostName -o tsv)"
    @echo "Next: just disk   # bake conversion image, then just smoke"

# GET /healthz and /openapi.json on the deployed API.
smoke:
    az account set --subscription "{{ subscription }}"
    fqdn="$(az afd endpoint show -g "{{ edge_rg }}" --profile-name "{{ front_door }}" --endpoint-name "{{ front_door_endpoint }}" --query hostName -o tsv)"; \
    curl -fsS "https://$fqdn/healthz"; echo; \
    curl -fsS "https://$fqdn/openapi.json" | python -c 'import json,sys; spec=json.load(sys.stdin); print(spec["info"]["title"], "openapi", spec["openapi"], "paths", len(spec["paths"]))'

# Write docs/openapi.json from the FastAPI schema.
openapi out="docs/openapi.json":
    BLUEPAPER_API_KEY=dev poetry run python -c 'from bluepaper.api.app import write_openapi; write_openapi("{{ out }}")'

# Live ACA sandbox spike (needs Container Apps SandboxGroup Data Owner).
spike:
    az account set --subscription "{{ subscription }}"
    export BLUEPAPER_AZURE_REGION="$(az group show --name "{{ rg }}" --query location -o tsv)"; \
    poetry run python dev_scripts/aca_spike.py

[private]
_acr:
    if [[ -n "{{ acr }}" ]]; then \
      echo "{{ acr }}"; \
    else \
      server="$(az acr list --subscription "{{ subscription }}" --resource-group "{{ rg }}" --query "[0].loginServer" -o tsv)"; \
      if [[ -z "$server" ]]; then \
        echo "No ACR in {{ rg }}. Pass acr=myregistry.azurecr.io or set ACR." >&2; \
        exit 1; \
      fi; \
      echo "$server"; \
    fi
