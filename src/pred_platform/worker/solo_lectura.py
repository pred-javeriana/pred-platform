"""Whether the platform's database can be written: one answer for every action that writes.

A saved copy is served read-only to look at its results. Launching, stopping, resuming and
uploading a file all write, so each asks ``database_read_only`` on every request (a permission
change takes effect at once), and a write that still meets a database it cannot write (the
copy changed meanwhile, or a layout the check cannot see) is recognised by
``is_read_only_error``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from pred_platform.dal.lectura import immutable_wal_copy, is_wal


def database_read_only(db_path: Path) -> bool:
    """Whether SQLite cannot write the database file (``False`` while there is no file yet).

    The file needs write permission, and so do the ``-wal`` and ``-shm`` files beside it: SQLite
    creates them with the file's permissions, so reading a copy without write permission leaves
    them without it too. In a folder without write permission a database with a rollback journal
    cannot be written either, because every write creates ``<name>-journal`` beside it.
    """
    if not db_path.exists():
        return False
    if immutable_wal_copy(db_path):
        return True
    for path in (db_path, *(db_path.with_name(db_path.name + s) for s in ("-wal", "-shm"))):
        if path.exists() and not os.access(path, os.W_OK):
            return True
    return not os.access(db_path.parent, os.W_OK) and not is_wal(db_path)


def is_read_only_error(exc: sqlite3.Error) -> bool:
    """Whether SQLite refused because it cannot write or create the database's files.

    A locked database or a malformed statement is not this error.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    return code is not None and (code & 0xFF) in (sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN)


__all__ = ["database_read_only", "is_read_only_error"]
