# OpenAPI specification

The OpenAPI specification endpoint provides a machine-readable schema of all registered routes and operations in the PRED platform, allowing client generators and API tools to inspect the service contract.

## Sub-features

- `openapi-schema` returns a valid OpenAPI 3.1.0 JSON document at `GET /openapi.json`.
- `openapi-metadata` confirms application metadata contains `title: "PRED"` and `version: "0.1.0"`.
- `openapi-paths` exposes registered routes for `/health` and `/`.

## How to get to it (user POV)

- Request `http://localhost:<port>/openapi.json` in a browser or API development client.
- Run `curl -s http://127.0.0.1:<port>/openapi.json` from the command line.
- Ingest into Postman, Insomnia, or an OpenAPI-compliant validation tool.

## Driving it with driver

Preconditions:

- PRED platform is running and healthy.
- `$EVIDENCE_DIR` exists.

- **Retrieve schema.** Fetch the OpenAPI JSON schema. Run `./.cursor/skills/verify-pred-platform/driver http GET /openapi.json -s > "$EVIDENCE_DIR/openapi.json"`. The command exits with code 0 and outputs valid JSON.
- **Validate schema title and version.** Parse metadata from the downloaded schema. Run `python3 -c "import json; d = json.load(open('$EVIDENCE_DIR/openapi.json')); assert d['info']['title'] == 'PRED'; assert d['info']['version'] == '0.1.0'; print('Schema info verified')"` . The verification script prints `Schema info verified`.
- **Verify registered paths.** Confirm active endpoints are documented. Run `python3 -c "import json; d = json.load(open('$EVIDENCE_DIR/openapi.json')); paths = list(d['paths'].keys()); assert '/health' in paths and '/' in paths; print('Paths verified:', paths)"` . The script prints `Paths verified: ['/health', '/']`.
- **Proof.** Confirm `$EVIDENCE_DIR/openapi.json` exists and is non-empty.

## Gotchas

- Interactive documentation UIs (`/docs` and `/redoc`) are explicitly disabled in application configuration (`docs_url=None`, `redoc_url=None`).
- Custom route parameters added in future versions will automatically appear under `paths`.
