#!/usr/bin/env python3
"""Build a versioned KEGG compound/pathway cache for bounded metabolomics jobs."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


URLS = {
    "compound_list": "https://rest.kegg.jp/list/compound",
    "compound_pathway": "https://rest.kegg.jp/link/pathway/compound",
    "pathway_list": "https://rest.kegg.jp/list/pathway",
}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "AI-for-Science/omics-cache-builder"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower().replace("α", "alpha").replace("β", "beta"))


def main() -> None:
    output = Path(__file__).resolve().parent.parent / "data" / "metabolite_kegg_cache_v1"
    output.mkdir(parents=True, exist_ok=True)
    raw = {name: fetch(url) for name, url in URLS.items()}

    aliases: list[tuple[str, str, str]] = []
    for row in csv.reader(raw["compound_list"].decode("utf-8").splitlines(), delimiter="\t"):
        compound_id = row[0].removeprefix("cpd:")
        names = [item.strip() for item in row[1].split(";") if item.strip()]
        canonical = names[0] if names else compound_id
        for name in names:
            aliases.append((normalise_name(name), compound_id, canonical))
    aliases = sorted(set(aliases))

    term_gene: list[tuple[str, str]] = []
    for row in csv.reader(raw["compound_pathway"].decode("utf-8").splitlines(), delimiter="\t"):
        compound_id = row[0].removeprefix("cpd:")
        pathway_id = row[1].removeprefix("path:")
        if pathway_id.startswith("map"):
            term_gene.append((pathway_id, compound_id))
    term_gene = sorted(set(term_gene))

    term_name: list[tuple[str, str]] = []
    for row in csv.reader(raw["pathway_list"].decode("utf-8").splitlines(), delimiter="\t"):
        pathway_id = row[0].removeprefix("path:")
        if pathway_id.startswith("map"):
            term_name.append((pathway_id, row[1]))
    term_name = sorted(set(term_name))

    files = {
        "compound_alias.tsv": (["normalised_name", "compound_id", "canonical_name"], aliases),
        "compound_term_gene.tsv": (["term", "gene"], term_gene),
        "compound_term_name.tsv": (["term", "name"], term_name),
    }
    hashes: dict[str, str] = {}
    for filename, (header, rows) in files.items():
        path = output / filename
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)
        hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()

    metadata = {
        "version": "metabolite-kegg-cache-v1",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "sources": URLS,
        "counts": {"aliases": len(aliases), "term_gene": len(term_gene), "term_name": len(term_name)},
        "sha256": hashes,
        "scope": "KEGG reference compound-to-pathway links; exact normalised aliases only.",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
