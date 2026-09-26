# Service health probe

The service health probe allows operators, automated monitors, and orchestrators to verify that the PRED platform process is running and able to handle incoming HTTP requests.

## Sub-features

- `health-status` returns an HTTP 200 OK response with JSON payload `{"status":"ok"}`.
- `health-headers` provides the standard `application/json` Content-Type header.
- `health-methods` rejects unsupported HTTP methods (e.g. POST, PUT, DELETE) with HTTP 405 Method Not Allowed.

## How to get to it (user POV)

- Open `http://localhost:<port>/health` in a browser or API monitoring console.
- Run `curl -s http://127.0.0.1:<port>/health` in a terminal.
- Run `./.cursor/skills/verify-pred-platform/driver http GET /health`.

## Driving it with driver

Preconditions:

- PRED platform is running and listening on its loopback port.
- `driver doctor` reports healthy status.
- `$EVIDENCE_DIR` exists.

- **Check liveness.** Send a GET request to `/health`. Run `./.cursor/skills/verify-pred-platform/driver http GET /health -s -w "\nHTTP_STATUS: %{http_code}\n" > "$EVIDENCE_DIR/health-response.txt"`. The stdout output contains `{"status":"ok"}` and `HTTP_STATUS: 200`.
- **Verify response headers.** Query HTTP response headers for `/health`. Run `./.cursor/skills/verify-pred-platform/driver http GET /health -I -s > "$EVIDENCE_DIR/health-headers.txt"`. The header output confirms `content-type: application/json` and `content-length: 17`.
- **Method rejection.** Attempt an invalid POST request to `/health`. Run `./.cursor/skills/verify-pred-platform/driver http POST /health -s -w "\nHTTP_STATUS: %{http_code}\n" > "$EVIDENCE_DIR/health-post-405.txt"`. The output returns `HTTP_STATUS: 405`.
- **Proof.** Confirm that `$EVIDENCE_DIR/health-response.txt` and `$EVIDENCE_DIR/health-headers.txt` are created.

## Gotchas

- The `/health` endpoint is purely an HTTP service readiness check in this phase; it does not test database connection pools or external background worker queues.
- Ensure automated health pingers issue GET requests; non-GET requests will receive HTTP 405.
