from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest

from app.auth import UserContext, get_current_user
from app.database import Base, get_db
from app.models import Project
from app.process_patent_extraction import _google_paragraphs, extract_examples, normalize_publication, uploaded_paragraphs
from app.process_retrosynthesis import router


PATENT_HTML = '''<html><section itemprop="description"><p id="p1"><span class="google-src-text">实施例1</span>Example 1</p>
<p id="p2"><span class="google-src-text">(1)制备水杨酸：加入苯酚，反应后得到水杨酸粗品。</span>translated</p>
<p id="p3"><span class="google-src-text">重结晶得到水杨酸(收率95.2%，纯度99.3%)。</span>translated</p>
<p id="p4"><span class="google-src-text">(2)制备阿司匹林：将水杨酸和乙酸酐反应得到阿司匹林粗品。</span>translated</p>
<p id="p5"><span class="google-src-text">过滤得到阿司匹林(收率98.1%，纯度99.5%)。</span>translated</p>
</section></html>'''.encode("utf-8")


def test_paragraph_evidence_yields_and_precursor_overlap():
    paragraphs = _google_paragraphs(PATENT_HTML)
    result = extract_examples(paragraphs, {"kind": "mirror", "url": "https://patents.google.com/patent/CN116354819A/en",
                                           "publication": "CN116354819A", "original": False}, "50-78-2")
    assert len(result["examples"]) == 1
    steps = result["examples"][0]["steps"]
    assert len(steps) == 4
    assert [(step["yield_pct"], step["purity_pct"]) for step in steps] == [(None, None), (95.2, 99.3), (None, None), (98.1, 99.5)]
    assert steps[1]["source_url"].endswith("#p3")
    assert "translated" not in steps[1]["evidence_excerpt"]
    assert result["comparison"][0]["matched_count"] == 1
    assert "乙酸" not in {item["name"] for item in result["molecules"]}  # 乙酸酐 != 乙酸


def test_google_patents_description_divs_are_read_with_locators():
    raw = b'<section itemprop="description"><div id="p0215" class="description-paragraph">Step-1: A <sub>2</sub> added</div><div id="p0216" class="description-paragraph">Product 57%</div></section>'
    assert _google_paragraphs(raw) == [("Step-1: A 2 added", "p0215"), ("Product 57%", "p0216")]


def test_manual_structure_is_provenanced_and_invalid_inputs_rejected():
    paragraphs = [("实施例1", "p1"), ("加入中间体A，反应得到阿司匹林。", "p2")]
    result = extract_examples(paragraphs, {"kind": "upload", "url": None, "publication": None, "original": True},
                              "50-78-2", {"中间体A": "O=C(O)c1ccccc1O"})
    item = next(item for item in result["molecules"] if item["name"] == "中间体A")
    assert "用户手动补录" in item["identity_source"]
    assert result["comparison"][0]["matched_count"] == 1
    with pytest.raises(Exception):
        normalize_publication("http://127.0.0.1/admin")
    with pytest.raises(Exception):
        uploaded_paragraphs(b"not a pdf", "patent.pdf")


def test_authorized_read_and_upload_endpoints(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/patent.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Project(id="p", name="P", disease="D", owner_id="owner"))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    def connection():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = connection
    app.dependency_overrides[get_current_user] = lambda: UserContext("owner", "owner", "researcher")
    monkeypatch.setattr("app.process_patent_extraction._fetch", lambda url: PATENT_HTML)
    client = TestClient(app)
    base = "/api/v1/drug-discovery/projects/p/process/retrosynthesis/patent"
    read = client.post(base + "/read", json={"publication": "CN116354819A", "target": "50-78-2"})
    assert read.status_code == 200 and read.json()["examples"][0]["steps"]
    uploaded = client.post(base + "/upload", data={"target": "50-78-2"},
                           files={"file": ("example.txt", "实施例1\n\n在反应器中加入苯酚，控制温度并充分搅拌，反应后得到水杨酸，收率95%。".encode(), "text/plain")})
    assert uploaded.status_code == 200 and uploaded.json()["examples"][0]["steps"][0]["yield_pct"] == 95
    denied = client.post(base.replace("/projects/p/", "/projects/unknown/") + "/read", json={"publication": "CN116354819A"})
    assert denied.status_code in (403, 404)


def test_pdf_text_extraction_has_page_locator():
    from io import BytesIO
    from reportlab.pdfgen import canvas
    output = BytesIO()
    page = canvas.Canvas(output)
    page.drawString(50, 750, "Example 1")
    page.drawString(50, 725, "Salicylic acid was added and stirred to obtain aspirin with yield 87%.")
    page.save()
    paragraphs, kind = uploaded_paragraphs(output.getvalue(), "example.pdf")
    assert "PDF" in kind and paragraphs[0][1] == "第 1 页"
    result = extract_examples(paragraphs, {"kind": kind, "url": None, "publication": None, "original": True})
    assert result["examples"][0]["steps"][0]["yield_pct"] == 87
