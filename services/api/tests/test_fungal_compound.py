"""Fungal compound exploration: curated evidence, retrieval shape, generation gates.

The suite stays offline.  ChEMBL is replaced with ``httpx.MockTransport`` and
the CGD artifact is a small hand-built fixture, so every assertion is about
this module's behaviour rather than about EBI availability.
"""

import hashlib
import json
import sqlite3

import httpx
import pytest

from app import fungal_compound as fc
from app.chembl_runtime import ChEMBLRuntimeAdapter


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def artifact_payload() -> dict:
    return {
        "schema_version": "fungal-cgd-chemogenomics-v1",
        "generated_at": "2026-09-28T00:00:00+00:00",
        "organism": "Candida albicans SC5314",
        "chembl_db_version": "ChEMBL_37",
        "chembl_release_date": "2026-05-01",
        "counts": {
            "compounds_curated": 3,
            "compounds_resolved": 2,
            "compounds_unresolved": 1,
            "gene_compound_links": 3,
            "targets_with_evidence": 1,
            "targets_sensitizing": 1,
            "cgd_links_dropped_unresolved_compound": 1,
            "cgd_rows_without_uniprot_mapping": 2,
        },
        "compounds": [
            {
                "cgd_name": "fluconazole", "class": "antifungal_drug",
                "note": "三唑类", "chembl_override": "CHEMBL106", "status": "resolved",
                "chembl_id": "CHEMBL106", "pref_name": "FLUCONAZOLE",
                "canonical_smiles": "OC(Cn1cncn1)(Cn1cncn1)c1ccc(F)cc1F",
                "inchi_key": "RFHAOTPXVQNOHP-UHFFFAOYSA-N", "molecule_type": "Small molecule",
                "max_phase": 4, "first_approval": 1988, "resolved_by": "curated_override",
            },
            {
                "cgd_name": "sirolimus", "class": "immunosuppressant",
                "note": "mTOR 抑制剂", "chembl_override": "CHEMBL413", "status": "resolved",
                "chembl_id": "CHEMBL413", "pref_name": "SIROLIMUS",
                "canonical_smiles": "CO[C@H]1C[C@@H]2CC[C@@H](C)[C@@](O)(O2)C(=O)C(=O)N2CCCC[C@H]2C(=O)O1",
                "inchi_key": "QFJCIRLUMZQUOT-HPLJOQBZSA-N", "molecule_type": "Small molecule",
                "max_phase": 4, "first_approval": 1999, "resolved_by": "curated_override",
            },
        ],
        "unresolved_compounds": [
            {"cgd_name": "nystatin", "class": "antifungal_drug", "status": "no_structure", "chembl_id": "CHEMBL229699"},
        ],
        "targets": [
            {
                "uniprot": "P10613", "gene_symbol": "ERG11",
                "protein_name": "Lanosterol 14-alpha demethylase",
                "compound_count": 2,
                "sensitizing_compounds": ["fluconazole"],
                "resistance_compounds": ["sirolimus"],
            },
        ],
        "links": [
            {
                "uniprot": "P10613", "gene_symbol": "ERG11", "cgd_name": "fluconazole",
                "direction": "sensitizing", "phenotype_text": "resistance to chemicals: decreased",
                "experiment_type": "homozygous diploid, classical genetics", "mutant_type": "Deletion",
                "reference": "REF1",
            },
            {
                "uniprot": "P10613", "gene_symbol": "ERG11", "cgd_name": "fluconazole",
                "direction": "resistance", "phenotype_text": "resistance to chemicals: increased",
                "experiment_type": "heterozygous diploid, classical genetics", "mutant_type": "Deletion",
                "reference": "REF2",
            },
            {
                "uniprot": "P10613", "gene_symbol": "ERG11", "cgd_name": "sirolimus",
                "direction": "resistance", "phenotype_text": "resistance to chemicals: increased",
                "experiment_type": "homozygous diploid, classical genetics", "mutant_type": "Deletion",
                "reference": "REF3",
            },
        ],
    }


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    path = tmp_path / "derived" / "fungal-cgd-evidence.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact_payload()), encoding="utf-8")
    monkeypatch.setattr(fc, "FUNGAL_TARGET_DATA_DIR", tmp_path)
    fc._load_artifact_cached.cache_clear()
    yield artifact_payload()
    fc._load_artifact_cached.cache_clear()


def chembl_client(handler) -> fc.FungalChEMBL:
    return fc.FungalChEMBL(adapter=ChEMBLRuntimeAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)), max_workers=1))


