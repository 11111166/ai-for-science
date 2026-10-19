"""Bacterial compound exploration: curated evidence, grouping, generation gates.

The suite stays offline.  ChEMBL is replaced with ``httpx.MockTransport`` and
the CARD artifact is a small hand-built fixture, so every assertion is about
this module's behaviour rather than about CARD availability.
"""

import json
import sqlite3

import pytest

from app import bacterial_compound as bc


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def artifact_payload() -> dict:
    return {
        "schema_version": "bacterial-card-resistance-v1",
        "generated_at": "2026-10-14T00:00:00+00:00",
        "organism": "Staphylococcus aureus NCTC 8325",
        "chembl_db_version": "ChEMBL_37",
        "chembl_release_date": "2026-05-01",
        "counts": {
            "organisms": 2, "organisms_indexed": 2, "card_protein_entries": 6337,
            "compounds_curated": 2, "compounds_resolved": 2, "compounds_unresolved": 0,
            "gene_compound_links": 3, "gene_compound_links_resolved_only": 3,
            "targets_with_evidence": 2, "targets_resistance_determinant": 2,
            "hits_below_threshold": 5, "hits_without_compound_mapping": 1,
        },
        "per_organism": {
            "staphylococcus-aureus": {"status": "indexed", "hits": 2, "targets": 2},
            "escherichia-coli": {"status": "indexed", "hits": 1, "targets": 1},
        },
        "organisms": [
            {"id": "staphylococcus-aureus", "slug": "staphylococcus-aureus",
             "species": "Staphylococcus aureus", "strain": "NCTC 8325",
             "organism": "Staphylococcus aureus NCTC 8325",
             "aliases": ["S. aureus"], "coverage": "full_curated_case"},
            {"id": "escherichia-coli", "slug": "escherichia-coli",
             "species": "Escherichia coli", "strain": "K-12",
             "organism": "Escherichia coli K-12",
             "aliases": ["E. coli"], "coverage": "full_curated_case"},
        ],
        "compounds": [
            {"curated_name": "ciprofloxacin", "class": "antibacterial_drug",
             "note": "氟喹诺酮", "chembl_id": "CHEMBL8", "pref_name": "CIPROFLOXACIN",
             "canonical_smiles": "O=C(O)c1cn(C2CC2)c2cc(N3CCNCC3)c(F)cc2c1=O",
             "inchi_key": "MYSWGUAQZAJSOK-UHFFFAOYSA-N", "status": "resolved"},
            {"curated_name": "ampicillin", "class": "antibacterial_drug",
             "note": "青霉素类", "chembl_id": "CHEMBL174", "pref_name": "AMPICILLIN",
             "canonical_smiles": "CC1(C)SC2C(NC(=O)C(N)c3ccccc3)C(=O)N2C1C(=O)O",
             "inchi_key": "LSQZJLSUYDQPKJ-NJBDSQKTSA-N", "status": "resolved"},
        ],
        "unresolved_compounds": [],
        "targets": [
            {"uniprot": "Q2G0A2", "gene_symbol": "norA", "protein_name": "Quinolone resistance protein NorA",
             "organism_slug": "staphylococcus-aureus", "organism": "Staphylococcus aureus NCTC 8325",
             "compound_count": 1, "sensitizing_compounds": [], "resistance_compounds": ["ciprofloxacin"]},
            {"uniprot": "P0AE06", "gene_symbol": "acrA", "protein_name": "Multidrug efflux pump subunit AcrA",
             "organism_slug": "escherichia-coli", "organism": "Escherichia coli K-12",
             "compound_count": 1, "sensitizing_compounds": [], "resistance_compounds": ["ampicillin"]},
        ],
        "links": [
            {"uniprot": "Q2G0A2", "gene_symbol": "norA", "organism_slug": "staphylococcus-aureus",
             "curated_name": "ciprofloxacin", "direction": "resistance_determinant",
             "phenotype_text": "序列同源命中 CARD 耐药决定簇 ARO:3004667（major facilitator superfamily）",
             "experiment_type": "序列同源比对（DIAMOND blastp vs CARD 同源模型）",
             "mutant_type": "identity 99.7% / coverage 100.0%", "reference": "CARD ARO:3004667",
             "evidence_source": "CARD", "drug_class": "fluoroquinolone antibiotic",
             "mechanism": "antibiotic efflux", "aro_accession": "ARO:3004667"},
            {"uniprot": "P0AE06", "gene_symbol": "acrA", "organism_slug": "escherichia-coli",
             "curated_name": "ampicillin", "direction": "resistance_determinant",
             "phenotype_text": "序列同源命中 CARD 耐药决定簇 ARO:3004043（RND）",
             "experiment_type": "序列同源比对（DIAMOND blastp vs CARD 同源模型）",
             "mutant_type": "identity 100.0% / coverage 100.0%", "reference": "CARD ARO:3004043",
             "evidence_source": "CARD", "drug_class": "penicillin beta-lactam",
             "mechanism": "antibiotic efflux", "aro_accession": "ARO:3004043"},
        ],
    }


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    path = tmp_path / "derived" / "bacterial-card-evidence.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact_payload()), encoding="utf-8")
    monkeypatch.setattr(bc, "BACTERIAL_TARGET_DATA_DIR", tmp_path)
    monkeypatch.setattr(bc, "BACTERIAL_DISCOVERY_DATA_DIR", tmp_path)
    bc._load_artifact_cached.cache_clear()
    yield artifact_payload()
    bc._load_artifact_cached.cache_clear()


