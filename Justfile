# BluePaper Azure deploy
#
# Target:
#   /subscriptions/1e238434-310b-4bcd-ab1f-a9381d170243/resourceGroups/rg-bluepaper-wehi-sandbox
#
# Prereqs: `az login`, Podman, API and GitHub App credentials (env or `.env`).
# Images live in ghcr.io/bluethroatlabs, where a `v*` tag deploy pushes them
# (.github/workflows/bluepaper-deploy.yml). The packages are public, and
# `just build` needs `gh` with the write:packages scope to push there.
# Pass `registry=myregistry.azurecr.io` or set `REGISTRY` for an Azure registry.
#
#   just              # this list
#   just use          # select the subscription
#   just deploy       # build, push, bicep, sandbox disk, worker role
#   just infra        # bicep only, with the images already in the registry
#   just openapi      # write docs/openapi.json
#   just disk         # bake the Dangerzone disk for the current upstream image and set it on the worker
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
# Hostname Cloudflare reports for a widget solved on the public site.
# The Front Door endpoint hostname (*.azurefd.net) is not that name.
public_host := "bluepaper.bluethroatlabs.com"
sandbox_group := "bluepaper-sandboxes"
dangerzone_image := "ghcr.io/freedomofpress/dangerzone/v1:latest"
tag := env("TAG", "latest")
registry := env("REGISTRY", "ghcr.io/bluethroatlabs")

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

# Build linux/amd64 API and worker images and push them to the registry.
build:
    #!/usr/bin/env bash
    set -euo pipefail
    registry="{{ registry }}"
    host="${registry%%/*}"
    if [[ "$host" == *.azurecr.io ]]; then
      az account set --subscription "{{ subscription }}"
      token="$(az acr login --name "${host%%.*}" --expose-token --output tsv --query accessToken)"
      printf '%s\n' "$token" | podman login "$host" \
        --username 00000000-0000-0000-0000-000000000000 \
        --password-stdin
    else
      # Pushing a package needs a scope `gh auth login` does not ask for:
      #   gh auth refresh --scopes write:packages
      gh auth token | podman login "$host" \
        --username "$(gh api user --jq .login)" \
        --password-stdin
    fi
    commit="$(git rev-parse HEAD)"
    # The source label links each package to its repository.
    source="org.opencontainers.image.source=https://github.com/BluethroatLabs/BluePaper"
    podman build --platform linux/amd64 -f Dockerfile.api \
      --build-arg "SOURCE_COMMIT=${commit}" \
      --label "$source" \
      -t "$registry/{{ api_name }}:{{ tag }}" .
    podman build --platform linux/amd64 -f Dockerfile.worker \
      --label "$source" \
      -t "$registry/{{ worker_name }}:{{ tag }}" .
    podman push "$registry/{{ api_name }}:{{ tag }}"
    podman push "$registry/{{ worker_name }}:{{ tag }}"

