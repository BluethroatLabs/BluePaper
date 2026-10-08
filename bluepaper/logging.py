"""Process logging for the API and worker."""

from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    """Log application records at INFO. Keep Azure SDK failures, drop its INFO traces."""
    logging.basicConfig(level=logging.INFO, format=_FORMAT)
    # basicConfig does nothing once a handler exists, so set the level directly.
    logging.getLogger().setLevel(logging.INFO)
    # HTTP request lines from azure-core, identity, and storage are INFO.
    # WARNING leaves SDK errors and warnings in place.
    logging.getLogger("azure").setLevel(logging.WARNING)
