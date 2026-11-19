"""Verify standalone SQLite recovery and report continuity."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from hita_paper.cli import run_demo
from hita_paper.recovery import copy_paper_database
from hita_paper.research import read_research_run, run_research
from hita_paper.store import PaperStore
from hita_paper.wallet import read_wallet_run


class PaperRecoveryTests(unittest.TestCase):
    def test_backup_and_restore_preserve_reports_including_uncheckpointed_wal(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "paper original.sqlite3"
            demo = run_demo(source)
            with closing(sqlite3.connect(source, isolation_level=None)) as writer:
                self.assertEqual(writer.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
                writer.execute("BEGIN")
                writer.execute("SELECT count(*) FROM shadow_runs").fetchone()
                # Pin the pre-research read snapshot so WAL pages remain uncheckpointed.
                research = run_research(source)
                wal = Path(f"{source}-wal")
                self.assertGreater(wal.stat().st_size, 0)
                before = source.read_bytes(), wal.read_bytes()

                backup = root / "backup.sqlite3"
                restored = root / "restored.sqlite3"
                backed_up = copy_paper_database(source, backup)
                recovered = copy_paper_database(backup, restored)
                self.assertEqual((source.read_bytes(), wal.read_bytes()), before)
                for result, path in ((backed_up, backup), (recovered, restored)):
                    self.assertEqual(result["status"], "VERIFIED")
                    self.assertEqual(result["integrity_check"], "ok")
                    self.assertEqual(result["foreign_key_violations"], 0)
                    self.assertEqual(result["research_run_count"], 1)
                    self.assertEqual(result["size_bytes"], path.stat().st_size)
                    for suffix in ("-wal", "-shm"):
                        self.assertFalse(Path(f"{path}{suffix}").exists())

                with PaperStore(restored, readonly=True) as paper:
                    wallet = read_wallet_run(paper.db, demo["paper_wallet"]["run_id"])
                    shadow = paper.shadow_run(demo["shadow_signal_book"]["run_id"])
                    self.assertEqual(wallet, demo["paper_wallet"])
                    self.assertEqual(shadow, demo["shadow_signal_book"])
                    self.assertEqual(read_research_run(paper, research["run_id"]), research)
                    self.assertTrue({r["recommendation_id"] for r in demo["recommendations"]}.issubset(
                        {r.recommendation_id for r in paper.recommendations()}
                    ))

    def test_rejects_same_path_and_preserves_existing_destination(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "paper.sqlite3"
            original = run_demo(source)
            destination = Path(folder) / "already-exists.sqlite3"
            destination.write_bytes(b"keep this file")
            with self.assertRaisesRegex(ValueError, "must differ"):
                copy_paper_database(source, source)
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                copy_paper_database(source, destination)
            self.assertEqual(destination.read_bytes(), b"keep this file")
            with PaperStore(source, readonly=True) as paper:
                self.assertEqual(paper.shadow_run(original["shadow_signal_book"]["run_id"]), original["shadow_signal_book"])

    def test_rejects_foreign_incompatible_corrupt_and_orphaned_sources(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            good = root / "paper.sqlite3"
            run_demo(good)
            foreign = root / "foreign.sqlite3"
            with closing(sqlite3.connect(foreign, isolation_level=None)) as db:
                db.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY)")

            incompatible = root / "incompatible.sqlite3"
            copy_paper_database(good, incompatible)
            with closing(sqlite3.connect(incompatible, isolation_level=None)) as db:
                db.execute("PRAGMA user_version = 999")

            corrupt = root / "corrupt.sqlite3"
            corrupt.write_bytes(good.read_bytes()[:512])

            orphaned = root / "orphaned.sqlite3"
            copy_paper_database(good, orphaned)
            with closing(sqlite3.connect(orphaned, isolation_level=None)) as db:
                db.execute("PRAGMA foreign_keys = OFF")
                db.execute("INSERT INTO shadow_run_recommendations VALUES ('unknown-run', 'unknown-rec')")

            destination = root / "never-created.sqlite3"
            for source, message in (
                (foreign, "non-paper"), (incompatible, "schema version"),
                (corrupt, "verify the paper database"), (orphaned, "foreign key"),
            ):
                with self.subTest(source=source):
                    before = source.read_bytes()
                    with self.assertRaisesRegex(ValueError, message):
                        copy_paper_database(source, destination)
                    self.assertEqual(source.read_bytes(), before)
                    self.assertFalse(destination.exists())
                    self.assertFalse(list(root.glob(".hita-copy-*")))

    def test_competing_copies_cannot_clobber_the_same_destination(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            sources = [root / name for name in ("first.sqlite3", "second.sqlite3")]
            runs = [run_demo(source) for source in sources]
            # Give the competing snapshots distinct stored content.
            run_research(sources[1])
            target = root / "backup.sqlite3"

            def copy(source):
                try:
                    return copy_paper_database(source, target)
                except FileExistsError:
                    return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(copy, sources))
            self.assertEqual(sum(result is not None for result in results), 1)
            winner = next(index for index, result in enumerate(results) if result is not None)
            with PaperStore(target, readonly=True) as paper:
                shadow = runs[winner]["shadow_signal_book"]
                self.assertEqual(paper.shadow_run(shadow["run_id"]), shadow)
            self.assertEqual(results[winner]["research_run_count"], winner)
            self.assertEqual(copy_paper_database(target, root / "second-copy.sqlite3")["research_run_count"], winner)


if __name__ == "__main__":
    unittest.main()