# Deploy Container Apps, storage, and (if the preview type works) the sandbox group.
infra:
    #!/usr/bin/env bash
    set -euo pipefail
    : "${BLUEPAPER_API_KEY:?Set BLUEPAPER_API_KEY in the environment or .env}"
    : "${BLUEPAPER_GITHUB_CLIENT_ID:?Set BLUEPAPER_GITHUB_CLIENT_ID in the environment or .env}"
    : "${BLUEPAPER_GITHUB_CLIENT_SECRET:?Set BLUEPAPER_GITHUB_CLIENT_SECRET in the environment or .env}"
    : "${TURNSTILE_SECRET:?Set TURNSTILE_SECRET in the environment or .env}"
    az account set --subscription "{{ subscription }}"
    registry="{{ registry }}"
    endpoint_host="$(az afd endpoint show \
      --subscription "{{ subscription }}" \
      --resource-group "{{ edge_rg }}" \
      --profile-name "{{ front_door }}" \
      --endpoint-name "{{ front_door_endpoint }}" \
      --query hostName -o tsv 2>/dev/null || true)"
    turnstile_host="{{ public_host }}"
    if [[ -n "$endpoint_host" && "$endpoint_host" != "{{ public_host }}" ]]; then
      turnstile_host="${turnstile_host},${endpoint_host}"
    fi
    disk_id="${BLUEPAPER_SANDBOX_DISK_ID:-}"
    if [[ -z "$disk_id" ]]; then
      disk_id="$(az containerapp show \
        --subscription "{{ subscription }}" \
        --resource-group "{{ rg }}" \
        --name "{{ worker_name }}" \
        --query "properties.template.containers[0].env[?name=='BLUEPAPER_SANDBOX_DISK_ID'].value | [0]" \
        -o tsv 2>/dev/null || true)"
    fi
    disk_args=()
    if [[ -n "$disk_id" ]]; then
      disk_args+=(sandboxDiskId="$disk_id")
    fi
    az deployment group create \
      --subscription "{{ subscription }}" \
      --resource-group "{{ rg }}" \
      --template-file infra/main.bicep \
      --parameters infra/parameters.json \
      --parameters \
        apiKey="$BLUEPAPER_API_KEY" \
        githubClientId="$BLUEPAPER_GITHUB_CLIENT_ID" \
        githubClientSecret="$BLUEPAPER_GITHUB_CLIENT_SECRET" \
        turnstileSecret="$TURNSTILE_SECRET" \
        turnstileHostnames="$turnstile_host" \
        apiImage="$registry/{{ api_name }}:{{ tag }}" \
        workerImage="$registry/{{ worker_name }}:{{ tag }}" \
        "${disk_args[@]}"
    # The template image stays `:latest`. Container Apps pins the digest
    # when a revision is created, and setting that same tag again does not
    # pull the manifest just pushed. Point each app at the tag's digest.
    for name in "{{ api_name }}" "{{ worker_name }}"; do
      digest="$(just _digest "$registry/$name:{{ tag }}")"
      target="$registry/$name@$digest"
      current="$(az containerapp show \
        --subscription "{{ subscription }}" \
        --resource-group "{{ rg }}" \
        --name "$name" \
        --query "properties.template.containers[0].image" -o tsv)"
      if [[ "$current" == "$target" ]]; then
        echo "$name already running $digest"
        continue
      fi
      az containerapp update \
        --subscription "{{ subscription }}" \
        --resource-group "{{ rg }}" \
        --name "$name" \
        --image "$target" \
        -o none
      echo "$name -> $digest"
    done

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

# With an Azure registry, both apps also get AcrPull and pull with their identities.
# Grant the worker SandboxGroup Data Owner.
worker-role:
    #!/usr/bin/env bash
    set -euo pipefail
    az account set --subscription "{{ subscription }}"
    registry="{{ registry }}"
    host="${registry%%/*}"
    api_id="$(az containerapp show -g "{{ rg }}" -n "{{ api_name }}" --query identity.principalId -o tsv)"
    worker_id="$(az containerapp show -g "{{ rg }}" -n "{{ worker_name }}" --query identity.principalId -o tsv)"
    sandbox_id="$(az resource list \
      --subscription "{{ subscription }}" \
      --resource-group "{{ rg }}" \
      --query "[?name=='{{ sandbox_group }}'].id" -o tsv)"
    # Grant a role to an app's managed identity unless it already has it.
    # The principal type is passed explicitly: this account's right to assign
    # roles carries a condition that only admits service principals, and
    # `--assignee` leaves the type out when it cannot query Microsoft Graph.
    grant() {
      local principal="$1" role="$2" scope="$3"
      local existing
      existing="$(az role assignment list --scope "$scope" --role "$role" \
        --query "[?principalId=='$principal'] | [0].id" -o tsv)"
      if [[ -n "$existing" ]]; then
        echo "$role already granted to $principal"
        return 0
      fi
      az role assignment create \
        --assignee-object-id "$principal" \
        --assignee-principal-type ServicePrincipal \
        --role "$role" \
        --scope "$scope" \
        -o none
      echo "$role granted to $principal"
    }
    bind_registry() {
      local name="$1"
      local current
      current="$(az containerapp show -g "{{ rg }}" -n "$name" \
        --query "properties.configuration.registries[?server=='$host'].identity | [0]" -o tsv)"
      if [[ "$current" == "system" ]]; then
        echo "$name already pulls $host with system identity"
        return 0
      fi
      az containerapp registry set -g "{{ rg }}" -n "$name" --server "$host" --identity system
    }
    if [[ "$host" == *.azurecr.io ]]; then
      acr_id="$(az acr show --name "${host%%.*}" --query id -o tsv)"
      grant "$api_id" AcrPull "$acr_id"
      grant "$worker_id" AcrPull "$acr_id"
      bind_registry "{{ api_name }}"
      bind_registry "{{ worker_name }}"
    else
      echo "$host images are public: no pull role or registry binding"
    fi
    if [[ -z "$sandbox_id" ]]; then
      echo "No sandbox group yet. Run: just sandbox-group && just worker-role" >&2
      exit 1
    fi
    grant "$worker_id" "Container Apps SandboxGroup Data Owner" "$sandbox_id"

