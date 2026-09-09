from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def ocr_language_codes() -> frozenset[str]:
    candidates = [
        Path(__file__).resolve().parent.parent / "share" / "ocr-languages.json",
        Path("/usr/share/dangerzone/ocr-languages.json"),
        Path("/usr/share/tesseract-ocr/4.00/tessdata"),
    ]
    for path in candidates:
        if path.is_file() and path.suffix == ".json":
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return frozenset(str(code) for code in data.values())
    return frozenset({"eng"})


def is_supported_ocr_lang(ocr_lang: str) -> bool:
    return ocr_lang in ocr_language_codes()
