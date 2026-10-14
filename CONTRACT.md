# Contract provenance

The bundled `contract.json` is a deterministic subset of SanctionsKit's public OpenAPI 3.1 schema, fetched on October 14, 2026:

- Source: https://www.sanctionskit.com/openapi.json
- Source SHA-256: `ee35d211051ac59466f15ac101fe9fd8b572641c5328a6b605359cf869705c6c`
- Operations: `POST /screenings` and `GET /results/{id}`.
- Root schemas: `ScreeningRequest`, `ScreeningResult`, `RetainedScreeningResult`.
- All transitively referenced component schemas are included unchanged.

The test responses are the source's explicitly synthetic or isolated-handler examples. They are not live customer data or evidence that a hosted request was executed for this adapter. The test request uses the fictional Alex Morgan sandbox identity and `sandbox@1` coverage.

`examples/synthetic_response.json` copies the same synthetic potential-match fixture. The offline example derives its retained-result stub by adding the fictional request subject and a null reference to that evidence; this is a local demonstration, not a recorded hosted response.

An API contract update requires reviewing the changed schema and fixtures, rerunning component/pipeline tests, and qualifying compatibility before widening the supported range. Request validation rejects unknown input fields in the pinned contract. Responses retain additive fields permitted by the source schema.
