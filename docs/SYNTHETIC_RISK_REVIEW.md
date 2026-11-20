# Synthetic tactical risk review

The paper wallet explains its risk decisions using the measured amount, comparison operator, limit, denominator, rule outcome, and reason code. The pre-entry policy and paper fill checks use the same synthetic tactical limit calculations. The fill checks use the opening auction that the wallet can observe at entry.

## Run the examples

From the project root in PowerShell:

```powershell
py -3.13 -m hita_paper risk --scenario at-limit
py -3.13 -m hita_paper risk --scenario over-limit
py -3.13 -m hita_paper risk --scenario missing-base
py -3.13 -m hita_paper risk --scenario stale-exposure
py -3.13 -m hita_paper risk --scenario overlap
```

| Scenario | Expected decision | Detail |
| --- | --- | --- |
| At limit | `ALLOW` | €150 per trade, €2,000 for the position, and €600 aggregate risk all equal their respective limits. |
| Over limit | `BLOCK` | Each measure exceeds its limit by one cent. |
| Missing base | `BLOCK` | Denominator-dependent limits are unknown. |
| Stale exposure | `BLOCK` | A stale mark leaves required exposure measurements unknown. |
| Overlap | `BLOCK` | €1,950 already held plus €100 proposed gives €2,050 in one instrument. €451 existing risk plus €150 proposed gives €601 aggregate risk. |

The examples use declared inputs and a fictional €100,000 denominator where it is available. They exercise inclusive boundaries of 0.15% per trade, 2% per tactical position, and 0.60% aggregate tactical risk. The examples return JSON for each outcome.

## Inspect a paper run

Run `python -m hita_paper demo --db paper-demo.sqlite` and look under `paper_wallet.signals[].risk_review`. When a review takes place, it records the decision stage, relevant clocks, applicable rule results, and evidence references. A frozen decision can be blocked before an entry attempt. Missing or stale market data can leave a proposed entry unfilled. The review identifies which stage produced the outcome.

The entry check compares planned loss with the synthetic zero-recovery amount, including costs. It uses aggregate open-lot risk and same-instrument exposure. An allowed review still requires cash and qualifying auction liquidity for a paper fill. Saved wallet reports with the previous engine version remain readable by their original run IDs. Reruns with the new engine produce versioned reports.

These checks apply to the synthetic tactical paper policy. Applying capital-base and emergency-reserve rules to real portfolios requires the authoritative denominator, qualifying-capital and expense definitions, and policy approval. Core and Satellite allocation review remains a separate step.
