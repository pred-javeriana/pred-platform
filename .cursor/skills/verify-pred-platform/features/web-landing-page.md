# Web landing page

The web landing page serves as the user entry point for the PRED demand forecasting platform. In the current platform phase, it presents a server-rendered Jinja2 HTML interface displaying the application brand, document title, and initial construction status.

## Sub-features

- `landing-render` delivers a server-rendered HTML page at `GET /` with HTTP status 200.
- `landing-title` displays the document title `PRED — Plataforma de Pronóstico de Demanda` in the browser window.
- `landing-card` presents a centered white card containing the primary level 1 heading `PRED`.
- `landing-status` informs the user of the initial status with the text `Plataforma en construcción`.

## How to get to it (user POV)

- Open `http://localhost:<port>/` in any standard web browser.
- Run `./.cursor/skills/verify-pred-platform/driver browser open /` to navigate using automated browser tools.
- Send an HTTP `GET /` request using `curl` or any HTTP client.

## Driving it with driver

Preconditions:

- The PRED platform instance is launched and responding on its configured loopback port.
- `./.cursor/skills/verify-pred-platform/driver doctor` reports healthy status.
- Evidence directory `$EVIDENCE_DIR` is initialized.

- **Navigate to root.** Open the base URL in the browser. Run `./.cursor/skills/verify-pred-platform/driver browser open /`. The browser loads the page and reports page title `PRED — Plataforma de Pronóstico de Demanda`.
- **Verify accessibility structure.** Capture the semantic accessibility tree. Run `./.cursor/skills/verify-pred-platform/driver browser snapshot`. The output contains a RootWebArea titled `PRED — Plataforma de Pronóstico de Demanda`, a heading `PRED` with level `1`, and static text `Plataforma en construcción`.
- **Capture visual screen.** Save a screenshot of the rendered page. Run `./.cursor/skills/verify-pred-platform/driver browser screenshot "$EVIDENCE_DIR/landing-page.png"`. The saved image depicts a centered card container with title `#1a1a2e` over a `#f5f5f5` background.
- **Inspect HTTP response.** Fetch the raw HTTP response headers and status code. Run `./.cursor/skills/verify-pred-platform/driver http GET / -i > "$EVIDENCE_DIR/landing-http.txt"`. The status line indicates `HTTP/1.1 200 OK` and the header `content-type` indicates `text/html; charset=utf-8`.
- **Proof retention.** Verify that `$EVIDENCE_DIR/landing-page.png` and `$EVIDENCE_DIR/landing-http.txt` exist and are populated.

## Gotchas

- The current implementation is an initial scaffold; interactive forms and navigation links have not yet been wired to the root view.
- Assert against rendered DOM and accessibility nodes rather than static file contents.
- Fast successive browser reloads may require waiting for DOM hydration if assets are updated in future iterations.
