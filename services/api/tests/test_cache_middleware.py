from types import SimpleNamespace

from app.cache_middleware import _eligible_cache_request, _namespace_for, _shared_reference_namespace
from app.persistent_cache import response_cache_key


def request(method: str, path: str, content_type: str = "application/json"):
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        query_params={},
        headers={"content-type": content_type},
    )


def test_safe_compute_posts_are_cacheable_but_mutations_are_not():
    base = "/api/v1/drug-discovery/projects/project-1"
    assert not _eligible_cache_request(request("POST", f"{base}/process/engineering/thermodynamics"))
    assert not _eligible_cache_request(request("POST", f"{base}/process/retrosynthesis/analyze"))
    assert _eligible_cache_request(request("POST", "/api/v1/drug-discovery/fungal-discovery/structure-search"))
    assert _eligible_cache_request(request("POST", "/api/v1/drug-discovery/bacterial-discovery/structure-search"))
    assert not _eligible_cache_request(request("POST", f"{base}/reports"))
    assert not _eligible_cache_request(request("POST", f"{base}/process/engineering/thermodynamics", "multipart/form-data"))


def test_post_body_participates_in_persistent_cache_identity():
    first = response_cache_key("POST", "/compute", "", "user:one", "application/json", "aaa")
    second = response_cache_key("POST", "/compute", "", "user:one", "application/json", "bbb")
    assert first != second


def test_contract_and_namespace_participate_in_cache_identity():
    fungal = response_cache_key("GET", "/reference", "", "shared", namespace="target.fungal.reference")
    bacterial = response_cache_key("GET", "/reference", "", "shared", namespace="target.bacterial.reference")
    assert fungal != bacterial


def test_project_and_job_records_always_bypass_postgresql_cache():
    assert not _eligible_cache_request(request("GET", "/api/v1/drug-discovery/projects/project-1"))
    assert not _eligible_cache_request(request("GET", "/api/v1/drug-discovery/jobs/job-1"))
    assert not _eligible_cache_request(request("POST", "/api/v1/drug-discovery/projects/project-1/fungal-targets/preview"))


def test_target_reference_namespaces_are_shared_but_other_reads_are_user_scoped():
    fungal = _namespace_for("/api/v1/drug-discovery/fungal-discovery/organisms")
    bacterial = _namespace_for("/api/v1/drug-discovery/bacterial-compounds/targets")
    protein = _namespace_for("/api/v1/drug-discovery/protein-structure/pdb/1abc")
    assert fungal == "target.fungal.reference"
    assert bacterial == "target.bacterial.reference"
    assert _shared_reference_namespace(fungal)
    assert _shared_reference_namespace(bacterial)
    assert not _shared_reference_namespace(protein)
