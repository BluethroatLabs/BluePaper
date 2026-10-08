from __future__ import annotations

import logging

from bluepaper.logging import configure_logging


def test_azure_info_is_quiet_and_app_errors_stay() -> None:
    root = logging.getLogger()
    azure = logging.getLogger("azure")
    previous_root = root.level
    previous_azure = azure.level
    handlers = list(root.handlers)
    try:
        configure_logging()
        app_log = logging.getLogger("bluepaper.api")
        sdk_log = logging.getLogger("azure.core.pipeline.policies.http_logging_policy")
        assert app_log.isEnabledFor(logging.ERROR)
        assert app_log.isEnabledFor(logging.INFO)
        assert sdk_log.isEnabledFor(logging.ERROR)
        assert sdk_log.isEnabledFor(logging.WARNING)
        assert not sdk_log.isEnabledFor(logging.INFO)
    finally:
        root.setLevel(previous_root)
        azure.setLevel(previous_azure)
        root.handlers[:] = handlers
