# Windows GOLD-012 test teardown correction (v0.2.1)

**Symptom:** `PermissionError: [WinError 32] ... race.sqlite` at the end of `test_GOLD_012_overrun_and_concurrent`. The two settlement outcomes had already been compared; the error occurred during cleanup of `tempfile.TemporaryDirectory`.

**Root cause:** `self.addCleanup(verify.close)` schedules the SQLite connection closure at the end of the entire unittest test method, after the `TemporaryDirectory` context manager attempts to remove the database file. Windows rejects deletion of an open SQLite database.

**Correction:** Use `try/finally` to call `verify.close()` before leaving the `TemporaryDirectory` context. Confirm both concurrent settlement threads terminated before inspecting the resulting database.

**What changed:** `tests/test_gold.py` only, plus documentation and package version. No financial ledger, schema, or input data changes.

**Re-test on Windows:**

```powershell
conda activate hita-a0
cd "<path-to-HITA-A0>"
python -m unittest discover -s tests -v
python -m hita_a0.cli audit --db artifacts\gold001_demo.sqlite
```

Expected when the environment is otherwise healthy: 62 tests, `OK`; audit classification `SYNTHETIC_ONLY` and `PASS_LOCAL_CHECKS`.

Local Linux test result is not proof of Windows success. No independent certification is claimed.
