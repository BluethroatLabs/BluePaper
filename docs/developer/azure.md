# Deploy BluePaper on Azure

Single-tenant conversion API in the operator’s subscription. Architecture: [ARCHITECTURE.md](../../ARCHITECTURE.md). Sandbox details: [aca-sandboxes.md](aca-sandboxes.md).

## Images

```bash
TOKEN=$(az acr login --name "${ACR%%.*}" --expose-token --output tsv --query accessToken)
printf '%s\n' "$TOKEN" | podman login "$ACR" \
  --username 00000000-0000-0000-0000-000000000000 \
  --password-stdin
podman build --platform linux/amd64 -f Dockerfile.api -t "$ACR/bluepaper-api:latest" .
podman build --platform linux/amd64 -f Dockerfile.worker -t "$ACR/bluepaper-worker:latest" .
podman push "$ACR/bluepaper-api:latest"
podman push "$ACR/bluepaper-worker:latest"
```

The API image must not run document parsers. The worker image includes PyMuPDF and Tesseract for `pixels_to_pdf` only.

## Deploy

```bash
az deployment group create \
  --resource-group "$RG" \
  --template-file infra/main.bicep \
  --parameters infra/parameters.json \
  --parameters apiKey="$BLUEPAPER_API_KEY" \
               apiImage="$ACR/bluepaper-api:latest" \
               workerImage="$ACR/bluepaper-worker:latest"
```

If the preview `sandboxGroups` resource fails, create it with the `aca` CLI (`aca sandboxgroup create --name bluepaper-sandboxes --location <region> --set-config`) and set `BLUEPAPER_SANDBOX_GROUP`.

Grant the worker **Container Apps SandboxGroup Data Owner** (sandboxes have no identity):

```bash
WORKER_ID=$(az containerapp show -g "$RG" -n bluepaper-worker --query identity.principalId -o tsv)
az role assignment create \
  --assignee "$WORKER_ID" \
  --role "Container Apps SandboxGroup Data Owner" \
  --scope "$SANDBOX_GROUP_ID"
```

Bake the Dangerzone disk and set `BLUEPAPER_SANDBOX_DISK_ID` on the worker. Confirm with `aca doctor`.

## Operator config

| Variable | Purpose |
| --- | --- |
| `BLUEPAPER_API_KEY` | Integrator bearer token. The console uses Turnstile instead. |
| `BLUEPAPER_MAX_UPLOAD_BYTES` | Default 32 MiB |
| `BLUEPAPER_MAX_CONCURRENT_JOBS` | 429 when running jobs hit this |
| `BLUEPAPER_MAX_QUEUE_DEPTH` | 503 when the queue is full |
| `BLUEPAPER_MAX_PIXEL_BYTES` | Cap on sandbox pixel output (default 512 MiB) |
| `BLUEPAPER_SOURCE_URL` / `BLUEPAPER_SOURCE_COMMIT` | AGPL corresponding source (`GET /v1/source`) |

## Trust split

- API identity: Storage Blob, Queue, and Table only.
- Worker identity: Storage plus SandboxGroup Data Owner.
- Conversion sandboxes: deny-all egress, no identity, no Blob mounts, deleted after every job.

## Local tests (no Azure)

```bash
export BLUEPAPER_API_KEY=dev
poetry install --with bluepaper,test
poetry run pytest tests/bluepaper -q
```

Dummy isolation is the default (`BLUEPAPER_ISOLATION=dummy`). Do not use Dummy in production.

## Live smoke

The public URL is the Front Door endpoint (`bluepaper-fd` / `bluepaper-wehi` in `rg-blueskills-wehi-aci-sandbox`). The BluePaper resource group denies `Microsoft.Cdn`, and that group already allows Front Door. It forwards HTTPS to the API container only. OpenAPI is at `https://<front-door-host>/openapi.json` (Swagger UI at `/docs`). The operator console is `/`.

1. `poetry run python dev_scripts/aca_spike.py`
2. `POST /v1/conversions` with a small PDF
3. Poll `GET /v1/conversions/{id}` until `succeeded` or `failed`
4. Fetch `/report` and `/pdf`
5. Confirm the sandbox no longer exists (`aca sandbox list`)
