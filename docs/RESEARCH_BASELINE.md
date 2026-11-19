# Synthetic walk-forward research baseline

Run the experiment from the project root with Python 3.11 or newer and SQLite 3.37 or newer with JSON support.

```powershell
python -m hita_paper research
$run = python -m hita_paper research --db research.sqlite | ConvertFrom-Json
python -m hita_paper report research "$($run.run_id)" --db research.sqlite
python -m hita_paper research --full > research-report.json
python -m unittest discover -s tests -v
```

The default command prints a summary and uses an in-memory database. Pass `--db` to keep a local report and replay it by the returned `run_id`. `--full` includes the dataset, dated predictions, fitted coefficients and evidence. A cutoff specified with `--as-of` changes the data available to an evaluation. It leaves the holdout boundary fixed. `--horizon-sessions` changes the experiment's holding horizon and run identity.

## Protocol

- Each session's decision is timestamped 30 minutes after its scheduled close. The asset, benchmark and feature window use values released by that clock. Five close-to-close intervals require six consecutive scheduled closes. Returns incorporate known splits and gross dividend entitlements. Relative strength is the asset return less the benchmark return. Realized volatility is the sample standard deviation of the asset's daily total returns without annualization. Unqualified prices stay visible as data gaps.
- The five-session target is the difference between the paper trade's net return and its matched benchmark's net return. Both start from the next scheduled opening auction under the paper engine's cost and sizing rules. Each training label comes from its own single-opportunity paper run. The label is eligible only after the original scheduled closing auction's publication window. Missing fills, missing benchmarks and censored exposures retain their status without a numeric label.
- Development uses expanding chronological fits. Training uses labels published strictly before the first of two full embargoed sessions. The final holdout starts on 16 March 2026 at 16:30 UTC and uses one fit from earlier data. All holdout outcomes are excluded from training. The development scorecard also excludes labels published on or after 12 March at 00:00 UTC, while retaining their rows for audit. Feature scales, ridge penalty, decision thresholds and the random seed are fixed. Accuracy, mean absolute error and root mean square error use the same matched rows. A forecast of zero has neutral direction.
- Economic comparisons use the same €10,000 starting cash, dates and fee schedule. Policy-only selects all eligible candidates. The seeded random rule assigns each candidate a fixed binary draw. Momentum and ridge select candidates with positive scores. The shared paper ledger rechecks affordability, aggregate exposure and auction liquidity for the resulting portfolio. The independent passive benchmark holds one benchmark unit and leaves the rest in cash. Each trading strategy also has its own matched-capital benchmark. The separate shadow scorecard reports gross outcomes.
- The synthetic policy uses the existing fictional €100,000 risk reference and blocks announced delistings within the proposed exposure window. Known and unknown risks remain visible in coverage. The saved report records the manifest, evidence hashes, training cutoffs and paper run IDs.

## Default run

Of 79 decision dates, 30 were in the holdout. Nineteen had qualified features and 11 had a matched net label. The other 19 included five announced-delisting blocks, three unfilled opportunities and 11 dates with insufficient data.

| Forecast | Holdout MAE | Holdout RMSE |
|---|---:|---:|
| Ridge | 3.70 pp | 5.41 pp |
| Relative momentum | 3.94 pp | 4.96 pp |
| Zero excess | 4.93 pp | 7.34 pp |

| Simulation | Filled entries | Ending NAV |
|---|---:|---:|
| Cash | 0 | €10,000.00 |
| Policy only | 11 | €9,963.79 |
| Seeded random | 4 | €9,988.09 |
| Relative momentum | 5 | €9,994.70 |
| Ridge | 0 | €10,000.00 |
| Passive benchmark, one unit | 1 | €10,000.01 |

Ridge's mean absolute error was lowest and momentum's root mean square error was lowest. Ridge selected zero trades, so its wallet matched cash. All 11 comparable outcomes were negative and the holding windows overlap. These synthetic results check the research and accounting workflow. Evidence of predictive edge requires a wider, licensed point-in-time dataset and independent evaluation.

Verification ran all 141 tests on Linux, including the 111 tests from the published release. A saved default run replayed byte-for-byte. Windows execution remains unverified.
