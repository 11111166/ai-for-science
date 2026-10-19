import json
from pathlib import Path


def test_milestone_disease_set_is_unique_and_balanced():
    config = Path(__file__).parents[1] / "config" / "diseases.json"
    diseases = json.loads(config.read_text())["diseases"]

    assert len(diseases) == 25
    assert len({item["id"] for item in diseases}) == 25
    assert len([item for item in diseases if item["tier"] == "complete"]) == 5
    assert len([item for item in diseases if item["tier"] == "enhanced"]) == 20
    assert len([item for item in diseases if item["tier"] == "enhanced" and item["area"] == "oncology"]) == 7
    assert len([item for item in diseases if item["tier"] == "enhanced" and item["area"] == "rare"]) == 5

