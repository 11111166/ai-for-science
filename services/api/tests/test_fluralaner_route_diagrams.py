import json
from pathlib import Path
import xml.etree.ElementTree as ET

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors


ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "apps/web/data/fluralaner_route_structures.json"
PATENT = ROOT / "services/api/app/data/fluralaner_patent_sources.json"
ASSETS = ROOT / "apps/web/public/process/fluralaner"


def test_diagram_structures_match_formulas_and_verified_patent_snapshot():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    patent = json.loads(PATENT.read_text(encoding="utf-8"))["sources"][data["patent"]]
    assert data["patent_source_sha256"] == patent["source_sha256"]
    assert [row["id"] for row in data["nodes"]] == ["start", *[f"step{i}" for i in range(1, 8)]]
    evidence = {row["locator"]: row["text"] for row in patent["paragraphs"]}
    for row in [*data["nodes"], data["branch_reagent"]]:
        molecule = Chem.MolFromSmiles(row["smiles"])
        assert molecule is not None
        assert rdMolDescriptors.CalcMolFormula(molecule) == row["formula"]
        assert row["patent_locator"] in evidence
        assert row["source_url"].startswith("https://")
    assert "ethyl" in evidence["p0234"].lower()
    assert "glycine" in evidence["p0235"].lower()
    assert data["nodes"][6]["formula"] == "C20H15Cl2F3N2O4"


def test_diagrams_are_standalone_svg_and_warn_about_unverified_engineering():
    route = (ASSETS / "synthesis-route.svg").read_text(encoding="utf-8")
    flow = (ASSETS / "process-flow-concept.svg").read_text(encoding="utf-8")
    for svg in (route, flow):
        assert ET.fromstring(svg).tag.endswith("svg")
        assert "氟雷拉纳" in svg
    assert "第 6 步按实验所得羧酸" in route
    assert "物料衡算和放大条件仍待验证" in flow
    assert "非专利原图" in flow
    assert "柱层析" in flow
