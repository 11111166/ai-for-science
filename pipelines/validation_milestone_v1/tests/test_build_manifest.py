import json

from build_manifest import artifact_quality, build_manifest


def test_novel_quality_uses_nested_build_and_benchmark_gates():
    payload = {
        "schema_version": "novel-target-discovery-v1",
        "quality": {
            "build": {"status": "passed"},
            "benchmark": {"quality_gates": {"passed": True}},
        },
    }
    assert artifact_quality(payload, "MONDO_TEST") is True


def test_manifest_requires_target_disease_and_records_five_artifacts(tmp_path):
    disease_id = "MONDO_TEST"
    payloads = [
        {
            "schema_version": "target-discovery-product-snapshot-v1",
            "quality": {"benchmark": {"quality_gates": {"passed": True}}},
            "targets": [{"disease_id": disease_id}],
        },
        {
            "schema_version": "novel-target-discovery-v1",
            "quality": {
                "build": {"status": "passed"},
                "benchmark": {"quality_gates": {"passed": True}},
            },
        },
        *({"schema_version": "module-v1", "quality_passed": True} for _ in range(3)),
    ]
    artifacts = []
    for index, payload in enumerate(payloads):
        path = tmp_path / f"artifact-{index}.json"
        path.write_text(json.dumps(payload))
        artifacts.append(f"module-{index}={path}")

    result = build_manifest(disease_id, tmp_path / "validation.json", artifacts)

    assert result["quality_passed"] is True
    assert len(result["artifacts"]) == 5

