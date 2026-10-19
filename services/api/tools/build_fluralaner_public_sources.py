"""Refresh the versioned public-patent excerpt used by the offline demo.

Run from the repository root with .venv/bin/python. This is a deliberate
snapshot build, not a claim that a visitor's browser can reach Google Patents.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import sys
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.process_patent_extraction import _google_paragraphs

DESTINATION = Path(__file__).resolve().parents[1] / "app/data/fluralaner_patent_sources.json"
SOURCES = {
    "WO2023012821A1": {
        "url": "https://patents.google.com/patent/WO2023012821A1/en",
        "locators": ["p0214", "p0215", "p0218", "p0219", "p0222", "p0223", "p0226", "p0227", "p0230", "p0231", "p0234", "p0235", "p0238", "p0239"],
    },
    "EP4077285A1": {
        "url": "https://patents.google.com/patent/EP4077285A1/en",
        "locators": ["p0097", "p0098", *[f"p{number:04}" for number in range(99, 109)]],
    },
}


def build() -> dict:
    records = {}
    for publication, spec in SOURCES.items():
        with urlopen(Request(spec["url"], headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as response:
            raw = response.read()
        paragraphs = dict((locator, text) for text, locator in _google_paragraphs(raw))
        missing = set(spec["locators"]) - paragraphs.keys()
        if missing:
            raise RuntimeError(f"{publication}: expected patent paragraphs missing: {sorted(missing)}")
        records[publication] = {
            "url": spec["url"],
            "source_kind": "Google Patents 公开文本镜像",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": sha256(raw).hexdigest(),
            "paragraphs": [{"locator": locator, "text": paragraphs[locator]} for locator in spec["locators"]],
        }
    return {"schema_version": 1, "sources": records}


if __name__ == "__main__":
    content = build()
    DESTINATION.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {DESTINATION} ({DESTINATION.stat().st_size} bytes)")
