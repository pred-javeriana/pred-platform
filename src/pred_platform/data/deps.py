"""FastAPI dependency that hands a view the application's data repository."""

from __future__ import annotations

from typing import cast

from starlette.requests import Request

from pred_platform.data.repository import DataRepository


def get_repository(request: Request) -> DataRepository:
    """Use as ``Depends(get_repository)``; the repository is built once in ``create_app``."""
    return cast(DataRepository, request.app.state.repository)