# --------------------------------------------------------------------------
# catalog
# --------------------------------------------------------------------------

def test_catalog_reports_unconfigured_without_a_fallback_list(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "BACTERIAL_TARGET_DATA_DIR", tmp_path)
    bc._load_artifact_cached.cache_clear()
    payload = bc.target_catalog()
    assert payload["configured"] is False
    assert payload["targets"] == []
    assert payload["compounds"] == []


def test_catalog_exposes_curated_targets_and_compound_classes(artifact):
    payload = bc.target_catalog()
    assert payload["configured"] is True
    assert {item["curated_name"] for item in payload["compounds"]} == {"ciprofloxacin", "ampicillin"}
    assert {item["gene_symbol"] for item in payload["targets"]} == {"norA", "acrA"}
    assert [item["slug"] for item in payload["organisms"]] == ["staphylococcus-aureus", "escherichia-coli"]


def test_bacterium_catalog_keeps_each_strain_separate(artifact):
    catalog = bc._bacterium_catalog()
    organisms = {item["species"]: item for item in catalog["organisms"]}
    # E. coli's acrA must never be attributed to S. aureus.
    assert organisms["Staphylococcus aureus"]["associated_target_count"] == 1
    assert [item["name"] for item in organisms["Staphylococcus aureus"]["known_compounds"]] == ["ciprofloxacin"]
    assert organisms["Escherichia coli"]["associated_target_count"] == 1
    assert [item["name"] for item in organisms["Escherichia coli"]["known_compounds"]] == ["ampicillin"]


def test_first_organism_is_the_one_click_case(artifact):
    catalog = bc._bacterium_catalog()
    assert catalog["organisms"][0]["organism"] == "Staphylococcus aureus NCTC 8325"
    assert catalog["organisms"][0]["coverage"] == "full_curated_case"


# --------------------------------------------------------------------------
# evidence semantics
# --------------------------------------------------------------------------

def test_genetic_evidence_keeps_direction_and_provenance(artifact):
    rows = bc._genetic_evidence(artifact, "Q2G0A2")
    assert rows and all(row["direction"] == "resistance_determinant" for row in rows)
    assert rows[0]["evidence_source"] == "CARD"
    assert rows[0]["aro_accession"] == "ARO:3004667"


def test_exploration_summary_states_what_was_dropped(artifact):
    summary = bc._exploration_summary(artifact)
    joined = " ".join(summary["warnings"])
    assert "5 条 CARD 同源命中" in joined
    assert "尚未映射到具体化合物" in joined


def test_catalog_degrades_when_the_data_dir_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "BACTERIAL_TARGET_DATA_DIR", tmp_path / "missing")
    monkeypatch.setattr(bc, "BACTERIAL_DISCOVERY_DATA_DIR", tmp_path / "missing")
    bc._load_artifact_cached.cache_clear()
    assert bc.target_catalog()["configured"] is False
    assert bc._bacterium_catalog()["configured"] is False
    assert bc._raw_source_status()["configured"] is False


def test_catalog_works_when_the_data_dir_is_a_symlink(tmp_path, monkeypatch):
    release = tmp_path / "releases" / "20261014-public-v1"
    (release / "derived").mkdir(parents=True)
    (release / "derived" / "bacterial-card-evidence.json").write_text(
        json.dumps(artifact_payload()), encoding="utf-8")
    link = tmp_path / "current"
    link.symlink_to(release)
    monkeypatch.setattr(bc, "BACTERIAL_TARGET_DATA_DIR", link)
    monkeypatch.setattr(bc, "BACTERIAL_DISCOVERY_DATA_DIR", link)
    bc._load_artifact_cached.cache_clear()
    payload = bc.target_catalog()
    # The release name is reported, not the literal "current" symlink name.
    assert payload["bundle_version"] == "20261014-public-v1"


# --------------------------------------------------------------------------
# reverse search
# --------------------------------------------------------------------------

