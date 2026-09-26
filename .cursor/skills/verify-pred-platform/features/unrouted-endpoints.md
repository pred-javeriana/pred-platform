# Unrouted endpoints and disabled documentation

Unrouted endpoints and disabled documentation handle unexpected request paths gracefully, returning standard HTTP 404 responses while ensuring administrative or interactive documentation surfaces are not exposed unintentionally.

## Sub-features

- `unrouted-404` returns HTTP status 404 with JSON payload `{"detail":"Not Found"}` when requesting unregistered paths.
- `docs-disabled` confirms `/docs` (Swagger UI) returns HTTP 404 rather than rendering an interactive interface.
- `redoc-disabled` confirms `/redoc` (ReDoc UI) returns HTTP 404 rather than rendering documentation.

## How to get to it (user POV)

- Navigate to an invalid or unknown URL path (e.g. `http://localhost:<port>/nonexistent`).
- Attempt to navigate to default documentation URLs (e.g. `http://localhost:<port>/docs` or `http://localhost:<port>/redoc`).

## Driving it with driver

Preconditions:

- PRED platform is running and healthy.
- `$EVIDENCE_DIR` exists.

- **Request unknown path.** Query an unregistered endpoint. Run `./.cursor/skills/verify-pred-platform/driver http GET /nonexistent -s -w "\nHTTP_STATUS: %{http_code}\n" > "$EVIDENCE_DIR/404-unrouted.txt"`. Output indicates body `{"detail":"Not Found"}` and `HTTP_STATUS: 404`.
- **Verify /docs disabled.** Probe `/docs`. Run `./.cursor/skills/verify-pred-platform/driver http GET /docs -s -w "\nHTTP_STATUS: %{http_code}\n" > "$EVIDENCE_DIR/404-docs.txt"`. Output indicates `HTTP_STATUS: 404`.
- **Verify /redoc disabled.** Probe `/redoc`. Run `./.cursor/skills/verify-pred-platform/driver http GET /redoc -s -w "\nHTTP_STATUS: %{http_code}\n" > "$EVIDENCE_DIR/404-redoc.txt"`. Output indicates `HTTP_STATUS: 404`.
- **Proof.** Confirm all three 404 response artifacts exist in `$EVIDENCE_DIR`.

## Gotchas

- FastAPI by default enables `/docs` and `/redoc`; their 404 status in this application is intentional configuration (`docs_url=None`, `redoc_url=None`) to preserve security boundaries.
- HTML 404 error pages are not currently configured; unrouted requests return JSON error objects.
