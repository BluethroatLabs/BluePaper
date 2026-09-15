# BluePaper

Network-service fork of [Dangerzone](https://github.com/freedomofpress/dangerzone). Callers upload an untrusted document and receive a PDF rebuilt from pixels, plus a regexp report on the original bytes so they can see that sanitization removed something real.

Safety comes from **destruction**, not detection. Zero regexp hits is not “clean.” The trusted API never opens the original with a document parser. Conversion runs in a disposable Azure Container Apps Sandbox.

The product contract is [ARCHITECTURE.md](ARCHITECTURE.md). Deep PDF-analysis research (parser IR, JS AST, visual phishing) is **out of scope**; see [docs/research-and-architecture.md](docs/research-and-architecture.md).

## HTTP API

All `/v1` routes require `Authorization: Bearer <api-key>`. Conversion ids look like `cnv_…`. Hashes are SHA-256 hex.

OpenAPI is unauthenticated: `GET /openapi.json` (spec) and `GET /docs` (Swagger UI).

- `POST /v1/conversions` — multipart `file` and optional `ocr_lang` → **202** queued
- `GET /v1/conversions/{id}` — status only
- `GET /v1/conversions/{id}/report` — regexp hits when conversion succeeded, or when it failed but the scan finished
- `GET /v1/conversions/{id}/pdf` — sanitized PDF (succeeded only)
- `DELETE /v1/conversions/{id}` — cancel or delete artifacts
- `GET /v1/source` — AGPL corresponding source pointer

Unsupported types return **415** before a sandbox is created. Size, concurrency, and queue limits return **413** / **429** / **503**.

## Local development

```bash
export BLUEPAPER_API_KEY=dev
poetry install --with bluepaper,test
poetry run pytest tests/bluepaper -q

# API (in-memory storage; run a worker in the same process only via tests)
poetry run bluepaper-api
```

Production isolation is `BLUEPAPER_ISOLATION=aca`. `dummy` is for tests only.

Azure deploy, identities, and disk baking: [docs/developer/azure.md](docs/developer/azure.md). Sandbox lifecycle: [docs/developer/aca-sandboxes.md](docs/developer/aca-sandboxes.md).

The Dangerzone desktop GUI and Podman/gVisor path remain in this tree for upstream mergeability; they are not the BluePaper product.

## License

AGPLv3. Offering BluePaper as a network service requires providing corresponding source to users of that service (`GET /v1/source`, and this repository).
