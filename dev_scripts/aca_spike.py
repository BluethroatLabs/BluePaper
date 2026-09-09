#!/usr/bin/env python3
"""Live ACA Sandbox spike for BluePaper pixel transport.

Creates one sandbox with deny-all egress, measures exec/file buffering,
and always deletes the sandbox. See docs/developer/aca-sandboxes.md.

Requires az login / managed identity and Container Apps SandboxGroup Data Owner.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback


def _env(name: str) -> str | None:
    return os.environ.get(name) or os.environ.get(name.removeprefix("BLUEPAPER_"))


def main() -> int:
    subscription_id = _env("BLUEPAPER_AZURE_SUBSCRIPTION_ID") or _env(
        "AZURE_SUBSCRIPTION_ID"
    )
    resource_group = _env("BLUEPAPER_AZURE_RESOURCE_GROUP")
    region = _env("BLUEPAPER_AZURE_REGION") or "eastus2"
    sandbox_group = _env("BLUEPAPER_SANDBOX_GROUP")
    disk_id = _env("BLUEPAPER_SANDBOX_DISK_ID")

    if not all([subscription_id, resource_group, sandbox_group]):
        print(
            "Missing Azure config. Set BLUEPAPER_AZURE_SUBSCRIPTION_ID, "
            "BLUEPAPER_AZURE_RESOURCE_GROUP, BLUEPAPER_SANDBOX_GROUP.",
            file=sys.stderr,
        )
        print(
            json.dumps(
                {
                    "live": False,
                    "reason": "not_configured",
                    "transport": "write_file + wrapper + pixels.bin + read_file",
                    "exec_stdout": "one-shot buffered (SDK)",
                    "default_max_pixel_bytes": 512 * 1024 * 1024,
                }
            )
        )
        return 2

    try:
        from azure.containerapps.sandbox import (  # type: ignore[import-untyped]
            EgressPolicy,
            SandboxGroupClient,
            endpoint_for_region,
        )
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        print(f"SDK import failed: {exc}", file=sys.stderr)
        return 1

    credential = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint_for_region(region),
        credential,
        subscription_id=subscription_id,
        resource_group=resource_group,
        sandbox_group=sandbox_group,
    )

    create_kwargs: dict = {
        "cpu": "2000m",
        "memory": "4096Mi",
        "egress_policy": EgressPolicy(default_action="Deny"),
        "labels": {"bluepaper": "spike"},
    }
    if disk_id:
        create_kwargs["disk_id"] = disk_id
    else:
        create_kwargs["disk"] = "python"

    sandbox = None
    findings: dict = {"live": True, "deleted": False}
    try:
        t0 = time.monotonic()
        sandbox = client.begin_create_sandbox(**create_kwargs).result()
        findings["create_seconds"] = round(time.monotonic() - t0, 3)
        findings["sandbox_id"] = getattr(sandbox, "id", None)

        policy = None
        if hasattr(sandbox, "get_egress_policy"):
            policy = sandbox.get_egress_policy()
        findings["egress_policy"] = str(policy)

        payload = b"bluepaper-spike-input\n"
        sandbox.write_file("/tmp/bluepaper-spike-in.bin", payload)
        result = sandbox.exec(
            "python3 -c \"open('/tmp/bluepaper-spike-out.bin','wb').write("
            "open('/tmp/bluepaper-spike-in.bin','rb').read() * 1)\""
        )
        findings["exec_exit_code"] = result.exit_code
        findings["exec_stdout_bytes"] = len(result.stdout or b"")
        findings["exec_stderr_bytes"] = len((result.stderr or b""))

        info = sandbox.stat_file("/tmp/bluepaper-spike-out.bin")
        findings["stat_size"] = getattr(info, "size", None)
        echoed = sandbox.read_file("/tmp/bluepaper-spike-out.bin")
        if isinstance(echoed, str):
            echoed = echoed.encode("utf-8")
        findings["roundtrip_ok"] = echoed == payload
        findings["transport"] = "write_file + exec + read_file"
        findings["snapshot_after_document"] = False
        print(json.dumps(findings, indent=2, default=str))
        return 0 if findings["roundtrip_ok"] and result.exit_code == 0 else 1
    except Exception:
        traceback.print_exc()
        findings["error"] = "exception"
        print(json.dumps(findings, indent=2, default=str))
        return 1
    finally:
        if sandbox is not None:
            try:
                sandbox.delete()
                findings["deleted"] = True
            except Exception:
                traceback.print_exc()
                print("ERROR: failed to delete spike sandbox", file=sys.stderr)
        client.close()


if __name__ == "__main__":
    sys.exit(main())
