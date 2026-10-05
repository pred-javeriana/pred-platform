"""The data contract between the engine, the DAL and the views (TASK-UI-1.0-A3).

Authored in pred-docs (``diseno/contratos/``) and copied here byte for byte by
``scripts/sync_contract.py`` (``make sync-contract``): ``models.py``, ``schemas/`` and
``examples/``. ``contract.lock.json`` pins the SHA-256 of every copy and the tests verify it, so
the contract is changed in pred-docs first and synced here, never edited in place.
"""

import json
from importlib.resources import files

_LOCK = json.loads((files(__package__) / "contract.lock.json").read_text(encoding="utf-8"))

CONTRACT_VERSION: str = _LOCK["contract_version"]
CONTRACT_MAJOR: int = int(CONTRACT_VERSION.split(".", 1)[0])
