"""Two explicit API operations; no implicit coverage, retries, or legal decisions."""

from __future__ import annotations

import copy
import json
import math
import re
from importlib.resources import files
from typing import Any

import httpx
from haystack import component, default_from_dict, default_to_dict
from haystack.utils import Secret, deserialize_secrets_inplace
from jsonschema import Draft202012Validator, FormatChecker

from .errors import SanctionsKitAPIError, SanctionsKitProtocolError, SanctionsKitTransportError

_ORIGIN = "https://www.sanctionskit.com/api/v1"
_CONTRACT = json.loads(files("sanctionskit_haystack").joinpath("contract.json").read_text())
_IDEMPOTENCY = re.compile(r"[\w:.-]{8,128}", re.ASCII)
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": _CONTRACT["components"]},
        format_checker=FormatChecker(),
    )


def _valid_uuid(value: Any) -> bool:
    return isinstance(value, str) and _UUID.fullmatch(value) is not None


class _APIComponent:
    def __init__(self, api_key: Secret | None = None, timeout: float = 30.0):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError("timeout must be a finite number greater than 0 and at most 300 seconds")
        if api_key is not None and not isinstance(api_key, Secret):
            raise TypeError("api_key must be a Haystack Secret")
        self.api_key = api_key if api_key is not None else Secret.from_env_var("SANCTIONSKIT_API_KEY")
        self.timeout = float(timeout)

    def to_dict(self) -> dict[str, Any]:
        # Haystack refuses serialization of token-based secrets. Environment
        # references serialize without resolving or copying their values.
        return default_to_dict(self, api_key=self.api_key.to_dict(), timeout=self.timeout)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _APIComponent:
        restored = copy.deepcopy(data)
        deserialize_secrets_inplace(restored["init_parameters"], keys=["api_key"])
        return default_from_dict(cls, restored)

    def _request(self, method: str, path: str, response_schema: str, *, payload: bytes | None = None,
                 idempotency_key: str | None = None) -> dict[str, Any]:
        token = self.api_key.resolve_value()
        if not token or "\r" in token or "\n" in token:
            raise ValueError("A nonempty API key without newline characters is required")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        try:
            # An API response must never redirect this credential to another host.
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                response = client.request(method, _ORIGIN + path, headers=headers, content=payload)
        except httpx.HTTPError:
            raise SanctionsKitTransportError(
                "SanctionsKit transport failed. A screening may have been accepted; "
                "retry only with the identical request and the same Idempotency-Key."
            ) from None
        if not response.is_success:
            code, request_id = "http_error", None
            try:
                error = response.json().get("error", {})
                if isinstance(error, dict):
                    candidate = error.get("code")
                    if isinstance(candidate, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,99}", candidate):
                        code = candidate
                    if _valid_uuid(error.get("requestId")):
                        request_id = error["requestId"]
            except (ValueError, AttributeError, TypeError):
                pass
            raise SanctionsKitAPIError(response.status_code, code, request_id)
        try:
            envelope = response.json()
        except ValueError:
            raise SanctionsKitProtocolError("SanctionsKit returned invalid JSON; no decision is available.") from None
        if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), dict):
            raise SanctionsKitProtocolError("SanctionsKit response has no evidence object; no decision is available.")
        result = envelope["data"]
        if next(_validator(response_schema).iter_errors(result), None) is not None:
            raise SanctionsKitProtocolError("SanctionsKit evidence does not match the supported contract; no decision is available.")
        # Return the complete evidence, including unknown additive response keys.
        # Do not flatten status to a boolean or discard source/version metadata.
        return result


@component
class SanctionsKitScreening(_APIComponent):
    """Screen one subject with explicitly selected sources or a versioned package.

    `screenings:write` is required. The caller owns the idempotency key and must
    reuse it for an identical retry. This component never retries a request.
    """

    @component.output_types(result=dict[str, Any])
    def run(self, request: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY.fullmatch(idempotency_key):
            raise ValueError("idempotency_key must contain 8–128 ASCII letters, digits, underscore, colon, dot, or hyphen")
        if not isinstance(request, dict) or next(_validator("ScreeningRequest").iter_errors(request), None) is not None:
            raise ValueError("request must match ScreeningRequest, including exactly one of sources or package")
        try:
            payload = json.dumps(request, allow_nan=False, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            raise ValueError("request must contain only JSON values") from None
        if len(payload) > 1_048_576:
            raise ValueError("request exceeds the API's 1 MiB request limit")
        return {"result": self._request("POST", "/screenings", "ScreeningResult", payload=payload,
                                        idempotency_key=idempotency_key)}


@component
class SanctionsKitResultReader(_APIComponent):
    """Retrieve retained evidence by UUID using an API key with `results:read`.

    This does not perform a fresh screening. Minimal-retention results expire
    after 24 hours; retrieval errors remain errors.
    """

    @component.output_types(result=dict[str, Any])
    def run(self, result_id: str) -> dict[str, Any]:
        if not _valid_uuid(result_id):
            raise ValueError("result_id must be a UUID")
        result = self._request("GET", f"/results/{result_id}", "RetainedScreeningResult")
        if result["id"].lower() != result_id.lower():
            raise SanctionsKitProtocolError("SanctionsKit returned evidence for a different result; no decision is available.")
        return {"result": result}
