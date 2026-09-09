from __future__ import annotations

import logging
import time

from bluepaper.config import Settings
from bluepaper.storage import build_stores
from bluepaper.worker.job import get_isolation, process_one

log = logging.getLogger("bluepaper.worker")


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()
    stores = build_stores(settings)
    isolation = get_isolation(settings)
    log.info("starting BluePaper worker isolation=%s", settings.isolation)
    while True:
        try:
            processed = process_one(settings, stores, isolation)
        except Exception:
            log.exception("worker loop error")
            processed = False
        if not processed:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run()
