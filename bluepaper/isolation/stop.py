from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger("bluepaper.isolation")


class StopSignal:
    """Tells a running conversion to give up, and kills what it started.

    ``is_set()`` lets a loop poll it, like ``threading.Event``. ``on_stop``
    callbacks run once, in the thread that calls ``set()``, so a sandbox or a
    process pool is torn down even when the conversion thread is blocked.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._set = False
        self._callbacks: list[Callable[[], None]] = []

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        with self._lock:
            if self._set:
                return
            self._set = True
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            _run(callback)

    def on_stop(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Run ``callback`` on ``set()`` (now, if already set). Returns an undo."""
        with self._lock:
            if not self._set:
                self._callbacks.append(callback)
                return lambda: self._forget(callback)
        _run(callback)
        return lambda: None

    def _forget(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)


def _run(callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:
        log.exception("stop callback failed")
