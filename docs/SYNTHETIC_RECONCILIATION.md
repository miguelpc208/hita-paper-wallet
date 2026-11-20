# Synthetic reconciliation review

Run these commands from the project root. They compare two bundled closing statements with the same in-memory A0 ledger.

```powershell
python -m hita_a0.cli reconcile --scenario matched
python -m hita_a0.cli reconcile --scenario breaks
```

The matched fixture reports `RECONCILED`. The breaks fixture reports `UNQUALIFIED` and exits with code 2 after printing the full report. This lets a script verify qualification without discarding the explanation.

| Line | Ledger amount | Matched statement | Breaks statement |
| --- | ---: | ---: | ---: |
| Settled cash, EUR | 910.00 | 910.00 | 900.00 |
| Holdings, EQ1 shares | 9 | 9 | 8 |
| Outstanding payables, EUR | 226.00 | 226.00 | missing |
| Outstanding receivables, EUR | 248.00 | 248.00 | 248.00 |
| Settled cash, USD | absent | absent | 20.00 |

Each comparison line includes the ledger amount, observed amount, and observed-minus-expected difference where both amounts exist. A missing or unexpected line remains visible as an exception. Read the statement record ID and ledger evidence on each line to trace opening values, accepted postings, lots and unsettled obligations.

## Timing and qualification

The built-in fixtures use one synthetic account and trade-date recognition. The ledger selects accepted activity within the effective and knowledge cutoffs. A statement published after the knowledge cutoff cannot qualify the review. Outstanding payables and receivables include the effects of partial settlements and cancellations. A missing, unresolved, or unqualified input remains visible in the result.

`RECONCILED` applies to the synthetic balances in this report. Valuation, approved capital-base policy, coverage of real statements, and independent accounting sign-off require their own evidence.