@pytest.fixture
def discovery_index(tmp_path, monkeypatch):
    derived = tmp_path / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    database = derived / "fungal-discovery.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE phi_species (
          tax_id TEXT PRIMARY KEY, species TEXT NOT NULL, species_norm TEXT NOT NULL,
          record_count INTEGER NOT NULL, gene_count INTEGER NOT NULL,
          protein_count INTEGER NOT NULL, strain_count INTEGER NOT NULL,
          compound_count INTEGER NOT NULL
        );
        CREATE TABLE phi_interactions (
          id INTEGER PRIMARY KEY, record_id TEXT, tax_id TEXT NOT NULL,
          species TEXT NOT NULL, strain TEXT, protein_id TEXT, gene_locus_id TEXT,
          gene_name TEXT, phenotype TEXT, essential_gene TEXT, anti_infective TEXT,
          compound TEXT, chemical_accession TEXT, target_site TEXT, mode_of_action TEXT,
          experimental_evidence TEXT, literature_id TEXT, doi TEXT, host_species TEXT,
          function_text TEXT, compound_norm TEXT, anti_infective_norm TEXT,
          chemical_accession_norm TEXT
        );
        CREATE TABLE chebi_compounds (
          chebi_id TEXT PRIMARY KEY, name TEXT NOT NULL, name_norm TEXT NOT NULL,
          star INTEGER, definition TEXT, formula TEXT, mass REAL, smiles TEXT,
          inchi TEXT, inchikey TEXT, iupac_name TEXT, synonyms TEXT,
          pubchem_ids TEXT, cas_ids TEXT
        );
        CREATE TABLE chebi_terms (
          term_norm TEXT NOT NULL, chebi_id TEXT NOT NULL, term_type TEXT NOT NULL,
          PRIMARY KEY(term_norm, chebi_id, term_type)
        );
    """)
    connection.execute(
        "INSERT INTO phi_species VALUES (?,?,?,?,?,?,?,?)",
        ("746128", "Aspergillus fumigatus", "aspergillus fumigatus", 1, 1, 1, 1, 1),
    )
    connection.execute(
        """INSERT INTO phi_interactions VALUES (
          1,'PHI:1','746128','Aspergillus fumigatus','Af293','Q4WNT5','AFUA_4G06890','CYP51A',
          'reduced virulence','','fluconazole','','CHEBI:46081','','','gene disruption',
          'PMID:1','10.1/example','Homo sapiens','lanosterol 14-alpha demethylase',
          '','fluconazole','chebi:46081')"""
    )
    connection.execute(
        """INSERT INTO chebi_compounds VALUES (
          'CHEBI:46081','fluconazole','fluconazole',3,'antifungal','C13H12F2N6O',306.27,
          'OC(Cn1cncn1)(Cn1cncn1)c1ccc(F)cc1F','','RFHAOTPXVQNOHP-UHFFFAOYSA-N',
          'fluconazole','[]','','')"""
    )
    connection.execute("INSERT INTO chebi_terms VALUES ('fluconazole','CHEBI:46081','preferred_name')")
    connection.commit()
    connection.close()
    (derived / "index-manifest.json").write_text(json.dumps({
        "counts": {"fungal_species": 1, "phi_interactions": 1, "chebi_compounds": 1},
        "sources": [
            {"id": "phi_base", "status": "indexed", "records": 1},
            {"id": "chebi", "status": "indexed", "records": 1},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(fc, "FUNGAL_DISCOVERY_DATA_DIR", tmp_path)
    return tmp_path


# --------------------------------------------------------------------------
# artifact + target catalog
# --------------------------------------------------------------------------

def test_catalog_reports_unconfigured_without_a_fallback_list(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "FUNGAL_TARGET_DATA_DIR", tmp_path)
    fc._load_artifact_cached.cache_clear()
    catalog = fc.target_catalog()
    assert catalog["configured"] is False
    assert catalog["targets"] == []
    # A built-in demonstration list must never stand in for missing evidence.
    assert "message" in catalog


def test_catalog_exposes_curated_targets_and_compound_classes(artifact):
    catalog = fc.target_catalog()
    assert catalog["configured"] is True
    assert [item["uniprot"] for item in catalog["targets"]] == ["P10613"]
    classes = {item["cgd_name"]: item["class"] for item in catalog["compounds"]}
    assert classes["fluconazole"] == "antifungal_drug"
    assert classes["sirolimus"] == "immunosuppressant"


def test_resolve_target_is_exact_then_reports_ambiguity(artifact):
    exact = fc.resolve_fungal_target(fc.ResolveRequest(identifier="P10613"), user=None)
    assert exact["status"] == "resolved"
    assert exact["matched_by"] == "uniprot"

    by_gene = fc.resolve_fungal_target(fc.ResolveRequest(identifier="erg11"), user=None)
    assert by_gene["status"] == "resolved"
    assert by_gene["matched_by"] == "gene_symbol"

    missing = fc.resolve_fungal_target(fc.ResolveRequest(identifier="NOTAPROTEIN"), user=None)
    assert missing["status"] == "not_found"
    assert missing["candidates"] == []


def test_fungus_catalog_separates_curated_association_from_direct_binding(artifact, tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "FUNGAL_DISCOVERY_DATA_DIR", tmp_path / "raw-bundle")
    payload = fc.get_fungal_discovery_organisms(q="SC5314", user=None)
    assert payload["configured"] is True
    assert len(payload["organisms"]) == 1
    organism = payload["organisms"][0]
    assert organism["organism"] == "Candida albicans SC5314"
    fluconazole = next(item for item in organism["known_compounds"] if item["name"] == "fluconazole")
    assert fluconazole["evidence_kind"] == "curated_chemogenomic_association"
    assert fluconazole["target_count"] == 1
    assert "直接结合" in payload["boundary"]


def test_compound_reverse_search_is_case_insensitive_and_does_not_overclaim(artifact, tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "FUNGAL_DISCOVERY_DATA_DIR", tmp_path / "raw-bundle")
    payload = fc.search_fungal_compound(query="FLUCONAZOLE", user=None)
    assert [item["chembl_id"] for item in payload["matches"]] == ["CHEMBL106"]
    assert payload["fungi"][0]["organism"] == "Candida albicans SC5314"
    assert payload["fungi"][0]["target_count"] == 1
    assert {item["gene_symbol"] for item in payload["fungi"][0]["targets"]} == {"ERG11"}
    assert all(item["relationship"] == "chemogenomic_association" for item in payload["fungi"][0]["targets"])
    assert all("非直接结合" in item["relationship_label"] for item in payload["fungi"][0]["targets"])


def test_compound_reverse_search_returns_empty_without_fabrication(artifact):
    payload = fc.search_fungal_compound(query="definitely-not-a-compound", user=None)
    assert payload["matches"] == []
    assert payload["fungi"] == []


def test_phi_index_adds_searchable_species_without_promoting_it_to_full_case(artifact, discovery_index):
    payload = fc.get_fungal_discovery_organisms(q="Aspergillus fumigatus", user=None)
    indexed = next(item for item in payload["organisms"] if item["coverage"] == "phi_base_phenotype_evidence")
    assert indexed["species"] == "Aspergillus fumigatus"
    assert indexed["strain"] == "Af293"
    assert indexed["known_compounds"][0]["name"] == "fluconazole"
    assert indexed["known_compounds"][0]["cas_numbers"] == []
    assert indexed["known_compounds"][0]["evidence_records"][0]["source"] == "PHI-base"
    assert indexed["targets"][0]["gene_symbol"] == "CYP51A"


def test_chebi_identity_and_phi_phenotype_are_joined_without_direct_binding_claim(artifact, discovery_index):
    payload = fc.search_fungal_compound(query="CHEBI:46081", user=None)
    assert payload["matches"][0]["chebi_id"] == "CHEBI:46081"
    assert payload["matches"][0]["source"] == "ChEBI"
    association = payload["fungi"][0]["targets"][0]
    assert association["gene_symbol"] == "CYP51A"
    assert association["relationship"] == "phi_base_antiinfective_phenotype"
    assert "非直接结合" in association["relationship_label"]


def test_local_activity_index_adds_measured_fungal_target_with_evidence_tier(artifact, discovery_index):
    database = discovery_index / "derived" / "fungal-discovery.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE activity_compounds (
          compound_key TEXT PRIMARY KEY, preferred_name TEXT, name_norm TEXT, aliases_norm TEXT,
          chembl_id TEXT, bindingdb_monomer_id TEXT, canonical_smiles TEXT, standard_inchi TEXT,
          standard_inchi_key TEXT, chebi_id TEXT, pubchem_cids TEXT, molecular_formula TEXT,
          molecular_weight REAL, alogp REAL, psa REAL, hbd INTEGER, hba INTEGER, rtb INTEGER,
          ro5_violations INTEGER, source_list TEXT, evidence_count INTEGER
        );
        CREATE TABLE activity_targets (
          target_key TEXT PRIMARY KEY, preferred_name TEXT, name_norm TEXT, organism TEXT,
          organism_norm TEXT, tax_id TEXT, uniprot_accessions TEXT, gene_names TEXT,
          target_type TEXT, source_list TEXT, evidence_count INTEGER
        );
        CREATE TABLE fungal_activities (
          id INTEGER PRIMARY KEY, source TEXT, source_record_id TEXT, compound_key TEXT,
          target_key TEXT, assay_id TEXT, assay_type TEXT, standard_type TEXT,
          standard_relation TEXT, standard_value REAL, standard_units TEXT, raw_value TEXT,
          pchembl_value REAL, activity_comment TEXT, assay_description TEXT,
          confidence_score INTEGER, direct_binding INTEGER, document_doi TEXT,
          pubmed_id TEXT, document_year INTEGER, source_url TEXT, classification_basis TEXT
        );
    """)
    connection.execute(
        "INSERT INTO activity_compounds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("CHEMBL106", "FLUCONAZOLE", "fluconazole", "fluconazole | chembl106", "CHEMBL106", None,
         "OC(Cn1cncn1)(Cn1cncn1)c1ccc(F)cc1F", None, "RFHAOTPXVQNOHP-UHFFFAOYSA-N",
         "CHEBI:46081", "[]", "C13H12F2N6O", 306.27, None, None, None, None, None, None,
         '["ChEMBL"]', 1),
    )
    connection.execute(
        "INSERT INTO activity_targets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("CHEMBL_TARGET:1780", "Lanosterol 14-alpha demethylase", "lanosterol 14-alpha demethylase",
         "Candida albicans", "candida albicans", "5476", '["P10613"]', '["ERG11"]',
         "SINGLE PROTEIN", '["ChEMBL"]', 1),
    )
    connection.execute(
        "INSERT INTO fungal_activities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (1, "ChEMBL", "123", "CHEMBL106", "CHEMBL_TARGET:1780", "CHEMBL_A", "binding",
         "IC50", "=", 25.0, "nM", "25", 7.6, None, "binding assay", 9, 1,
         "10.1/example", "12345", 2024, "https://example.test", "Fungi"),
    )
    connection.commit()
    connection.close()

    payload = fc.search_fungal_compound(query="CHEMBL106", user=None)
    fungus = next(item for item in payload["fungi"] if item["organism"] == "Candida albicans")
    evidence = fungus["targets"][0]
    assert evidence["uniprot"] == "P10613"
    assert evidence["relationship"] == "direct_binding_activity"
    assert evidence["evidence_source"] == "ChEMBL"
    assert evidence["direction"] == "IC50 25 nM"

    organism_payload = fc.get_fungal_discovery_organisms(q="Candida albicans", user=None)
    organism = organism_payload["organisms"][0]
    assert "full_curated_case" in organism["coverage"]
    assert "measured_fungal_activity" in organism["coverage"]
    assert any(item["evidence_kind"] == "direct_binding_activity" for item in organism["known_compounds"])


