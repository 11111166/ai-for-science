import hashlib

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import UserContext, get_current_user
from app.database import Base, get_db
from app.models import Project, ProjectMember
from app.protein_structure import ProteinStructureStudy, compare_chains, model_coordinates, parse_pdb, public_structure, router, search_uniprot, structure_summary


def pdb_bytes(transform=lambda x, y, z: (x, y, z), confidence=82.0):
    lines = []
    for index in range(1, 21):
        x, y, z = transform(float(index), float((index * index) % 7), float(index % 3))
        lines.append(
            f"ATOM  {index:5d}  CA  ALA A{index:4d}    {x:8.3f}{y:8.3f}{z:8.3f}  1.00{confidence:6.2f}           C"
        )
    return ("\n".join(lines) + "\nEND\n").encode()


def test_pdb_summary_confidence_and_rigid_alignment():
    model = structure_summary(pdb_bytes(), "alphafold", "manual", "model.pdb", {})
    reference = structure_summary(
        pdb_bytes(lambda x, y, z: (-y + 10, x + 4, z - 2), confidence=20),
        "pdb_experimental", "1ABC", "1ABC.pdb", {},
    )
    assert model["residue_count"] == 20
    assert model["confidence"]["mean"] == 82
    assert reference["confidence"] is None
    public = public_structure(model)
    assert "_chains" not in public
    assert public["viewer"]["kind"] == "ca_trace_3d"
    assert public["viewer"]["total_residues"] == 20
    assert len(public["viewer"]["chains"]["A"][0]) == 4
    result = compare_chains(model["_chains"], reference["_chains"])
    assert result["sequence_identity"] == 1
    assert result["ca_rmsd_angstrom"] == pytest.approx(0, abs=0.001)
    assert result["sequence_registered_tm_like"] == 1


def test_rejects_non_structure_and_too_short_structure():
    with pytest.raises(HTTPException) as exc:
        parse_pdb(b"not a pdb")
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException):
        parse_pdb(b"\n".join(pdb_bytes().splitlines()[:5]))


def test_model_coordinates_recovers_and_caches_allowed_upstream(tmp_path, monkeypatch):
    raw = pdb_bytes()
    calls = []
    monkeypatch.setattr("app.protein_structure.coordinate_root", lambda: tmp_path / "uploads")
    monkeypatch.setattr("app.protein_structure.fetch_bytes", lambda url: calls.append(url) or raw)
    study = ProteinStructureStudy(id="study", project_id="project", data={"model": {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "provenance": {"coordinate_url": "https://alphafold.ebi.ac.uk/files/model.pdb"},
    }})
    assert model_coordinates(study) == raw
    assert model_coordinates(study) == raw
    assert calls == ["https://alphafold.ebi.ac.uk/files/model.pdb"]


def test_uniprot_search_parses_real_fields_and_escapes_query(monkeypatch):
    payload = (
        "Entry\tEntry Name\tReviewed\tProtein names\tGene Names\tOrganism\tLength\tPDB\n"
        "P00533\tEGFR_HUMAN\treviewed\tEpidermal growth factor receptor\tEGFR ERBB\tHomo sapiens (Human)\t1210\t1M17;2ITX;\n"
        "C9JYS6\tC9JYS6_HUMAN\tunreviewed\tReceptor protein-tyrosine kinase\tEGFR\tHomo sapiens (Human)\t1157\t\n"
    )

    class Response:
        content = payload.encode()
        text = payload
        headers = {"x-total-results": "2"}

        @staticmethod
        def raise_for_status():
            return None

    captured = {}

    def fake_get(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr("app.protein_structure.httpx.get", fake_get)
    result = search_uniprot('EGFR"', "gene", 9606, True, 10)
    assert captured["url"] == "https://rest.uniprot.org/uniprotkb/search"
    assert 'gene_exact:"EGFR\\\""' in captured["params"]["query"]
    assert "organism_id:9606" in captured["params"]["query"]
    assert "reviewed:true" in captured["params"]["query"]
    assert result["total"] == 2
    assert result["items"][0]["accession"] == "P00533"
    assert result["items"][0]["pdb_count"] == 2
    assert result["items"][0]["reviewed"] is True


def test_project_permissions_revision_persistence_and_evidence_separation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.protein_structure.coordinate_root", lambda: tmp_path / "uploads")
    engine = create_engine(f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Project(id="p", name="P", disease="D", owner_id="owner"))
        db.add(ProjectMember(project_id="p", user_id="viewer", role="viewer"))
        db.commit()

    app = FastAPI()
    app.include_router(router)

    def connection():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = connection
    app.dependency_overrides[get_current_user] = lambda: UserContext("owner", "owner", "researcher")
    client = TestClient(app)
    base = "/api/v1/drug-discovery/projects/p/protein-structures"
    created = client.post(base + "/studies", json={
        "name": "EGFR 结构复核", "target": "EGFR", "uniprot_accession": "P00533",
        "sequence": "A" * 20, "assembly": "monomer", "research_question": "与实验结构是否一致？",
    })
    assert created.status_code == 200, created.text
    study = created.json()
    assert study["revision"] == 1 and study["model"] is None
    assert study["prediction_task"]["status"] == "not_configured"

    url = base + "/studies/" + study["id"]
    uploaded = client.post(
        url + "/model/upload?revision=1&source=alphafold",
        files={"file": ("model.pdb", pdb_bytes(), "chemical/x-pdb")},
    )
    assert uploaded.status_code == 200, uploaded.text
    study = uploaded.json()
    assert study["revision"] == 2 and study["model"]["confidence"]["metric"] == "pLDDT"
    assert "_chains" not in study["model"]
    assert study["model"]["viewer"]["sampled_residues"] == 20
    coordinates = client.get(url + "/model/coordinates")
    assert coordinates.status_code == 200
    assert coordinates.content == pdb_bytes()
    assert coordinates.headers["content-type"].startswith("chemical/x-pdb")

    compared = client.post(
        url + "/references/upload?revision=2&reference_kind=experimental&chain=A",
        files={"file": ("reference.pdb", pdb_bytes(lambda x, y, z: (-y + 10, x + 4, z - 2)), "chemical/x-pdb")},
    )
    assert compared.status_code == 200, compared.text
    study = compared.json()
    comparison = study["comparisons"][0]
    assert comparison["reference"]["confidence"] is None
    assert comparison["metrics"]["ca_rmsd_angstrom"] == pytest.approx(0, abs=0.001)
    assert "不能证明功能或疗效" in comparison["metrics"]["metric_boundary"]

    stale = client.post(url + "/validations", json={
        "revision": 2, "method": "binding", "status": "planned", "criterion": "Kd < 10 nM",
        "conclusion": "pending",
    })
    assert stale.status_code == 409
    saved = client.post(url + "/validations", json={
        "revision": 3, "method": "binding", "status": "completed", "criterion": "Kd < 10 nM",
        "result": "SPR Kd = 7.8 nM", "conclusion": "supports", "source": "实验批次 SPR-01",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["validations"][0]["result"] == "SPR Kd = 7.8 nM"

    app.dependency_overrides[get_current_user] = lambda: UserContext("viewer", "viewer", "researcher")
    assert client.get(base + "/studies").status_code == 200
    # 要求 27：权限全面放开，只读角色也能写
    allowed = client.post(url + "/validations", json={
        "revision": 4, "method": "activity", "status": "planned", "criterion": "预设活性门限",
    })
    assert allowed.status_code == 200
