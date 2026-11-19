# Windows setup and synthetic smoke test

This guide covers the HITA `0.2.2+paper.2` source release on Windows in Anaconda PowerShell Prompt. The release ran on Linux. A fresh Windows run supplies the evidence for [M3-01](DEVELOPMENT_BACKLOG.md#m3--windows-and-local-reliability). Use the bundled synthetic data for every command below.

## Open the source release

Download **Source code (zip)** from the [paper.2 prerelease](https://github.com/miguelpc208/hita-paper-wallet/releases/tag/v0.2.2-paper.2). Put it in Downloads or set `$zip` to the full path where you saved it. This extracts the archive into a fresh temporary folder.

```powershell
$zip = Join-Path $env:USERPROFILE 'Downloads\hita-paper-wallet-0.2.2-paper.2.zip'
$source = Join-Path $env:TEMP ('hita-paper-2-' + [guid]::NewGuid().ToString('N'))
Expand-Archive -LiteralPath $zip -DestinationPath $source
Set-Location -LiteralPath (Join-Path $source 'hita-paper-wallet-0.2.2-paper.2')
```

For a local clone, open the checkout folder containing `pyproject.toml`, `hita_a0`, `hita_paper` and `tests`. The release checks below use the defaults from tag `v0.2.2-paper.2`.

## Start a fresh Conda environment

Use an unused environment name. The source CLI runs from the project folder with Python 3.11 or newer and SQLite 3.37 or newer with JSON support. These commands use Python 3.12. Conda may need network access to install Python. HITA's synthetic commands run offline and have no third-party runtime dependencies.

```powershell
conda create --name hita-paper-m3 python=3.12 --yes
conda activate hita-paper-m3
```

Use `python` in the activated shell so the command resolves to the Conda environment. Create a separate folder for the smoke databases and verification output.

```powershell
$smoke = Join-Path $env:TEMP ('hita-smoke-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $smoke | Out-Null
$os = Get-CimInstance Win32_OperatingSystem
$runtime = python -c "import sqlite3, sys; assert sys.version_info >= (3, 11); assert sqlite3.sqlite_version_info >= (3, 37, 0); assert sqlite3.connect(':memory:').execute('SELECT json_valid(1)').fetchone()[0] == 1; print('Python ' + sys.version.split()[0]); print('SQLite ' + sqlite3.sqlite_version); print(sys.executable)"
if ($LASTEXITCODE -ne 0) { throw 'Python or SQLite runtime check failed.' }
@($os.Caption, ('Windows build ' + $os.BuildNumber), (conda --version), $runtime) |
    Set-Content -LiteralPath (Join-Path $smoke 'runtime.txt') -Encoding UTF8
Get-Content -LiteralPath (Join-Path $smoke 'runtime.txt')
```

## Run the release checks

Run the full suite, including the GOLD-001 and GOLD-012 regressions. The `paper.2` release contains 141 tests. Record the actual count, result and any failing test names from the console.

```powershell
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'The test suite failed.' }
```

Run A0 in memory. Audit a separate copy of the supplied synthetic database.

```powershell
$a0Output = python -m hita_a0.cli demo
if ($LASTEXITCODE -ne 0) { throw 'The A0 demo failed.' }
$a0 = ($a0Output -join "`n") | ConvertFrom-Json
$auditDb = Join-Path $smoke 'a0-audit.sqlite'
Copy-Item -LiteralPath 'artifacts\gold001_demo.sqlite' -Destination $auditDb
$auditOutput = python -m hita_a0.cli audit --db $auditDb
if ($LASTEXITCODE -ne 0) { throw 'The A0 audit failed.' }
$audit = ($auditOutput -join "`n") | ConvertFrom-Json
if ($a0.consolidated_nav_eur -ne '18811' -or $audit.status -ne 'PASS_LOCAL_CHECKS' -or
    $audit.tables -ne 30 -or $audit.migrations -ne 5 -or
    $audit.accepted_events -ne 12 -or $audit.accepted_postings -ne 23) {
    throw 'A0 results differ from the paper.2 synthetic reference.'
}
```

Save the paper demo and research run to one fresh paper database. The paper demo should report **€7.00** model P&L and **€10,009.05** matched benchmark NAV. The research holdout should have **30 candidates and 11 matched outcomes**. Ridge should report **zero buys**.

```powershell
$paperDb = Join-Path $smoke 'paper.sqlite'
$paperOutput = python -m hita_paper demo --db $paperDb
if ($LASTEXITCODE -ne 0) { throw 'The paper demo failed.' }
$paper = ($paperOutput -join "`n") | ConvertFrom-Json

$researchOutput = python -m hita_paper research --db $paperDb
if ($LASTEXITCODE -ne 0) { throw 'The research run failed.' }
$research = ($researchOutput -join "`n") | ConvertFrom-Json

if ($paper.paper_wallet.wallet_pnl_dec -ne '7.00' -or
    $paper.paper_wallet.benchmark_ending_nav_dec -ne '10009.05' -or
    $research.predictive_metrics.HOLDOUT.candidate_count -ne 30 -or
    $research.predictive_metrics.HOLDOUT.matched_count -ne 11 -or
    $research.economic.strategy_summaries.RIDGE.buy_fill_count -ne 0) {
    throw 'Paper or research results differ from the paper.2 synthetic reference.'
}

$walletId = $paper.paper_wallet.run_id
$researchId = $research.run_id
$walletReplay = python -m hita_paper report wallet $walletId --db $paperDb
if ($LASTEXITCODE -ne 0) { throw 'The saved wallet report could not be read.' }
$researchReplay = python -m hita_paper report research $researchId --db $paperDb
if ($LASTEXITCODE -ne 0) { throw 'The saved research report could not be read.' }
if ((($walletReplay -join "`n") | ConvertFrom-Json).run_id -ne $walletId -or
    (($researchReplay -join "`n") | ConvertFrom-Json).run_id -ne $researchId) {
    throw 'A saved run returned a different ID.'
}

$auditOutput | Set-Content -LiteralPath (Join-Path $smoke 'a0-audit.json') -Encoding UTF8
$walletReplay | Set-Content -LiteralPath (Join-Path $smoke 'wallet-report.json') -Encoding UTF8
$researchReplay | Set-Content -LiteralPath (Join-Path $smoke 'research-report.json') -Encoding UTF8
Write-Output "Smoke checks passed. Verification output: $smoke"
```

Record the Windows build, Python and SQLite versions, test result, and wallet and research run IDs for [M3-01](DEVELOPMENT_BACKLOG.md#m3--windows-and-local-reliability). The [`paper.2` research baseline](RESEARCH_BASELINE.md) describes the sample, cost assumptions and interpretation.

## Verify recovery in the M3 development checkout

The development checkout with M3 adds `scripts/verify_release.py` and the `backup` and `restore` commands. The `paper.2` ZIP predates these commands. From the development checkout root, run `python scripts/verify_release.py` for the automated checks. To exercise recovery using the `$paperDb` from the smoke test, choose two new destination files.

```powershell
$backupDb = Join-Path $smoke 'paper-backup.sqlite'
$restoredDb = Join-Path $smoke 'paper-restored.sqlite'
python -m hita_paper backup --db $paperDb --to $backupDb
if ($LASTEXITCODE -ne 0) { throw 'The paper backup failed.' }
python -m hita_paper restore --db $backupDb --to $restoredDb
if ($LASTEXITCODE -ne 0) { throw 'The paper restore failed.' }

$restoredWallet = python -m hita_paper report wallet $walletId --db $restoredDb
if ($LASTEXITCODE -ne 0) { throw 'The restored wallet report could not be read.' }
$restoredResearch = python -m hita_paper report research $researchId --db $restoredDb
if ($LASTEXITCODE -ne 0) { throw 'The restored research report could not be read.' }
if (($restoredWallet -join "`n") -cne ($walletReplay -join "`n") -or
    ($restoredResearch -join "`n") -cne ($researchReplay -join "`n")) {
    throw 'A restored report differs from its original readback.'
}
```

Both recovery commands should report `VERIFIED`. Each requires a new destination and preserves the source. Matching the restored reports confirms recovery of the two persisted synthetic runs.