def test_curated_compound_identity_is_enriched_with_exact_chebi_record(artifact, discovery_index):
    payload = fc.search_fungal_compound(query="fluconazole", user=None)
    match = payload["matches"][0]
    assert match["chembl_id"] == "CHEMBL106"
    assert match["chebi_id"] == "CHEBI:46081"
    assert match["source"] == "CGD + ChEMBL + ChEBI"


def test_fungus_compound_detail_keeps_identity_and_every_cgd_evidence_record_together(artifact, discovery_index):
    payload = fc.get_fungal_discovery_organisms(q="SC5314", user=None)
    fluconazole = next(item for item in payload["organisms"][0]["known_compounds"] if item["name"] == "fluconazole")
    assert fluconazole["chembl_id"] == "CHEMBL106"
    assert fluconazole["chebi_id"] == "CHEBI:46081"
    assert fluconazole["molecular_formula"] == "C13H12F2N6O"
    assert fluconazole["canonical_smiles"]
    assert len(fluconazole["evidence_records"]) == 2
    assert {item["source"] for item in fluconazole["evidence_records"]} == {"CGD"}
    assert {item["direction"] for item in fluconazole["evidence_records"]} == {"sensitizing", "resistance"}


def test_local_chebi_structure_endpoint_uses_indexed_smiles(artifact, discovery_index, monkeypatch):
    class Chemistry:
        @staticmethod
        def MolFromSmiles(smiles):
            return object() if smiles else None

    monkeypatch.setattr(fc, "_rdkit", lambda: {"Chem": Chemistry})
    monkeypatch.setattr(fc, "draw_svg", lambda molecule: '<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    response = fc.get_fungal_discovery_compound_structure("CHEBI:46081", user=None)
    assert response.media_type == "image/svg+xml"
    assert b"<svg" in response.body


def test_unknown_candidates_receive_exact_registry_status_without_invented_cas(artifact, discovery_index):
    records = [
        {"inchi_key": "RFHAOTPXVQNOHP-UHFFFAOYSA-N", "canonical_smiles": "known"},
        {"inchi_key": "AAAAAAAAAAAAAA-BBBBBBBBBB-C", "canonical_smiles": "generated"},
    ]
    fc._enrich_registry_records(records)
    assert records[0]["chebi_id"] == "CHEBI:46081"
    assert records[0]["registry_status"] == "exact_chebi_inchikey_match"
    assert records[1]["cas_numbers"] == []
    assert records[1]["cas_status"] == "not_assigned_or_not_found"
    assert "ChEBI exact InChIKey" in records[1]["registry_lookup_scope"]


@pytest.mark.skipif(not fc.capabilities()["rdkit"], reason="RDKit unavailable in this environment")
def test_structure_search_accepts_unknown_structure_and_returns_ranked_evidence(artifact, discovery_index):
    payload = fc.search_fungal_compound_structure(
        fc.StructureSearchRequest(
            structure="OC(Cn1cncn1)(Cn1cncn1)c1ccc(Cl)cc1F",
            similarity_threshold=0.4,
            max_results=5,
        ),
        user=None,
    )
    assert payload["search_mode"] == "structure_similarity"
    assert payload["query_structure"]["svg"].startswith("<?xml") or "<svg" in payload["query_structure"]["svg"]
    assert payload["query_structure"]["inchi_key"]
    assert payload["query_structure"]["canonical_smiles"]
    assert payload["query_structure"]["isomeric_smiles"]
    assert payload["query_structure"]["model_input_smiles"] == payload["query_structure"]["isomeric_smiles"]
    assert payload["query_structure"]["normalization"]["profile"].startswith("rdkit_")
    fluconazole = next(item for item in payload["matches"] if item["chembl_id"] == "CHEMBL106")
    assert fluconazole["match_type"] == "similar_structure"
    assert 0.4 <= fluconazole["structure_similarity"] < 1.0
    target = payload["fungi"][0]["targets"][0]
    assert target["gene_symbol"] in {"ERG11", "CYP51A"}
    assert target["relationship"] == "structure_similarity_inference"
    assert "证据属于" in target["relationship_label"]


@pytest.mark.skipif(not fc.capabilities()["rdkit"], reason="RDKit unavailable in this environment")
def test_structure_search_exact_match_keeps_curated_relationship(artifact, discovery_index):
    payload = fc.search_fungal_compound_structure(
        fc.StructureSearchRequest(
            structure="OC(Cn1cncn1)(Cn1cncn1)c1ccc(F)cc1F",
            similarity_threshold=0.9,
        ),
        user=None,
    )
    assert payload["matches"][0]["match_type"] == "exact_structure"
    assert payload["matches"][0]["structure_similarity"] == 1.0
    assert any(item["relationship"] == "chemogenomic_association" for item in payload["fungi"][0]["targets"])


def test_structure_search_rejects_unparseable_input(artifact, discovery_index):
    with pytest.raises(Exception) as error:
        fc.search_fungal_compound_structure(
            fc.StructureSearchRequest(structure="this is not a molecule"),
            user=None,
        )
    assert getattr(error.value, "status_code", None) == 422


@pytest.mark.skipif(not fc.capabilities()["rdkit"], reason="RDKit unavailable in this environment")
def test_structure_search_standardizes_salt_and_discloses_parent_selection(artifact, discovery_index):
    payload = fc.search_fungal_compound_structure(
        fc.StructureSearchRequest(structure="CC[NH2+]CC.[Cl-]", similarity_threshold=0.4),
        user=None,
    )
    query = payload["query_structure"]
    assert query["normalization"]["input_fragment_count"] == 2
    assert any("母体组分" in item for item in query["normalization"]["transformations"])
    assert query["normalization"]["warnings"]
    assert "." not in query["model_input_smiles"]


@pytest.mark.skipif(not fc.capabilities()["rdkit"], reason="RDKit unavailable in this environment")
def test_structure_standardization_preserves_defined_stereochemistry():
    _, query = fc._parse_structure_query("C[C@@H](N)C(=O)O", "smiles")
    assert "@" in query["raw_isomeric_smiles"]
    assert "@" in query["isomeric_smiles"]
    assert "@" in query["model_input_smiles"]
    assert "@" not in query["canonical_smiles"]


# --------------------------------------------------------------------------
# seeds and generation
# --------------------------------------------------------------------------

def test_seeds_are_named_when_the_two_sources_disagree(tmp_path, monkeypatch):
    """Which compounds a target's evidence names, in one place and sorted."""
    path = tmp_path / "derived" / "fungal-cgd-evidence.json"
    path.parent.mkdir(parents=True)
    payload = {
        "counts": {"cgd_links_dropped_unresolved_compound": 0},
        "compounds": [
            {"cgd_name": "coumarin-314", "class": "research_tool", "status": "resolved",
             "chembl_id": "CHEMBL314", "canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O"},
            {"cgd_name": "sirolimus", "class": "immunosuppressant", "status": "resolved",
             "chembl_id": "CHEMBL413", "canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O"},
        ],
        "unresolved_compounds": [],
        "targets": [{"uniprot": "X", "gene_symbol": "G", "protein_name": "P",
                     "compound_count": 2, "sensitizing_compounds": ["coumarin-314", "sirolimus"],
                     "resistance_compounds": []}],
        "links": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(fc, "FUNGAL_TARGET_DATA_DIR", tmp_path)
    fc._load_artifact_cached.cache_clear()
    try:
        artifact = fc.load_cgd_artifact()
        target = artifact["targets"][0]
        seeds = fc._seed_compounds(artifact, target)
        # Every compound is listed as a seed, and its curated class travels with
        # it so the UI can say what kind of compound it actually is.
        assert [seed["cgd_name"] for seed in seeds] == ["coumarin-314", "sirolimus"]
        classes = {seed["cgd_name"]: seed["class"] for seed in seeds}
        assert classes["sirolimus"] == "immunosuppressant"
    finally:
        fc._load_artifact_cached.cache_clear()


@pytest.mark.skipif(not fc.capabilities()["rdkit"], reason="RDKit unavailable in this environment")
def test_generation_is_deterministic(artifact):
    seeds = fc._screened_compounds(artifact)[:2]
    first = fc.generate_compounds(seeds, 10, None)
    second = fc.generate_compounds(seeds, 10, None)
    assert first["enabled"] is True
    assert [item["canonical_smiles"] for item in first["compounds"]] == [
        item["canonical_smiles"] for item in second["compounds"]
    ]
    assert [item["id"] for item in first["compounds"]] == [item["id"] for item in second["compounds"]]


@pytest.mark.skipif(not fc.capabilities()["rdkit"], reason="RDKit unavailable in this environment")
def test_generated_compounds_carry_no_activity_or_score_claims(artifact):
    seeds = fc._screened_compounds(artifact)[:2]
    generated = fc.generate_compounds(seeds, 10, None)
    forbidden = {
        "pchembl_median", "pchembl_best", "activity_count", "assay_count", "document_count",
        "predicted", "probability", "rank", "candidate_score", "score", "efficacy_score",
    }
    for item in generated["compounds"]:
        assert item["scientific_status"] == "unvalidated_exploration"
        assert item["activity_claim"] is None
        assert item["docking_score"] is None
        assert item["admet_predictions"] == {}
        assert not (set(item) & forbidden), sorted(set(item) & forbidden)
    # The report must state what was never evaluated, not just omit it.
    assert "antifungal_activity" in generated["constraints_not_evaluated"]
    assert "docking" in generated["constraints_not_evaluated"]
    assert generated["novelty_scope"] == "chembl_exact_canonical_smiles_only"


@pytest.mark.skipif(not fc.capabilities()["rdkit"], reason="RDKit unavailable in this environment")
def test_every_generated_structure_records_its_seed_and_rule(artifact):
    seeds = fc._screened_compounds(artifact)[:2]
    generated = fc.generate_compounds(seeds, 10, None)
    assert generated["compounds"], "fixture seeds should yield at least one product"
    for item in generated["compounds"]:
        provenance = item["provenance"]
        assert provenance["seed_molecule_chembl_id"] in {"CHEMBL106", "CHEMBL413"}
        assert provenance["rule_id"] in {rule["id"] for rule in fc.GENERATION_RULES}
        assert provenance["rdkit_version"]
        assert item["constraints"]["sa_score"] is not None
        assert 0.0 <= item["constraints"]["tanimoto_to_seed"] <= 1.0


def test_generation_disabled_without_seeds(artifact):
    disabled = fc.generate_compounds([], 10, None)
    assert disabled["enabled"] is False
    assert "没有" in disabled["reason"] or "未生成" in disabled["reason"]
    assert disabled["compounds"] == []
    assert disabled["funnel"] == []


def test_generation_disabled_without_rdkit(artifact, monkeypatch):
    real = fc.capabilities()
    monkeypatch.setattr(fc, "capabilities", lambda: {**real, "rdkit": False, "reaction_from_smarts": False})
    disabled = fc.generate_compounds(fc._screened_compounds(artifact)[:1], 10, None)
    assert disabled["enabled"] is False
    # No RDKit means no molecules invented to fill the section.
    assert disabled["compounds"] == []


def test_novelty_check_failure_is_reported_as_unchecked_not_novel(artifact):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    client = chembl_client(handler)
    try:
        generated = fc.generate_compounds(fc._screened_compounds(artifact)[:1], 5, client)
    finally:
        client.close()
    for item in generated["compounds"]:
        assert item["constraints"]["novelty_chembl_exact_smiles"] == "unchecked"
    assert generated["novelty_unchecked"] is True
    assert any("新颖性" in warning for warning in generated["warnings"])


# --------------------------------------------------------------------------
# retrieval layers
# --------------------------------------------------------------------------

def test_similarity_hits_do_not_inherit_activity(artifact):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/target.json"):
            return httpx.Response(200, json={"targets": [
                {"target_chembl_id": "CHEMBL1780", "pref_name": "Lanosterol 14-alpha demethylase",
                 "organism": "Candida albicans", "target_type": "SINGLE PROTEIN"},
                {"target_chembl_id": "CHEMBL4662933", "pref_name": "Lanosterol 14-alpha demethylase",
                 "organism": "Fungi", "target_type": "SINGLE PROTEIN"},
            ]})
        if path.endswith("/activity.json"):
            return httpx.Response(200, json={"page_meta": {"total_count": 1}, "activities": [
                {"molecule_chembl_id": "CHEMBL106", "parent_molecule_chembl_id": "CHEMBL106",
                 "pchembl_value": "7.3", "assay_chembl_id": "A1", "document_chembl_id": "D1",
                 "standard_type": "IC50", "target_organism": "Candida albicans"},
            ]})
        if path.endswith("/similarity.json"):
            return httpx.Response(200, json={"molecules": [
                {"molecule_chembl_id": "CHEMBL999", "pref_name": "ANALOGUE",
                 "molecule_hierarchy": {"parent_chembl_id": "CHEMBL999"},
                 "molecule_structures": {"canonical_smiles": "OC(Cn1cncn1)(Cn1cncn1)c1ccc(Cl)cc1F"},
                 "molecule_properties": {"full_mwt": "301.7", "alogp": "2.4", "psa": "60.1", "hbd": 1, "hba": 5, "rtb": 4, "qed_weighted": "0.72", "num_ro5_violations": 0},
                 "similarity": "88.5"},
            ]})
        if path.endswith("/molecule/CHEMBL106.json"):
            return httpx.Response(200, json={
                "molecule_chembl_id": "CHEMBL106", "pref_name": "FLUCONAZOLE", "max_phase": 4,
                "molecule_hierarchy": {"parent_chembl_id": "CHEMBL106"},
                "molecule_structures": {"canonical_smiles": "OC(Cn1cncn1)(Cn1cncn1)c1ccc(F)cc1F"},
                "molecule_properties": {"full_mwt": "306.3", "alogp": "0.5", "psa": "81.7", "hbd": 1, "hba": 7, "rtb": 5, "qed_weighted": "0.66", "num_ro5_violations": 0},
            })
        return httpx.Response(200, json={})

    client = chembl_client(handler)
    try:
        result = fc.explore_target(artifact, artifact["targets"][0], chembl=client, include_generation=False)
    finally:
        client.close()

    assert [item["molecule_chembl_id"] for item in result["direct_binding"]] == ["CHEMBL106"]
    assert result["direct_binding"][0]["pchembl_median"] == 7.3

    assert result["similar_compounds"], "the similarity layer should have produced a hit"
    for item in result["similar_compounds"]:
        assert item["evidence_kind"] == "similarity_only"
        # A similarity hit must never be handed the reference ligand's potency.
        assert "pchembl_median" not in item
        assert "pchembl_best" not in item
        assert "activity_count" not in item
        assert item["chembl_similarity"] == 88.5
        assert item["rdkit_tanimoto"] is None or 0.0 <= item["rdkit_tanimoto"] <= 1.0
    # The reference compound itself comes back as a 100% self-hit and is excluded.
    assert all(item["molecule_chembl_id"] != "CHEMBL106" for item in result["similar_compounds"])


def test_organism_mismatch_rows_are_dropped_and_counted(artifact):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/target.json"):
            return httpx.Response(200, json={"targets": [
                {"target_chembl_id": "CHEMBL1780", "organism": "Candida albicans", "target_type": "SINGLE PROTEIN"},
            ]})
        if request.url.path.endswith("/activity.json"):
            return httpx.Response(200, json={"page_meta": {"total_count": 2}, "activities": [
                {"molecule_chembl_id": "CHEMBL1", "parent_molecule_chembl_id": "CHEMBL1", "pchembl_value": "7.0",
                 "target_organism": "Candida albicans", "assay_chembl_id": "A", "document_chembl_id": "D", "standard_type": "IC50"},
                {"molecule_chembl_id": "CHEMBL2", "parent_molecule_chembl_id": "CHEMBL2", "pchembl_value": "9.0",
                 "target_organism": "Homo sapiens", "assay_chembl_id": "A", "document_chembl_id": "D", "standard_type": "IC50"},
            ]})
        return httpx.Response(200, json={})

    client = chembl_client(handler)
    try:
        result = fc.explore_target(artifact, artifact["targets"][0], chembl=client,
                                   include_generation=False, include_similarity=False)
    finally:
        client.close()
    assert result["resolution"]["organism_mismatch_count"] == 1
    assert [item["molecule_chembl_id"] for item in result["direct_binding"]] == ["CHEMBL1"]


def test_ambiguous_target_lookup_is_reported_not_silently_resolved(artifact):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"targets": [
            {"target_chembl_id": "CHEMBL1780", "organism": "Candida albicans (strain SC5314 / ATCC MYA-2876) (Yeast)", "target_type": "SINGLE PROTEIN"},
            {"target_chembl_id": "CHEMBL4662933", "organism": "Fungi", "target_type": "SINGLE PROTEIN"},
        ]})

    client = chembl_client(handler)
    try:
        result = fc.explore_target(artifact, artifact["targets"][0], chembl=client,
                                   include_generation=False, include_similarity=False)
    finally:
        client.close()
    assert result["resolution"]["chembl_target_id"] == "CHEMBL1780"
    assert len(result["resolution"]["chembl_candidates"]) == 2
    assert any("候选靶标" in warning for warning in result["warnings"])


