"""The data contract between the engine, the DAL and the views (TASK-UI-1.0-A3).

Authored in pred-docs (``diseno/contratos/``) and copied here byte for byte by
``scripts/sync_contract.py`` (``make sync-contract``): ``models.py`` (v1), ``models_v2.py`` (v2),
``schemas/`` and ``examples/``. ``contract.lock.json`` pins the SHA-256 of every copy and the tests
verify it, so the contract is changed in pred-docs first and synced here, never edited in place.

v2 is the read of M3's published evidence (M4); v1 keeps the ingestion, M2 selection, run status
and capability documents. Each document carries the version of its own major.
"""

import json
from importlib.resources import files

_LOCK = json.loads((files(__package__) / "contract.lock.json").read_text(encoding="utf-8"))

CONTRACT_VERSIONS: dict[int, str] = {
    int(major.removeprefix("v")): version for major, version in _LOCK["contract_versions"].items()
}
CONTRACT_VERSION: str = CONTRACT_VERSIONS[1]
"""Version of the v1 documents (ingestion, selection, run status, capabilities)."""
M3_CONTRACT_VERSION: str = CONTRACT_VERSIONS[2]
"""Version of the v2 documents: M3's published evidence."""
