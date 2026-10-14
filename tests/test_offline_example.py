import importlib.util
import json
from pathlib import Path


def test_documented_example_runs_without_credentials_and_preserves_evidence(monkeypatch):
    monkeypatch.delenv("SANCTIONSKIT_API_KEY", raising=False)
    example_path = Path(__file__).parents[1] / "examples" / "offline_screening.py"
    spec = importlib.util.spec_from_file_location("offline_screening_example", example_path)
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    outcome = example.run_example()
    expected = json.loads(example_path.with_name("synthetic_response.json").read_text())["data"]
    assert outcome["screening_evidence"] == expected
    assert outcome["retained_evidence"]["id"] == expected["id"]
    for field in ["status", "matches", "coverage", "versions", "matching", "warnings", "disclaimer"]:
        assert outcome["retained_evidence"][field] == expected[field]
    assert outcome["mocked_requests"] == [
        {"method": "POST", "path": "/api/v1/screenings"},
        {"method": "GET", "path": f"/api/v1/results/{expected['id']}"},
    ]
