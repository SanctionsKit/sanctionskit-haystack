import copy
import json
from pathlib import Path

import httpx
import pytest
from haystack import Pipeline
from haystack.core.errors import DeserializationError
from haystack.utils import Secret

from sanctionskit_haystack import (
    SanctionsKitAPIError,
    SanctionsKitProtocolError,
    SanctionsKitResultReader,
    SanctionsKitScreening,
    SanctionsKitTransportError,
)
from sanctionskit_haystack import components

FIXTURES = Path(__file__).parent / "fixtures"
REAL_CLIENT = httpx.Client
KEY = "not-a-real-key-offline-fixture"
RESULT_ID = "00000000-0000-4000-8000-000000000002"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def mock_api(monkeypatch, handler):
    requests = []
    options = []

    def capture(request):
        requests.append(request)
        return handler(request)

    def client(**kwargs):
        options.append(kwargs)
        return REAL_CLIENT(transport=httpx.MockTransport(capture), **kwargs)

    monkeypatch.setattr(components.httpx, "Client", client)
    return requests, options


@pytest.mark.parametrize("name", ["recorded_no_match", "recorded_potential_match"])
def test_real_pipeline_preserves_complete_synthetic_evidence(monkeypatch, name):
    response = fixture(name)
    response["data"]["future_evidence_field"] = {"preserved": True}
    original = fixture("request")
    unchanged = copy.deepcopy(original)
    requests, options = mock_api(monkeypatch, lambda _: httpx.Response(201, json=response))
    pipeline = Pipeline()
    pipeline.add_component("screen", SanctionsKitScreening())
    result = pipeline.run({"screen": {"request": original, "idempotency_key": "offline-screen-001"}})
    assert result["screen"]["result"] == response["data"]
    assert original == unchanged
    assert len(requests) == 1
    assert requests[0].url == "https://www.sanctionskit.com/api/v1/screenings"
    assert requests[0].method == "POST"
    assert requests[0].headers["Authorization"] == f"Bearer {KEY}"
    assert requests[0].headers["Idempotency-Key"] == "offline-screen-001"
    assert json.loads(requests[0].content) == original
    assert options == [{"timeout": 30.0, "follow_redirects": False}]


def test_reader_preserves_retained_result_and_does_not_screen(monkeypatch):
    response = fixture("recorded_retained")
    result_id = response["data"]["id"]
    requests, _ = mock_api(monkeypatch, lambda _: httpx.Response(200, json=response))
    assert SanctionsKitResultReader().run(result_id)["result"] == response["data"]
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"/api/v1/results/{result_id}"
    assert requests[0].content == b""
    assert "Idempotency-Key" not in requests[0].headers


def test_reader_rejects_other_results_evidence(monkeypatch):
    response = fixture("recorded_retained")
    assert response["data"]["id"] != RESULT_ID
    calls, _ = mock_api(monkeypatch, lambda _: httpx.Response(200, json=response))
    with pytest.raises(SanctionsKitProtocolError, match="different result"):
        SanctionsKitResultReader().run(RESULT_ID)
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [
    {"subject": {"name": "Alex Morgan"}},
    {"subject": {"name": "Alex Morgan"}, "sources": [], "package": "sandbox@1"},
    {"subject": {"name": "Alex Morgan"}, "sources": []},
    {"subject": {"name": "Alex Morgan", "birthDate": "2026-02-30"}, "package": "sandbox@1"},
    {"subject": {"name": "A"}, "package": "sandbox@1"},
    {"subject": {"name": "Alex Morgan", "unexpected": "private"}, "package": "sandbox@1"},
])
def test_invalid_or_ambiguous_coverage_fails_before_transport(monkeypatch, payload):
    calls, _ = mock_api(monkeypatch, lambda _: pytest.fail("must not call API"))
    with pytest.raises(ValueError):
        SanctionsKitScreening().run(payload, "offline-screen-001")
    assert not calls


@pytest.mark.parametrize("key", ["short", "a" * 129, "abcdefgh\r\nInjected: yes", "非ASCII-key", None])
def test_invalid_idempotency_key_cannot_reach_http(monkeypatch, key):
    calls, _ = mock_api(monkeypatch, lambda _: pytest.fail("must not call API"))
    with pytest.raises(ValueError):
        SanctionsKitScreening().run(fixture("request"), key)
    assert not calls


