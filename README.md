# SanctionsKit for Haystack

Sanctions screening and retained evidence components for [Haystack](https://haystack.deepset.ai/).

Two Haystack components connect a pipeline to the [SanctionsKit sanctions screening API](https://www.sanctionskit.com/docs/screenings):

- `SanctionsKitScreening`: submit one explicitly scoped screening, with a caller-provided idempotency key.
- `SanctionsKitResultReader`: retrieve retained evidence for an existing result UUID. Retrieval does not re-screen a subject.

Both return the complete API evidence object without summarizing away source versions, warnings, matching policy, conflicts, or the service disclaimer. API failures raise errors. A no-match response applies only to the selected coverage and matching rules; it is not legal clearance. Potential matches require human review.

## Install from source

This is a development version. There is no published PyPI package. From a source checkout, use a Python 3.10+ virtual environment and install the package:

```sh
python -m pip install .
```

The supported dependency range is Haystack 3.3.x. Tests use Haystack 3.3.0 and synthetic HTTP fixtures. Authenticated hosted interoperability has not been tested.

## Try it offline

From the source checkout, after installation, run:

```sh
python examples/offline_screening.py
```

No API key or account is needed. The example uses Haystack's real pipeline runtime with an `httpx.MockTransport` stub and a fictional Alex Morgan record from the public contract. It submits a mock screening, retrieves mock retained evidence, and preserves the full evidence for analyst review. The example blocks network connections, disables Haystack telemetry, and uses a nonfunctional key instead of reading any environment credentials. It creates no hosted screening and makes no approval or legal decision.

Expected output includes `Status: potential_match` and `Analyst review is required`. The [example source](examples/offline_screening.py) shows the stub; [synthetic_response.json](examples/synthetic_response.json) contains the invented evidence. This demonstrates local pipeline behavior, not authenticated hosted compatibility.

## Screening example

Set `SANCTIONSKIT_API_KEY` through your normal secret manager or shell environment. Never put the key in source, pipeline YAML, logs, or a prompt. Use a sandbox key for this fictional example. The free synthetic sandbox and paid production screening are separate products.

```python
from haystack import Pipeline
from sanctionskit_haystack import SanctionsKitScreening

pipeline = Pipeline()
pipeline.add_component("screen", SanctionsKitScreening())
result = pipeline.run({
    "screen": {
        "request": {
            "subject": {"name": "Alex Morgan", "entityType": "person"},
            "package": "sandbox@1",
            "retention": "standard",
        },
        "idempotency_key": "haystack-fictional-example-001",
    }
})
evidence = result["screen"]["result"]
# Hand evidence to the organization's review process; do not auto-approve.
```

The key needs `screenings:write`. Choose exactly one of `sources` or `package`; there is no implicit global coverage. Discover available coverage and approved policies through the public API documentation. If the organization requires a policy, supply its exact approved `id` and `version`. The component validates the request against the bundled public contract before calling the service.

The caller must generate a distinct idempotency key for each logical screening and retain it. For an identical retry, reuse that key and the identical request. The component performs no automatic retries. A timeout does not prove that the server rejected the request. Reusing a key with different input can return HTTP 409.

## Retrieve retained evidence

```python
from sanctionskit_haystack import SanctionsKitResultReader

reader = SanctionsKitResultReader()
retained = reader.run(result_id=evidence["id"])["result"]
```

The key needs `results:read`. Standard retention can retain submitted subject details. Minimal retention lasts 24 hours and omits submitted inputs; organization policy can require standard retention. A missing or expired result remains an error. Be deliberate about passing identity information to other pipeline components, prompts, traces, or document stores.

## Errors and serialization

`SanctionsKitAPIError` exposes `status_code`, a bounded machine `code`, and a valid `request_id` when supplied. It does not reproduce raw response messages. `SanctionsKitTransportError` signals an uncertain transport outcome. `SanctionsKitProtocolError` rejects malformed or incomplete evidence. None becomes a no-match result.

Result retrieval also checks that the returned evidence UUID matches the requested result.

The API origin is fixed to `https://www.sanctionskit.com/api/v1`, and redirects are not followed. Haystack `Secret.from_env_var` references support pipeline serialization without resolving secrets. Token-based secrets follow Haystack's refusal to serialize raw tokens.

Haystack 3.3 requires explicit trust when loading a custom component from serialized data. For a pipeline you trust, use `Pipeline.loads(yaml_text, allowed_modules=["sanctionskit_haystack.components"])`. Keep the default protection for untrusted pipeline files; the adapter does not change the global allowlist.

## Development and validation

Install the test dependencies and run the suite:

```sh
python -m pip install '.[test]'
python -m pytest
python -m build
```

Tests use the real installed Haystack runtime and an intercepted HTTP transport with synthetic examples from the public OpenAPI contract. A test fixture blocks socket connections and supplies a deliberately invalid fixture key. The suite creates no hosted screenings and uses no real credentials. Contract provenance and update guidance are in [CONTRACT.md](CONTRACT.md).

The build produces a wheel and source distribution in `dist/`. Building and running these tests does not verify an authenticated hosted integration.

## License and support

Maintained by SanctionsKit. The [MIT license](LICENSE) covers this adapter. Use of the hosted API is governed by the [SanctionsKit terms](https://www.sanctionskit.com/terms) and [privacy policy](https://www.sanctionskit.com/privacy).

For integration questions, contact [support@sanctionskit.com](mailto:support@sanctionskit.com).