def test_chembl_unavailable_degrades_without_inventing_rows(artifact):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    client = chembl_client(handler)
    try:
        result = fc.explore_target(artifact, artifact["targets"][0], chembl=client, include_generation=False)
    finally:
        client.close()
    assert result["direct_binding"] == []
    assert result["similar_compounds"] == []
    # Curated genetic evidence is offline and must survive an outage.
    assert result["genetic_evidence"]
    assert result["known_compounds"]
    assert any("失败" in warning for warning in result["warnings"])


def test_genetic_evidence_keeps_direction_and_provenance(artifact):
    rows = fc._genetic_evidence(artifact, "P10613")
    assert [row["direction"] for row in rows] == ["sensitizing", "resistance", "resistance"]
    for row in rows:
        assert row["phenotype_text"]
        assert row["reference"]
        assert row["experiment_type"]


def test_exploration_summary_states_what_was_dropped(artifact):
    summary = fc._exploration_summary(artifact)
    joined = " ".join(summary["warnings"])
    assert "nystatin" in joined
    assert "缺少 UniProt 映射" in joined


def test_catalog_works_when_the_data_dir_is_a_symlink(tmp_path, monkeypatch):
    """The serving layout points the data dir at a `current` symlink.

    Resolving only one side of the containment check raised ValueError on the
    production host and surfaced as "Unexpected server error" in the tab.
    """
    real = tmp_path / "releases" / "20260928-cgd-v1"
    (real / "derived").mkdir(parents=True)
    (real / "derived" / "fungal-cgd-evidence.json").write_text(json.dumps(artifact_payload()), encoding="utf-8")
    payload = artifact_payload()
    manifest = {
        "bundle_version": "20260928-cgd-v1",
        "files": [
            {
                "path": "derived/fungal-cgd-evidence.json",
                "sha256": hashlib.sha256((real / "derived" / "fungal-cgd-evidence.json").read_bytes()).hexdigest(),
            }
        ],
    }
    (real / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    link = tmp_path / "current"
    link.symlink_to(real)

    monkeypatch.setattr(fc, "FUNGAL_TARGET_DATA_DIR", link)
    fc._load_artifact_cached.cache_clear()
    try:
        integrity = fc.artifact_integrity()
        assert integrity["sha256_verified"] is True
        catalog = fc.target_catalog()
        assert catalog["configured"] is True
        # The live release name is reported, not the symlink's basename.
        assert catalog["bundle_version"] == "20260928-cgd-v1"
        assert payload["targets"][0]["uniprot"] in {item["uniprot"] for item in catalog["targets"]}
    finally:
        fc._load_artifact_cached.cache_clear()


def test_catalog_degrades_when_the_data_dir_is_absent(tmp_path, monkeypatch):
    """A missing bundle reports not-configured; it must never raise."""
    monkeypatch.setattr(fc, "FUNGAL_TARGET_DATA_DIR", tmp_path / "nope")
    fc._load_artifact_cached.cache_clear()
    try:
        catalog = fc.target_catalog()
        assert catalog["configured"] is False
        assert catalog["targets"] == []
        assert catalog["compounds"] == []
        assert catalog["bundle_version"] == "nope"
    finally:
        fc._load_artifact_cached.cache_clear()
