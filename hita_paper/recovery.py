"""Verified snapshots of the isolated paper database."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from .store import PaperStore


def _verify_snapshot(path: Path) -> dict:
    with PaperStore(path, readonly=True) as snapshot:
        db = snapshot.db
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Paper database integrity check failed: {integrity}")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Paper database contains foreign key violations")

        research_exists = db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'research_experiments'"
        ).fetchone()
        return {
            "integrity_check": integrity,
            "foreign_key_violations": 0,
            "wallet_run_count": db.execute("SELECT count(*) FROM wallet_runs").fetchone()[0],
            "shadow_run_count": db.execute("SELECT count(*) FROM shadow_runs").fetchone()[0],
            "research_run_count": (
                db.execute("SELECT count(*) FROM research_experiments").fetchone()[0] if research_exists else 0
            ),
        }


def copy_paper_database(source: str | Path, destination: str | Path) -> dict:
    """Create a standalone, verified copy at a fresh path.

    Both backup and restore use SQLite's online backup API. The destination
    becomes visible only after verification and can never replace an existing file.
    """
    source_path = Path(source).resolve(strict=True)
    requested = Path(destination)
    destination_path = requested.parent.resolve(strict=True) / requested.name
    if source_path == destination_path:
        raise ValueError("Source and destination must differ")
    if os.path.lexists(destination_path):
        raise FileExistsError(f"Destination already exists: {destination_path}")

    try:
        with PaperStore(source_path, readonly=True) as origin:
            with TemporaryDirectory(prefix=".hita-copy-", dir=destination_path.parent) as folder:
                stage = Path(folder) / "paper.sqlite3"
                stage.touch(mode=0o600, exist_ok=False)
                with closing(sqlite3.connect(stage, isolation_level=None)) as target:
                    origin.db.backup(target)
                    journal_mode = target.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
                    if journal_mode != "delete":
                        raise ValueError("Could not create a standalone SQLite snapshot")

                verification = _verify_snapshot(stage)
                size = stage.stat().st_size
                if os.name == "nt":
                    # On Windows rename fails when the destination exists.
                    stage.rename(destination_path)
                else:
                    # A hard link publishes atomically without replacing another writer's copy.
                    os.link(stage, destination_path)
    except sqlite3.DatabaseError as error:
        raise ValueError(f"Could not verify the paper database: {error}") from error

    return {
        "status": "VERIFIED",
        "source": str(source_path),
        "destination": str(destination_path),
        "size_bytes": size,
        **verification,
    }
