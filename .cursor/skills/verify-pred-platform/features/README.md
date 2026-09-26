# PRED platform verification map

This directory is the maintained source for verifying the user-facing behavior of the PRED platform. Read the index before driving the application, then use the matching feature file as the recipe.

## Baseline preconditions

- Launch the PRED platform on loopback using a dynamic or unassigned port (e.g. `http://127.0.0.1:<port>`).
- Set `PRED_PLATFORM_STATE_DIR=/tmp/pred-platform-verify-$RUN_ID` and `PRED_PLATFORM_EVIDENCE_DIR=/tmp/pred-platform-evidence-$RUN_ID` so concurrent verification runs do not collide or share state.
- Ensure dependencies are synced (`uv sync --extra dev`).
- Run `./.cursor/skills/verify-pred-platform/driver doctor` and require all four invariant checks (PID, `/health`, `/`, template rendering) to pass.
- Never drive an instance that was not started by this verification run.

## Driving conventions

- Start every recipe from the baseline state unless its preconditions say otherwise.
- Prefer ARIA roles and accessible names (e.g., `heading "PRED" [level=1]`) over arbitrary CSS selectors or coordinates.
- Treat every command as literal; keep flags and quoted strings unchanged.
- Drive browser interactions through `./.cursor/skills/verify-pred-platform/driver browser <command>`.
- Drive HTTP interactions through `./.cursor/skills/verify-pred-platform/driver http <method> <path> [flags...]` or direct curl against `$URL`.
- Do not remove proof artifacts during cleanup; proof must survive teardown.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final screen.
- Web UI proof includes an accessibility snapshot and a screenshot with the PRED window and card title visible.
- HTTP proof includes the command, HTTP status code, response headers, and response payload.
- Record the feature ID and entry point used with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order:

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with <harness>` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

Keep implementation details out of the map. Name only user paths, stable handles, required state, commands, and observable proof.

## Features

- [Web landing page](./web-landing-page.md) covers browser rendering, accessibility hierarchy, title, and visual layout.
- [Service health probe](./service-health-probe.md) covers HTTP liveness check and JSON response contract.
- [OpenAPI specification](./openapi-specification.md) covers schema discovery and registered route contracts.
- [Unrouted endpoints and disabled documentation](./unrouted-endpoints.md) covers 404 error responses and verification that interactive documentation endpoints are disabled.
