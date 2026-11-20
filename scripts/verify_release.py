"""Verify the HITA release from source or an installed wheel."""

import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory


def run_json(*args: str, expected_exit: int = 0) -> dict:
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    command = [sys.executable, *args]
    result = subprocess.run(command, capture_output=True, encoding="utf-8", env=environment)
    if result.returncode != expected_exit:
        raise RuntimeError(f"{' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return json.loads(result.stdout)


def expect(label: str, actual, expected) -> None:
    if actual != expected:
        raise RuntimeError(f"{label}: expected {expected!r}, received {actual!r}")


def verify() -> dict:
    schema = run_json("-m", "hita_a0.cli", "schema")
    expect("A0 table count", schema["tables"], 30)
    expect("A0 migration count", len(schema["migrations"]), 5)
    a0 = run_json("-m", "hita_a0.cli", "demo")
    expect("A0 consolidated NAV", a0["consolidated_nav_eur"], "18811")

    reconciled = run_json("-m", "hita_a0.cli", "reconcile", "--scenario", "matched")
    exceptions = run_json("-m", "hita_a0.cli", "reconcile", "--scenario", "breaks", expected_exit=2)
    expect("Matching statement", reconciled["status"], "RECONCILED")
    expect("Statement with exceptions", exceptions["status"], "UNQUALIFIED")

    with TemporaryDirectory(prefix="hita release verification ") as temporary:
        directory = Path(temporary)
        original = str(directory / "paper database.sqlite")
        backup = str(directory / "paper backup.sqlite")
        restored = str(directory / "restored paper.sqlite")
        demo = run_json("-m", "hita_paper", "demo", "--db", original)
        wallet = demo["paper_wallet"]
        shadow = demo["shadow_signal_book"]
        expect("Paper P&L", wallet["wallet_pnl_dec"], "7.00")
        expect("Paper matched benchmark ending NAV", wallet["benchmark_ending_nav_dec"], "10009.05")

        research = run_json("-m", "hita_paper", "research", "--db", original, "--full")
        holdout = research["predictive"]["metrics"]["HOLDOUT"]
        expect("Research holdout dates", holdout["candidate_count"], 30)
        expect("Research matched holdout outcomes", holdout["matched_count"], 11)
        expect("Ridge entries", research["economic"]["strategy_summaries"]["RIDGE"]["buy_fill_count"], 0)

        for operation, source, target in (("backup", original, backup), ("restore", backup, restored)):
            result = run_json("-m", "hita_paper", operation, "--db", source, "--to", target)
            expect(f"{operation} status", result["status"], "VERIFIED")
            expect(f"{operation} integrity check", result["integrity_check"], "ok")
            expect(f"{operation} foreign key violations", result["foreign_key_violations"], 0)

        for kind, report in (("wallet", wallet), ("shadow", shadow), ("research", research)):
            saved = run_json("-m", "hita_paper", "report", kind, report["run_id"], "--db", restored)
            expect(f"Restored {kind} report", saved == report, True)

    return {
        "status": "VERIFIED",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "a0_tables": schema["tables"],
        "reconciliation": {"matched": reconciled["status"], "breaks": exceptions["status"]},
        "paper_pnl_eur": wallet["wallet_pnl_dec"],
        "research_holdout_dates": holdout["candidate_count"],
        "research_matched_holdout_outcomes": holdout["matched_count"],
        "restored_reports": {kind: report["run_id"] for kind, report in (
            ("wallet", wallet), ("shadow", shadow), ("research", research),
        )},
    }


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), indent=2))
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        sys.exit(f"HITA release verification failed: {error}")
