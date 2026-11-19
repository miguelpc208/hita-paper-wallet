"""Isolated SQLite persistence for immutable synthetic decisions and S1 evaluations."""

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
from typing import Iterable

from .contracts import Recommendation
from .market import MarketData


PAPER_APPLICATION_ID = 0x48495450
PAPER_SCHEMA_VERSION = 1

_SCHEMA = (
    """CREATE TABLE frozen_recommendations (
        recommendation_id TEXT PRIMARY KEY,
        content_sha256 TEXT NOT NULL,
        source_snapshot_id TEXT NOT NULL,
        payload TEXT NOT NULL CHECK (json_valid(payload))
    ) STRICT""",
    """CREATE TABLE shadow_runs (
        run_id TEXT PRIMARY KEY,
        payload TEXT NOT NULL CHECK (json_valid(payload))
    ) STRICT""",
    """CREATE TABLE shadow_run_recommendations (
        run_id TEXT NOT NULL REFERENCES shadow_runs(run_id),
        recommendation_id TEXT NOT NULL REFERENCES frozen_recommendations(recommendation_id),
        PRIMARY KEY (run_id, recommendation_id)
    ) STRICT""",
)


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _validate_database(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA application_id").fetchone()[0] != PAPER_APPLICATION_ID:
        raise ValueError("Database is outside the HITA paper namespace")
    if db.execute("PRAGMA user_version").fetchone()[0] != PAPER_SCHEMA_VERSION:
        raise ValueError("Unsupported HITA paper schema version")
    try:
        db.execute("SELECT recommendation_id, content_sha256, payload FROM frozen_recommendations LIMIT 0")
        db.execute("SELECT run_id, payload FROM shadow_runs LIMIT 0")
        db.execute("SELECT run_id, recommendation_id FROM shadow_run_recommendations LIMIT 0")
    except sqlite3.OperationalError as error:
        raise ValueError("Incomplete HITA paper database") from error


@contextmanager
def _transaction(db: sqlite3.Connection):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _initialize_database(db: sqlite3.Connection) -> None:
    with _transaction(db):
        if db.execute("PRAGMA application_id").fetchone()[0] == PAPER_APPLICATION_ID:
            _validate_database(db)
            return
        if (
            db.execute("PRAGMA application_id").fetchone()[0]
            or db.execute("PRAGMA user_version").fetchone()[0]
            or db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
        ):
            raise ValueError("Database is outside the HITA paper namespace")
        for statement in _SCHEMA:
            db.execute(statement)
        for table in ("frozen_recommendations", "shadow_runs", "shadow_run_recommendations"):
            for operation in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER {table}_deny_{operation.lower()}
                    BEFORE {operation} ON {table} BEGIN
                        SELECT RAISE(ABORT, 'HITA paper history is append-only');
                    END""")
        # REPLACE can bypass DELETE triggers in external connections without recursive_triggers.
        for table, key in (("frozen_recommendations", "recommendation_id"), ("shadow_runs", "run_id")):
            db.execute(f"""CREATE TRIGGER {table}_deny_replacement
                BEFORE INSERT ON {table}
                WHEN EXISTS (SELECT 1 FROM {table} WHERE {key} = NEW.{key}) BEGIN
                    SELECT RAISE(ABORT, 'HITA paper history is append-only');
                END""")
        db.execute(f"PRAGMA application_id = {PAPER_APPLICATION_ID}")
        db.execute(f"PRAGMA user_version = {PAPER_SCHEMA_VERSION}")


class PaperStore:
    """Paper data lives only in an explicitly marked database, separate from A0."""

    def __init__(self, path: str | Path = ":memory:", *, readonly: bool = False):
        memory = path == ":memory:"
        database = None if memory else Path(path).resolve()
        if readonly:
            if database is None:
                raise ValueError("A persisted paper database is required")
            self._check_existing(database, readonly=True)
            self.db = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, isolation_level=None)
        else:
            if database is not None:
                self._check_existing(database)
            self.db = sqlite3.connect(database if database is not None else ":memory:",
                                      isolation_level=None)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys = ON")
            self.db.execute("PRAGMA recursive_triggers = ON")
            self.db.execute("PRAGMA busy_timeout = 5000")
            if not readonly and self.db.execute("PRAGMA application_id").fetchone()[0] != PAPER_APPLICATION_ID:
                _initialize_database(self.db)
            _validate_database(self.db)
            if not readonly:
                from .wallet import initialize_wallet

                initialize_wallet(self.db)
        except BaseException:
            self.db.close()
            raise

    @staticmethod
    def _check_existing(path: Path, *, readonly: bool = False) -> None:
        try:
            if path.stat().st_size == 0 and not readonly:
                return
        except FileNotFoundError:
            if readonly:
                raise
            return
        # Check only the header before any writable connection or SQLite sidecar can be opened.
        with path.open("rb") as stream:
            header = stream.read(100)
        if len(header) != 100 or not header.startswith(b"SQLite format 3\x00") or (
            int.from_bytes(header[68:72], "big") != PAPER_APPLICATION_ID
        ):
            raise ValueError("Refusing to open an existing non-paper file")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def freeze(self, recommendations: Iterable[Recommendation], market: MarketData) -> tuple[Recommendation, ...]:
        records = tuple(recommendations)
        for record in records:
            market.validate_recommendation(record)
        with _transaction(self.db):
            for record in records:
                payload = canonical_json(record.to_dict())
                existing = self.db.execute(
                    "SELECT content_sha256, payload FROM frozen_recommendations WHERE recommendation_id = ?",
                    (record.recommendation_id,),
                ).fetchone()
                if existing is not None:
                    if (existing["content_sha256"], existing["payload"]) != (record.content_sha256, payload):
                        raise ValueError(f"Frozen recommendation ID already has different content: {record.recommendation_id}")
                    continue
                self.db.execute("INSERT INTO frozen_recommendations VALUES (?, ?, ?, ?)", (
                    record.recommendation_id, record.content_sha256, record.source_snapshot_id, payload,
                ))
        return records

    def recommendations(self) -> tuple[Recommendation, ...]:
        return tuple(Recommendation.from_dict(json.loads(row["payload"])) for row in self.db.execute(
            "SELECT payload FROM frozen_recommendations ORDER BY recommendation_id"
        ))

    def save_shadow_run(self, identity: dict, outcomes: list[dict], scorecard: dict) -> dict:
        run_id = "shadow-" + sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        recommendation_hashes = identity["recommendation_hashes"]
        if sorted(outcome["recommendation_id"] for outcome in outcomes) != sorted(recommendation_hashes):
            raise ValueError("Shadow outcome population differs from the frozen inputs")
        for outcome in outcomes:
            if (
                outcome["recommendation_content_sha256"] != recommendation_hashes[outcome["recommendation_id"]]
                or outcome["evaluated_at"] != identity["evaluated_at"]
                or outcome["source_snapshot_id"] != identity["market_manifest_id"]
                or outcome["engine_version"] != identity["engine_version"]
            ):
                raise ValueError("Shadow outcome provenance differs from the run inputs")
        report = {"run_id": run_id, "inputs": identity, "outcomes": outcomes, "scorecard": scorecard}
        payload = canonical_json(report)
        with _transaction(self.db):
            previous = self.db.execute("SELECT payload FROM shadow_runs WHERE run_id = ?", (run_id,)).fetchone()
            if previous is not None:
                if previous["payload"] != payload:
                    raise ValueError("Shadow output changed without new versioned inputs")
                return report
            for recommendation_id, digest in recommendation_hashes.items():
                row = self.db.execute(
                    "SELECT content_sha256 FROM frozen_recommendations WHERE recommendation_id = ?",
                    (recommendation_id,),
                ).fetchone()
                if row is None or row["content_sha256"] != digest:
                    raise ValueError(f"Shadow input is missing or differs from its frozen record: {recommendation_id}")
            self.db.execute("INSERT INTO shadow_runs VALUES (?, ?)", (run_id, payload))
            self.db.executemany("INSERT INTO shadow_run_recommendations VALUES (?, ?)", (
                (run_id, recommendation_id) for recommendation_id in recommendation_hashes
            ))
        return report

    def shadow_run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT payload FROM shadow_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown shadow run: {run_id}")
        return json.loads(row["payload"])