# Bake the Dangerzone disk for the upstream image's current digest (reused only
# while that digest is unchanged) and set BLUEPAPER_SANDBOX_DISK_ID on the worker.
disk:
    #!/usr/bin/env bash
    set -euo pipefail
    az account set --subscription "{{ subscription }}"
    just sandbox-group
    region="$(az group show --name "{{ rg }}" --query location -o tsv)"
    disk_id="$(python3 dev_scripts/ensure_sandbox_disk.py \
      --subscription "{{ subscription }}" \
      --resource-group "{{ rg }}" \
      --region "$region" \
      --sandbox-group "{{ sandbox_group }}" \
      --name dangerzone-doc-to-pixels \
      --image "{{ dangerzone_image }}")"
    az containerapp update \
      --subscription "{{ subscription }}" \
      --resource-group "{{ rg }}" \
      --name "{{ worker_name }}" \
      --set-env-vars "BLUEPAPER_SANDBOX_DISK_ID=$disk_id" \
      -o none
    echo "BLUEPAPER_SANDBOX_DISK_ID=$disk_id"

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
    endpoint_host="$(az afd endpoint show \
      -g "{{ edge_rg }}" \
      --profile-name "{{ front_door }}" \
      --endpoint-name "{{ front_door_endpoint }}" \
      --query hostName -o tsv)"
    turnstile_host="{{ public_host }}"
    if [[ -n "$endpoint_host" && "$endpoint_host" != "{{ public_host }}" ]]; then
      turnstile_host="${turnstile_host},${endpoint_host}"
    fi
    az containerapp update \
      -g "{{ rg }}" \
      -n "{{ api_name }}" \
      --set-env-vars "TURNSTILE_HOSTNAMES=$turnstile_host" \
      -o none
    echo "Front Door: https://{{ public_host }} (endpoint ${endpoint_host})"

# Build, push, deploy infra, ensure sandbox group, grant worker roles, set the disk, put Front Door in front.
deploy: use build infra sandbox-group worker-role disk front-door
    @echo
    @echo "API: https://$(az afd endpoint show -g "{{ edge_rg }}" --profile-name "{{ front_door }}" --endpoint-name "{{ front_door_endpoint }}" --query hostName -o tsv)"

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

# Print the digest a tag points at right now. An Azure registry is asked
# through the CLI. Any other registry is asked without credentials, so the
# image must be public, as it must be for Container Apps to pull it.
[private]
_digest image:
    #!/usr/bin/env bash
    set -euo pipefail
    image="{{ image }}"
    host="${image%%/*}"
    if [[ "$host" == *.azurecr.io ]]; then
      az acr repository show \
        --name "${host%%.*}" \
        --image "${image#*/}" \
        --query digest -o tsv
    else
      python3 -c 'import sys; sys.path.insert(0, "dev_scripts"); from ensure_sandbox_disk import resolve_digest; print(resolve_digest(sys.argv[1]))' "$image"
    fi