def test_compound_reverse_search_is_case_insensitive_and_does_not_overclaim(artifact, monkeypatch):
    monkeypatch.setattr(bc, "_chebi_matches", lambda query, limit=20: [])
    monkeypatch.setattr(bc, "_indexed_activity_search", lambda query, limit=20: ([], []))
    monkeypatch.setattr(bc, "_phi_compound_groups", lambda terms, limit=5000: [])
    payload = bc.search_bacterial_compound(query="CIPROFLOXACIN", user=None)
    assert payload["configured"] is True
    assert len(payload["matches"]) == 1
    assert len(payload["bacteria"]) == 1
    entry = payload["bacteria"][0]
    assert entry["species"] == "Staphylococcus aureus"
    # The relationship is a curated homology association, never a bound target.
    assert all(row["relationship_label"].startswith("CARD 序列同源耐药关联") for row in entry["targets"])
    assert "不等同于" in payload["boundary"] or "不自动等于" in payload["boundary"]


def test_compound_reverse_search_returns_empty_without_fabrication(artifact, monkeypatch):
    monkeypatch.setattr(bc, "_chebi_matches", lambda query, limit=20: [])
    monkeypatch.setattr(bc, "_indexed_activity_search", lambda query, limit=20: ([], []))
    monkeypatch.setattr(bc, "_phi_compound_groups", lambda terms, limit=5000: [])
    payload = bc.search_bacterial_compound(query="not-a-real-compound-xyz", user=None)
    assert payload["matches"] == []
    assert payload["bacteria"] == []


def test_reverse_search_requires_two_characters():
    with pytest.raises(Exception):
        bc.search_bacterial_compound(query="a", user=None)


# --------------------------------------------------------------------------
# data dir independence for the discovery index
# --------------------------------------------------------------------------

def test_discovery_index_absence_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "BACTERIAL_DISCOVERY_DATA_DIR", tmp_path / "missing")
    assert bc._discovery_index_path().name == "bacterial-discovery.sqlite3"
    assert bc._open_discovery_index() is None


def test_mibig_and_host_homology_layers_are_queryable(monkeypatch, tmp_path):
    derived = tmp_path / "derived"
    derived.mkdir()
    database = derived / "bacterial-discovery.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript("""
      CREATE TABLE mibig_clusters (
        accession TEXT PRIMARY KEY, organism_name TEXT, organism_norm TEXT, ncbi_tax_id TEXT,
        biosyn_classes TEXT, evidence TEXT, completeness TEXT, gene_count INTEGER, compound_count INTEGER
      );
      CREATE TABLE mibig_compounds (
        id INTEGER PRIMARY KEY, accession TEXT, compound_name TEXT, compound_norm TEXT,
        canonical_smiles TEXT, molecular_formula TEXT, molecular_weight REAL,
        database_ids TEXT, activities TEXT, targets TEXT
      );
      CREATE TABLE host_homology (
        uniprot TEXT PRIMARY KEY, human_uniprot TEXT, percent_identity REAL,
        alignment_length INTEGER, query_length INTEGER, subject_length INTEGER,
        query_coverage REAL, subject_coverage REAL, similarity_score REAL,
        evalue REAL, bitscore REAL, method TEXT
      );
    """)
    connection.execute(
        "INSERT INTO mibig_clusters VALUES (?,?,?,?,?,?,?,?,?)",
        ("BGC0000001", "Escherichia coli K-12", "escherichia coli k-12", "83333",
         '["Polyketide"]', '["Knock-out studies"]', "complete", 4, 1),
    )
    connection.execute(
        "INSERT INTO mibig_compounds VALUES (NULL,?,?,?,?,?,?,?,?,?)",
        ("BGC0000001", "test antibiotic", "test antibiotic", "CCO", "C2H6O", 46.069,
         '["chembl:CHEMBL1","pubchem:702"]', '["antibacterial"]', '["gyrA"]'),
    )
    connection.execute(
        "INSERT INTO host_homology VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("P0A0V2", "P12345", 40.0, 80, 100, 120, 80.0, 66.67, 0.2667, 1e-20, 100.0, "DIAMOND"),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(bc, "BACTERIAL_DISCOVERY_DATA_DIR", tmp_path)

    organisms = bc._mibig_species_catalog("Escherichia coli")
    assert organisms[0]["known_compounds"][0]["chembl_id"] == "CHEMBL1"
    assert organisms[0]["targets"][0]["gene_symbol"] == "gyrA"
    matches, bacteria = bc._mibig_compound_search("test antibiotic")
    assert matches[0]["source"] == "MIBiG 3.1"
    assert bacteria[0]["targets"][0]["relationship"].startswith("mibig")
    homology = bc._host_homology_map(["P0A0V2"])
    assert homology["P0A0V2"]["human_similarity"] == pytest.approx(0.2667)
