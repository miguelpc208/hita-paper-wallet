# HITA synthetic paper wallet

HITA freezes dated synthetic recommendations, scores the observed signal in a Shadow Signal Book, and runs hypothetical transactions through a separate paper wallet. It ships with a fixed dataset, an imaginary security and benchmark, and a deterministic demonstration rule. All data and paper activity are synthetic.

Run the commands below from the project root with Python 3.11 or newer and SQLite 3.37 or newer with JSON support.

```bash
python -m hita_paper demo
python -m hita_paper demo --db paper-demo.sqlite
python -m hita_paper decisions --db paper-demo.sqlite
python -m hita_paper report shadow <shadow_run_id> --db paper-demo.sqlite
python -m hita_paper report wallet <wallet_run_id> --db paper-demo.sqlite
python -m hita_paper demo --as-of 2026-01-16T12:00:00Z
python -m unittest discover -s tests -v
```

The first command runs in memory. The second saves the frozen decisions and both evaluations in a new paper database. Find the run IDs in `shadow_signal_book.run_id` and `paper_wallet.run_id` in the demo JSON. The `decisions` and `report` commands open the saved paper database read-only. The `--as-of` cutoff is UTC and controls the signals and market knowledge that the evaluation can use. Replaying identical inputs reuses the saved histories.

## Reading the result

- **Shadow Signal Book.** Observes gross market returns from the next scheduled open to the horizon close, accounting for qualified splits and dividend entitlements. It shows blocked decisions and abstentions in the population. The scorecard reports maturity and the share of all decisions with a comparable directional outcome. Observational returns can identify an opportunity even when the wallet could not enter it.
- **Paper wallet.** Uses €10,000 of fictional opening cash and a long-only, all-or-none execution rule. The eligible entry is the next calendar open, using price and liquidity available at that instant. Exits at the horizon use the configured closing rule. Default settlement is same-day, with €0.50 commission per side, zero spread and slippage for the worked fixture, and zero interest on idle cash. An unavailable price, a policy block, insufficient cash, or inadequate volume can prevent execution. The engine records price sources, fills, funding movements, lots, and daily valuation marks. The preset closing auction uses a five-minute publication window. Historical marks record the run’s knowledge cutoff so later corrections can update a new valuation without revising an executed price.
- **Benchmark comparison.** The per-decision counterfactual starts with the same committed capital and applies the same timing and cost model. The wallet also reports a cash-only baseline and a passive benchmark that starts with the same wallet capital, invests the matched amounts on those dates, and keeps the balance in cash. Position return divides by committed capital. With no later deposits, whole-wallet return divides by the fictional €10,000 opening balance.
- **Policy and privacy.** The demonstration uses a separately declared fictional €100,000 risk reference for the eligible case, plus a conservative loss estimate that allows for zero recovery and transaction costs. It applies the tactical caps to that reference and blocks the second candidate with `MISSING_APPROVED_DENOMINATOR`. The approved capital-base methodology remains open. The software reads its bundled synthetic fixture and a dedicated paper SQLite file. The simulation has no brokerage, banking, or real-data integration.

## Programmatic deposit scenarios

A Python caller can pass `deposits=(PaperDeposit(id, effective_at, amount_dec),)` to `hita_paper.wallet.simulate_wallet`. Use a timezone-aware UTC instant and a positive EUR decimal string. The simulator posts the deposit to each comparison book, excludes it from investment P&L, and chains time-weighted returns at qualified pre-flow valuations. The default CLI example runs without later deposits.

## Reading a frozen decision

Each decision stores `decision_at`, `known_as_of`, horizon, disposition reasons, benchmark and cost identifiers, model and risk-policy versions, a source fixture hash, and the exact evidence observation and its publication and availability timestamps. The decision hash covers its complete canonical record. SQLite rejects in-place updates and deletes from paper history. A later clock can produce a different evaluation while preserving the earlier result and the original decision.

The demo provides a reproducible check of the economics, evidence flow and controls. Full research validation, model training, lifecycle and interface work, independently approved accounting, and assessment for any use with real data are separate open phases in the handoff.

[Verification report](PAPER_VERIFICATION.md)
