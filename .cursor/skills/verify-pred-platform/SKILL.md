---
name: verify-pred-platform
description: "Launch, doctor, and drive the PRED platform web application and its HTTP surface with isolated server processes and browser automation."
---

# Verify PRED Platform

The primary user-facing surface of `pred-platform` is a server-rendered FastAPI web application with Jinja2 HTML templates and HTTP endpoints. The application is in early platform construction: the root URL serves an initial landing status view (`PRED — Plataforma en construcción`), and HTTP endpoints provide a health probe (`/health`) and OpenAPI discovery (`/openapi.json`). Read [the feature map](features/README.md) before choosing a verification path.

## Launch

Run from the repository root. Always launch on a dynamic or isolated loopback port so concurrent verification runs never collide. Do not attach to a pre-existing or external server.

Using the provided driver helper:

```bash
./.cursor/skills/verify-pred-platform/driver launch
```

Or manually:

```bash
RUN_ID="$(date +%s)-$$"
STATE_DIR="/tmp/pred-platform-verify-$RUN_ID"
EVIDENCE_DIR="/tmp/pred-platform-evidence-$RUN_ID"
mkdir -p -m 700 "$STATE_DIR" "$EVIDENCE_DIR"

PORT=$(python3 -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()")
URL="http://127.0.0.1:$PORT"

# Ensure dependencies are installed
uv sync --extra dev

# Start server in background detached from subshell session
PYTHON_BIN="$(pwd -P)/.venv/bin/python"
setsid "$PYTHON_BIN" -m uvicorn pred_platform.app.main:app --host 127.0.0.1 --port "$PORT" </dev/null > "$STATE_DIR/uvicorn.log" 2>&1 &
SERVER_PID=$!

# Wait for server readiness probe
for i in {1..30}; do
  if curl -s -f "$URL/health" >/dev/null 2>&1; then
    echo "Ready at $URL (PID $SERVER_PID)"
    break
  fi
  sleep 0.2
done
```

Readiness is proven when `GET $URL/health` returns HTTP 200 with `{"status":"ok"}`. If startup fails, consult `$STATE_DIR/uvicorn.log` and execute Cleanup immediately.

## Doctor

Before driving features, execute a read-only check to confirm the instance is healthy and owned by this verification run:

```bash
./.cursor/skills/verify-pred-platform/driver doctor
```

Doctor enforces four read-only invariant checks:
1. **Process liveness:** The recorded `SERVER_PID` is alive and active.
2. **Health probe:** `GET $URL/health` returns HTTP 200 with JSON payload `{"status":"ok"}`.
3. **Landing page:** `GET $URL/` returns HTTP 200 and `content-type: text/html; charset=utf-8`.
4. **Template rendering:** Rendered HTML contains the heading `PRED` and the notice `construcción`.

If any doctor check fails, do not proceed with driving. Stop and inspect logs or run cleanup.

## Drive

The driver harness supports driving the web interface via browser automation (`chrome-devtools-axi` connected to an isolated headless Chromium) and driving the HTTP surface via `curl`.

### 1. Browser automation (Web UI)

```bash
# Open landing page in headless browser
./.cursor/skills/verify-pred-platform/driver browser open /

# Capture accessibility snapshot (semantic roles and headings)
./.cursor/skills/verify-pred-platform/driver browser snapshot

# Capture full page screenshot
./.cursor/skills/verify-pred-platform/driver browser screenshot "$EVIDENCE_DIR/landing.png"
```

Expected ARIA hierarchy:
- `RootWebArea "PRED — Plataforma de Pronóstico de Demanda"`
  - `heading "PRED" [level=1]`
  - `StaticText "Plataforma en construcción"`

### 2. HTTP surface

```bash
# Query health probe
./.cursor/skills/verify-pred-platform/driver http GET /health

# Fetch OpenAPI specification
./.cursor/skills/verify-pred-platform/driver http GET /openapi.json

# Test unrouted paths
./.cursor/skills/verify-pred-platform/driver http GET /nonexistent -s -w "\nHTTP_STATUS: %{http_code}\n"
```

## Evidence

All proof artifacts must be saved into `$EVIDENCE_DIR` (`/tmp/pred-platform-evidence-$RUN_ID`). Proof artifacts are preserved during cleanup and survive instance teardown.

Standards for proof:
- **Web UI proof:** Accessibility snapshot (`.txt`) and screenshot (`.png`) showing window title, card container, and status message.
- **HTTP proof:** Request method and path, response headers, response payload (`.json` or `.html`), and HTTP status code.
- **Side effects:** Observe actual network responses and exit codes; do not substitute internal unit tests or mocks for the live HTTP surface.

Example artifact collection:

```bash
EVIDENCE_DIR="/tmp/pred-platform-evidence-$RUN_ID"
mkdir -p "$EVIDENCE_DIR"

# Capture HTTP response and status
./.cursor/skills/verify-pred-platform/driver http GET / -i > "$EVIDENCE_DIR/landing-http.txt"

# Capture browser accessibility tree
./.cursor/skills/verify-pred-platform/driver browser snapshot > "$EVIDENCE_DIR/landing-snapshot.txt"

# Capture visual rendering
./.cursor/skills/verify-pred-platform/driver browser screenshot "$EVIDENCE_DIR/landing.png"
```

## Cleanup

Cleanup stops the application server, terminates any associated headless browser session, and removes temporary state directories. Crucially, it leaves the `$EVIDENCE_DIR` directory intact.

```bash
./.cursor/skills/verify-pred-platform/driver cleanup
```

Or manually:

```bash
# Terminate server process
if kill -0 "$SERVER_PID" 2>/dev/null; then
  kill -TERM "$SERVER_PID" 2>/dev/null || true
  sleep 0.5
  kill -9 "$SERVER_PID" 2>/dev/null || true
fi

# Stop browser session if started
if [[ -n "${CHROME_PID:-}" ]] && kill -0 "$CHROME_PID" 2>/dev/null; then
  kill -TERM "$CHROME_PID" 2>/dev/null || kill -9 "$CHROME_PID" 2>/dev/null || true
fi

# Remove runtime state, preserve proof
rm -rf "$STATE_DIR"
test -d "$EVIDENCE_DIR" && ls -la "$EVIDENCE_DIR"
```

## Helpers

The verification skill provides an executable driver at `./.cursor/skills/verify-pred-platform/driver`:

```text
Usage: driver <command> [arguments...]

Commands:
  launch [--port <port>]    Start an isolated pred-platform instance in the background
  doctor                    Perform read-only diagnostic check on running instance
  http <method> <path> ...  Issue HTTP request with curl against the instance
  browser <cmd> [args...]   Drive browser session using chrome-devtools-axi
  cleanup                   Shut down server and browser; preserve evidence artifacts
  status                    Print connection details and active run info
  help                      Show this help message
```

Environment variables recognized by `driver`:
- `PRED_PLATFORM_RUN_ID`: Custom identifier for the verification session.
- `PRED_PLATFORM_PORT`: Port to bind the server (defaults to a dynamic free port).
- `PRED_PLATFORM_STATE_DIR`: Directory storing runtime PIDs and logs (defaults to `/tmp/pred-platform-verify-$RUN_ID`).
- `PRED_PLATFORM_EVIDENCE_DIR`: Directory where proof artifacts are retained (defaults to `/tmp/pred-platform-evidence-$RUN_ID`).
