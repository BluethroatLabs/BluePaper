# Deploy BluePaper on Azure

Single-tenant conversion API in the operator’s subscription. Architecture: [ARCHITECTURE.md](../../ARCHITECTURE.md). Sandbox details: [aca-sandboxes.md](aca-sandboxes.md).

## Images

Images live in the GitHub registry as `ghcr.io/bluethroatlabs/bluepaper-api` and `ghcr.io/bluethroatlabs/bluepaper-worker`. Both packages are public: Container Apps pull them without credentials, and the code in them is this repository. A tag deploy (below) builds and pushes them. To push a build by hand:

```bash
REGISTRY=ghcr.io/bluethroatlabs
gh auth refresh --scopes write:packages   # once
gh auth token | podman login ghcr.io --username "$(gh api user --jq .login)" --password-stdin
podman build --platform linux/amd64 -f Dockerfile.api \
  --build-arg "SOURCE_COMMIT=$(git rev-parse HEAD)" \
  -t "$REGISTRY/bluepaper-api:latest" .
podman build --platform linux/amd64 -f Dockerfile.worker -t "$REGISTRY/bluepaper-worker:latest" .
podman push "$REGISTRY/bluepaper-api:latest"
podman push "$REGISTRY/bluepaper-worker:latest"
```

An Azure registry also works: set `REGISTRY=myregistry.azurecr.io` for the `just` recipes. The template then has each app pull with its own identity, and `just worker-role` grants AcrPull.

The API image must not run document parsers. The worker image includes PyMuPDF and Tesseract for `pixels_to_pdf` only.

## Deploy

```bash
az deployment group create \
  --resource-group "$RG" \
  --template-file infra/main.bicep \
  --parameters infra/parameters.json \
  --parameters apiKey="$BLUEPAPER_API_KEY" \
               githubClientId="$BLUEPAPER_GITHUB_CLIENT_ID" \
               githubClientSecret="$BLUEPAPER_GITHUB_CLIENT_SECRET" \
               apiImage="$REGISTRY/bluepaper-api:latest" \
               workerImage="$REGISTRY/bluepaper-worker:latest"
```

If the preview `sandboxGroups` resource fails, create it with the `aca` CLI (`aca sandboxgroup create --name bluepaper-sandboxes --location <region> --set-config`) and set `BLUEPAPER_SANDBOX_GROUP`.

Grant the worker **Container Apps SandboxGroup Data Owner** (sandboxes have no identity):

```bash
WORKER_ID=$(az containerapp show -g "$RG" -n bluepaper-worker --query identity.principalId -o tsv)
az role assignment create \
  --assignee-object-id "$WORKER_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Container Apps SandboxGroup Data Owner" \
  --scope "$SANDBOX_GROUP_ID"
```

`just deploy` bakes the Dangerzone disk for the upstream image's current digest, or reuses the disk already baked from that digest, and sets `BLUEPAPER_SANDBOX_DISK_ID` on the worker. `just disk` does that step alone. Run it after each upstream image release. Confirm with `aca doctor`.

## Deploy on tag

Pushing a `v*` tag that is on `main` runs [bluepaper-deploy.yml](../../.github/workflows/bluepaper-deploy.yml): the checks from [bluepaper-ci.yml](../../.github/workflows/bluepaper-ci.yml), then both image builds, a push to the GitHub registry, and an update of the two Container Apps to the pushed digests. The run fails unless `GET /v1/source` on the public hostname reports the tagged commit.

```bash
git tag v1.2.3 && git push origin v1.2.3
gh workflow run bluepaper-deploy.yml --ref v1.2.2   # redeploy or roll back
```

It ships images only. Template changes still need `just infra`, a new upstream converter image needs `just disk`, and Front Door changes need `just front-door`. The tag deploy also moves `latest`, so `just infra` on its own deploys the last release.

Three things have to be in place:

- **Azure sign-in.** The workflow uses OIDC as the app registration named by the `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` repository secrets. No password is stored. The app has a federated credential for the subject `repo:BluethroatLabs@116263537/BluePaper@1361172318:environment:production` (GitHub's immutable subject format, which carries the owner and repository ids) and Contributor on the resource group. Container Apps Contributor would be enough. It has no role on any registry.
- **The `production` environment.** It decides which runs may sign in. Its deployment rules must allow `v*` tags, because a tag deploy runs on the tag and not on `main`. Keep its required reviewers: any run that names the environment can sign in.
- **Public packages.** A package is private when it is first pushed, and the first deploy stops with the link to change that. Make both packages public once, in each package's settings, then re-run the job.

## Operator config

| Variable | Purpose |
| --- | --- |
| `BLUEPAPER_API_KEY` | Integrator bearer token. The console uses Turnstile instead. |
| `BLUEPAPER_GITHUB_CLIENT_ID` / `BLUEPAPER_GITHUB_CLIENT_SECRET` | Bluethroat GitHub App credentials used to validate CLI user tokens. Configure both or neither. |
| `BLUEPAPER_MAX_UPLOAD_BYTES` | Default 32 MiB |
| `BLUEPAPER_MAX_CONCURRENT_JOBS` | 429 when one GitHub user or guest address already has this many conversions queued or running (default 4). The API key is exempt. |
| `BLUEPAPER_MAX_QUEUE_DEPTH` | 503 when the queue is full |
| `BLUEPAPER_MAX_PIXEL_BYTES` | Cap on sandbox pixel output (default 512 MiB) |
| `BLUEPAPER_SOURCE_URL` / `BLUEPAPER_SOURCE_COMMIT` | AGPL corresponding source (`GET /v1/source`). The API image build pins `SOURCE_COMMIT` so this is not empty in production. |
| `BLUEPAPER_SOURCE_DIGEST` | Image digest used when the git commit is unset |

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
