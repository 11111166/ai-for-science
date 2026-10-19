"""Capture auditable public listing counts for identified fluralaner intermediates.

Counts are marketplace listings, not unique manufacturers, current capacity,
inventory or quotations. Rebuild, review and redeploy before refreshing them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import re
from urllib.request import Request, urlopen

DESTINATION = Path(__file__).resolve().parents[1] / "app/data/fluralaner_supplier_snapshot.json"
SOURCES = {
    "step_1": ("369-32-4", "https://www.chemicalbook.com/ChemicalProductProperty_EN_CB1734502.htm", r"Global Suppliers\s*\(\s*(\d+)"),
    "step_2": ("1138444-80-0", "https://www.chemicalbook.com/ProdSupplierGNCB72479625_EN.htm", r"All\s*\(\s*(\d+)"),
    "step_3": ("55860-35-0", "https://www.chemicalbook.com/ChemicalProductProperty_EN_CB43385195.htm", r"Global Suppliers\s*\(\s*(\d+)"),
    "step_6": ("864731-37-3", "https://m.chemicalbook.com/ProdSupplierGNCB54662829.htm", r"所有\s*\(\s*(\d+)"),
    "branch_reagent": ("130336-16-2", "https://www.chemicalbook.com/Manufacturers/3-5-dichloro-2-2-2-trifluoroacetophenone.htm", r'class="c1">(\d+)</span>\s+supplier list'),
}


def build() -> dict:
    checked_at = datetime.now(timezone.utc).isoformat()
    records = {}
    for key, (cas, url, pattern) in SOURCES.items():
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as response:
            raw = response.read()
        html = raw.decode("utf-8", errors="replace")
        if cas not in html:
            raise RuntimeError(f"{key}: CAS absent from supplier page")
        match = re.search(pattern, html, re.I)
        if not match:
            raise RuntimeError(f"{key}: supplier total absent or page format changed")
        count = int(match.group(1))
        if not 0 < count < 10_000:
            raise RuntimeError(f"{key}: implausible listing count {count}")
        records[key] = {"cas": cas, "source_url": url, "listed_suppliers": count,
                        "source_sha256": sha256(raw).hexdigest(), "checked_at": checked_at,
                        "basis": "ChemicalBook 单页标示的供应商挂牌总数（未核验生产厂家，可能混合贸易商、试剂商、重复/过期条目）"}
    return {"schema_version": 1, "records": records}


if __name__ == "__main__":
    data = build()
    DESTINATION.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({key: record["listed_suppliers"] for key, record in data["records"].items()})
