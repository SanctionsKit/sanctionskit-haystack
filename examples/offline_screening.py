"""Run a fictional screening and evidence retrieval without a key or network.

Run from a source checkout after `python -m pip install .`:
    python examples/offline_screening.py

The stub is only a local demonstration, not a hosted sandbox or a decision.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

# Set before importing Haystack so the example does not send telemetry.
os.environ["HAYSTACK_TELEMETRY_ENABLED"] = "false"

import httpx
from haystack import Pipeline
from haystack.utils import Secret

from sanctionskit_haystack import SanctionsKitResultReader, SanctionsKitScreening

_REAL_CLIENT = httpx.Client
_EXAMPLE_KEY = "offline-example-not-a-real-api-key"
_IDEMPOTENCY_KEY = "offline-fictional-example-001"


def _reject_network(*args, **kwargs):
    raise RuntimeError("This example must not open network connections")


def run_example() -> dict:
    request_body = {
        "subject": {"name": "Alex Morgan", "entityType": "person"},
        "package": "sandbox@1",
        "retention": "standard",
    }
    # Public-contract fixture: every identity and record is fictional.
    response = json.loads(Path(__file__).with_name("synthetic_response.json").read_text())
    retained = copy.deepcopy(response)
    retained["data"].update(subject=request_body["subject"], reference=None)
    mocked_requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host != "www.sanctionskit.com" or request.url.scheme != "https":
            raise AssertionError("Unexpected destination in offline example")
        if request.headers["Authorization"] != f"Bearer {_EXAMPLE_KEY}":
            raise AssertionError("The example must use only its nonfunctional key")
        mocked_requests.append({"method": request.method, "path": request.url.path})
        if request.method == "POST" and request.url.path == "/api/v1/screenings":
            assert json.loads(request.content) == request_body
            assert request.headers["Idempotency-Key"] == _IDEMPOTENCY_KEY
            return httpx.Response(201, json=response)
        if request.method == "GET" and request.url.path == f"/api/v1/results/{response['data']['id']}":
            assert not request.content and "Idempotency-Key" not in request.headers
            return httpx.Response(200, json=retained)
        raise AssertionError("Unexpected operation in offline example")

    def offline_client(**kwargs):
        return _REAL_CLIENT(transport=httpx.MockTransport(handle), **kwargs)

    # Demonstration-only transport replacement. The socket guards fail closed
    # if any code escapes the mock. No environment API key is read.
    with (
        patch("sanctionskit_haystack.components.httpx.Client", offline_client),
        patch("socket.socket.connect", _reject_network),
        patch("socket.create_connection", _reject_network),
    ):
        key = Secret.from_token(_EXAMPLE_KEY)
        pipeline = Pipeline()
        pipeline.add_component("screen", SanctionsKitScreening(api_key=key))
        result = pipeline.run({"screen": {
            "request": request_body,
            "idempotency_key": _IDEMPOTENCY_KEY,
        }})["screen"]["result"]
        saved = SanctionsKitResultReader(api_key=key).run(result_id=result["id"])["result"]

    # Preserve the complete objects for an analyst, not an approve/reject branch.
    return {"screening_evidence": result, "retained_evidence": saved, "mocked_requests": mocked_requests}


if __name__ == "__main__":
    example = run_example()
    evidence = example["screening_evidence"]
    print("Offline fictional example; no API requests were sent.")
    print(f"Status: {evidence['status']}; source version: {evidence['coverage'][0]['version']}")
    print(f"Retained evidence: {example['retained_evidence']['id']}")
    print("Analyst review is required. This example makes no approval or legal decision.")
