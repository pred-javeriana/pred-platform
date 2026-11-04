"""One worker per database: an exclusive operating-system lock on a file beside the database.

A run ``ejecutando`` is owned by the worker that claimed it, from the claim until it settles the
run, and the database cannot tell a live owner from a dead one: the engine pid is not recorded yet
right after the claim and is already reaped while the run is finalized. So only the worker that
holds this lock may queue interrupted runs again or claim new ones. The operating system releases
the lock when its holder exits, even when it crashes, so a run left ``ejecutando`` with the lock
free has no owner and can be resumed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class WorkerLock:
    """The lock that lets one worker at a time run the queue of a database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    @classmethod
    def for_database(cls, db_path: Path) -> WorkerLock:
        """The lock of a database, the same for every path that names it (relative, symlinked)."""
        db = db_path.resolve()
        return cls(db.with_name(f"{db.name}.worker.lock"))

    def acquire(self) -> bool:
        """Take the lock without waiting. Returns whether this worker holds it."""
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)  # not inherited by the engine
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def held_elsewhere(self) -> bool:
        """Whether another worker holds the lock now, for example ``make worker`` beside the app.

        It takes the lock and releases it at once, without creating the file. During that instant
        a worker that starts fails to take it: ``make worker`` and the app's worker try again at
        their next poll, and ``--once`` leaves the queue to the next worker that runs.
        """
        if self._fd is not None:
            return False
        try:
            fd = os.open(self.path, os.O_RDONLY)
        except OSError:  # no lock file (no worker ever ran here) or no access to it
            return False
        try:
            _lock(fd)
        except OSError:
            return True
        else:
            _unlock(fd)
            return False
        finally:
            os.close(fd)

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)


if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