@pytest.mark.parametrize("status,code", [
    (401, "invalid_api_key"), (403, "insufficient_scope"),
    (409, "idempotency_conflict"), (422, "source_unavailable"),
    (429, "rate_limited"), (503, "source_refresh_blocked"),
])
def test_api_failure_is_not_no_match_or_silent_retry(monkeypatch, status, code):
    payload = {"error": {"code": code, "message": f"echo {KEY} Alex Morgan",
                         "requestId": RESULT_ID}}
    calls, _ = mock_api(monkeypatch, lambda _: httpx.Response(status, json=payload))
    with pytest.raises(SanctionsKitAPIError) as caught:
        SanctionsKitScreening().run(fixture("request"), "offline-screen-001")
    assert caught.value.status_code == status
    assert caught.value.code == code
    assert caught.value.request_id == RESULT_ID
    assert len(calls) == 1
    assert KEY not in str(caught.value) and "Alex Morgan" not in str(caught.value)


def test_timeout_remains_uncertain_and_keeps_caller_idempotency(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout(f"private transport {KEY}", request=request)

    calls, _ = mock_api(monkeypatch, timeout)
    for _ in range(2):
        with pytest.raises(SanctionsKitTransportError) as caught:
            SanctionsKitScreening().run(fixture("request"), "stable-retry-001")
        assert KEY not in str(caught.value)
        assert "same Idempotency-Key" in str(caught.value)
    assert len(calls) == 2  # Two explicit caller attempts, no implicit retries.
    assert calls[0].headers["Idempotency-Key"] == calls[1].headers["Idempotency-Key"]
    assert calls[0].content == calls[1].content


def test_redirect_is_rejected_without_sending_key_to_another_host(monkeypatch):
    calls, _ = mock_api(monkeypatch, lambda _: httpx.Response(302, headers={"Location": "https://example.org/"}))
    with pytest.raises(SanctionsKitAPIError) as caught:
        SanctionsKitResultReader().run(RESULT_ID)
    assert caught.value.status_code == 302
    assert len(calls) == 1


@pytest.mark.parametrize("mutation", ["missing_coverage", "unknown_status", "missing_disclaimer", "malformed_envelope"])
def test_incomplete_evidence_is_rejected(monkeypatch, mutation):
    response = fixture("recorded_no_match")
    if mutation == "missing_coverage":
        del response["data"]["coverage"]
    elif mutation == "unknown_status":
        response["data"]["status"] = "approved"
    elif mutation == "missing_disclaimer":
        del response["data"]["disclaimer"]
    else:
        response = {"data": []}
    mock_api(monkeypatch, lambda _: httpx.Response(201, json=response))
    with pytest.raises(SanctionsKitProtocolError):
        SanctionsKitScreening().run(fixture("request"), "offline-screen-001")


def test_invalid_json_is_not_a_decision(monkeypatch):
    mock_api(monkeypatch, lambda _: httpx.Response(200, text="not-json"))
    with pytest.raises(SanctionsKitProtocolError):
        SanctionsKitResultReader().run(RESULT_ID)


@pytest.mark.parametrize("identifier", ["../../sources", "x?secret=1", "not-a-uuid", "", None])
def test_invalid_result_id_never_reaches_transport(monkeypatch, identifier):
    calls, _ = mock_api(monkeypatch, lambda _: pytest.fail("must not call API"))
    with pytest.raises(ValueError):
        SanctionsKitResultReader().run(identifier)
    assert not calls


def test_real_haystack_yaml_roundtrip_keeps_environment_reference(monkeypatch):
    pipeline = Pipeline()
    pipeline.add_component("screen", SanctionsKitScreening())
    serialized = pipeline.dumps()
    assert "SANCTIONSKIT_API_KEY" in serialized
    assert KEY not in serialized
    with pytest.raises(DeserializationError):
        Pipeline.loads(serialized)
    restored = Pipeline.loads(serialized, allowed_modules=["sanctionskit_haystack.components"])
    response = fixture("recorded_no_match")
    mock_api(monkeypatch, lambda _: httpx.Response(201, json=response))
    out = restored.run({"screen": {"request": fixture("request"), "idempotency_key": "offline-screen-001"}})
    assert out["screen"]["result"] == response["data"]


def test_token_secret_cannot_be_serialized():
    with pytest.raises(ValueError):
        SanctionsKitScreening(api_key=Secret.from_token(KEY)).to_dict()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, 301])
def test_timeout_requires_a_finite_positive_bound(timeout):
    with pytest.raises(ValueError):
        SanctionsKitScreening(timeout=timeout)
