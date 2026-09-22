# ACA Sandboxes for BluePaper

Isolation for `doc_to_pixels` is an Azure Container Apps Sandbox (hardware-isolated microVM). This is **not** Podman + gVisor and **not** a plain ACA Job.

## Pixel transport (spike decision)

Live Azure was not available when this was written (`az account show` failed). The decision is based on the public Python SDK (`azure-containerapps-sandbox` 0.1.0b4) and Microsoft’s sandbox skill/docs.

| API | Buffering | Implication |
| --- | --- | --- |
| `sandbox.exec(command)` | One-shot: returns `exit_code`, `stdout`, `stderr` after the process exits | Do **not** stream Dangerzone’s pixel protocol over exec stdout. A 150 DPI RGB dump will not fit a pipe-like `Popen`. |
| `sandbox.write_file` / `read_file` / `stat_file` | Whole-file | Same buffering. Cap with `BLUEPAPER_MAX_PIXEL_BYTES` (default 512 MiB) **before** `read_file`. |
| Stdin of `doc_to_pixels` | Not exposed by exec | Upload the original with `write_file`, run a tiny wrapper that opens those paths as stdin/stdout. |

**Chosen v1 path**

1. Create sandbox from a **clean** Dangerzone disk (`create_disk_image` from the upstream OCI image, then `begin_create_sandbox(disk_id=..., cpu="2000m", memory="4096Mi", egress_policy=Deny)`).
2. `mkdir` + `write_file` the original to `/tmp/bluepaper/input.bin`.
3. `write_file` the wrapper at `/tmp/bluepaper/run_convert.py` (not a document parser; it only redirects stdio into `dangerzone.conversion.doc_to_pixels`).
4. `exec("python3 /tmp/bluepaper/run_convert.py")`.
5. `stat_file("/tmp/bluepaper/pixels.bin")`; fail conversion if size exceeds the pixel budget.
6. `read_file` into a tempfile and run trusted `convert_from_pixel_stream`.
7. `sandbox.delete()` in `finally`. **Never** `create_snapshot` / `commit` after a document has been written.

Page-at-a-time `read_file` is a later optimization if 512 MiB still OOMs; budgets already cap pages and dimensions.

Exec stdout size limits and `read_file` caps are **unknown until a live run**. Record them by running [dev_scripts/aca_spike.py](../../dev_scripts/aca_spike.py) in a subscription with Sandboxes preview.

## Rules

- Egress **deny-all** at create time (`EgressPolicy(default_action="Deny")`, traffic inspection Full when the SDK allows it).
- No managed identity on the sandbox. No Blob volume mounts. The worker copies bytes in and pixels out.
- Default tier **L**: 2 vCPU / 4 GiB (`cpu="2000m"`, `memory="4096Mi"`).
- Do not nest gVisor/`runsc`.
- Idle suspend/resume is only for a **clean** golden image, never for a sandbox that processed an upload.

## Bake the conversion disk (operator)

```bash
# after aca auth login && aca doctor
aca sandboxgroup disk create \
  --image ghcr.io/freedomofpress/dangerzone/v1:latest \
  --name dangerzone-doc-to-pixels
```

Or Python: `SandboxGroupClient.create_disk_image("<oci>", name="dangerzone-doc-to-pixels")`.

`just disk` does the same through the Azure CLI. It reuses that image when it already exists and sets `BLUEPAPER_SANDBOX_DISK_ID` on the worker.

## Live spike

```bash
export BLUEPAPER_AZURE_SUBSCRIPTION_ID=...
export BLUEPAPER_AZURE_RESOURCE_GROUP=...
export BLUEPAPER_AZURE_REGION=eastus2
export BLUEPAPER_SANDBOX_GROUP=...
# optional: BLUEPAPER_SANDBOX_DISK_ID, or the spike uses public disk "python"
poetry run python dev_scripts/aca_spike.py
```

The script always deletes the sandbox it created.
