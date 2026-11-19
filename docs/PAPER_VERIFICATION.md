# HITA paper-wallet verification

The first runnable increment covers point-in-time synthetic prices and evidence, immutable recommendations, shadow outcomes and a separate paper wallet. The project includes a local Python interface and a JSON CLI. It uses only the Python standard library at runtime.

## Results of the synthetic demonstration

| Metric | Simulated recommendation | Matched benchmark | Cash-only |
|---|---:|---:|---:|
| Capital committed to the comparison | €100.50 | €100.50 | €100.50 |
| Net gain on the compared capital | €7.00 | €9.05 | €0.00 |
| Net return on that capital | 6.9652% | 9.0050% | 0.0000% |
| Ending value from a €10,000 wallet | €10,007.00 | €10,009.05 | €10,000.00 |
| Whole-wallet return | 0.0700% | 0.0905% | 0.0000% |

The eligible ADD entered at €100.00 on 16 January and closed at €108.00 on 13 February 2026. The benchmark entered at €100.00 and closed at €110.05 over the same interval. Both paid €0.50 at entry and exit. The trade was profitable and trailed the matched benchmark by about 2.04 percentage points on committed capital. The matched portfolio keeps uncommitted capital in zero-yield cash.

AVOID compared zero-yield cash with a 10% fall in the synthetic stock and generated zero paper orders. The second ADD remained visible in the shadow book and generated zero fills after `MISSING_APPROVED_DENOMINATOR`. ABSTAIN remained visible without a directional claim. All four scenarios matured and three had directional comparisons. Two scenarios intentionally repeat the same market path to exercise different policy decisions, so the scorecard illustrates coverage and counterfactual outcomes.

## Verification performed

- **111 tests passed on Linux** on Python 3.12.14 and SQLite 3.53.1. The suite includes all 63 original A0 tests and 48 additions covering the synthetic market and decision contracts, shadow scoring, wallet cases `PAPER-001` through `PAPER-020` under the supported simulation scope, and integrated replay. The A0 Python sources, five migrations and four original test files matched the uploaded ZIP byte-for-byte. [Full test log](../artifacts/paper_wallet_tests.txt)
- GOLD-001 read-only audit on a copy returned `PASS_LOCAL_CHECKS`, with 30 tables, five migrations, 12 accepted events, 23 postings and zero SQLite integrity or foreign-key issues. The audit reported zero independent oracle approvals and unverified original source artifact bytes. [Audit output](../artifacts/a0_baseline_verified.json)
- Persistent CLI replay returns identical JSON. Earlier runs and decisions remain available after a later evaluation. Later corrections can change a versioned shadow result or retrospective valuation while the original execution retains the contemporaneous auction observation. [Demo output](../artifacts/paper_wallet_demo.json)
- Tested isolation by passing a copy of the A0 database to both paper read and write interfaces. The paper interfaces rejected it without changing its bytes or creating SQLite sidecars. A concurrency stress check returned identical results for 60 overlapping replays against 12 fresh databases.
- The upload passed ZIP integrity and all 69 JSON manifest entries matched. The accompanying text manifest contains four outdated documentation hashes and nine entries for absent compiled Python files. The separate original A0 source archive was outside the upload.

## Operating assumptions and open gates

The paper wallet uses EUR, long-only scheduled auctions, all-or-none fills, fixed same-day settlement, configurable costs and fictional capital. Synthetic positive deposits through the Python interface are posted to both books, excluded from gains, and time-weighted using qualified pre-flow marks. Withdrawals and money-weighted returns remain outside this increment. Discretionary reductions, multiple currencies, and full portfolio research experiments remain open. When a delisted holding lacks proceeds, its outcome is censored and remains in population reporting.

The synthetic loss budget uses a separately declared €100,000 risk reference with a full-loss and fee assumption. The approved capital-base denominator, the Windows/Conda run, independent oracle sign-off, licensed data, live-data compliance review, trained-model validation, and UI and HTTP delivery remain open. A0 keeps its original accounting and its own database. The paper prototype uses only synthetic inputs and has no broker or bank integration.
