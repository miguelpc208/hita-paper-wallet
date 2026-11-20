# HITA development roadmap

HITA is a local-first desktop research and portfolio review tool. Its goal is to let someone inspect the evidence behind a research decision, replay its hypothetical outcome against a comparable benchmark, and review portfolio risk using an approved policy. The portfolio workflow is read-only and every simulated decision stays in the paper wallet.

## Delivered

| Release | Goal | Features and evidence |
| --- | --- | --- |
| paper.1 | Verify the synthetic accounting and paper workflow end to end | A0 accounting and audit, timestamped recommendations, shadow scoring, a separate SQLite paper ledger, cost-matched benchmark and deterministic replay. 111 tests passed on Linux. |
| paper.2 | Measure out-of-sample behavior before considering more complex models | Point-in-time features and net labels, chronological ridge fits with a fixed holdout, six portfolio strategies and immutable experiment reports. 141 tests passed on Linux. The ridge strategy tied cash with zero trades across the default holdout. |

## Planned milestones

The [development backlog](DEVELOPMENT_BACKLOG.md) translates these milestones into user stories, acceptance checks, and work that fits the session budget.

| Milestone | Goal | Features | Exit criteria |
| --- | --- | --- | --- |
| **M3 · Windows and local reliability** | Make the current software reproducible on the intended desktop | Windows/Conda startup guide, package and CLI smoke checks, Windows and Linux CI, SQLite backup and restore, project-wide diagnostic report | A fresh Windows setup runs the paper demo and research experiment. CI passes both platforms. A restored copy reproduces the original run IDs and reports. |
| **M4 · Synthetic risk and reconciliation** | Validate the accounting and risk workflow before introducing private data | Reconciliation fixtures for cash, holdings and pending transactions, clear exceptions, exposure view, allocation and reserve calculations, stable block reasons for missing or stale inputs | Independent accounting review records exceptions and dispositions. Risk boundaries, stale inputs and overlapping positions are tested. The official capital-base denominator and reserve interpretation are recorded and approved before those rules are treated as policy. |
| **M5 · Audited research data** | Test model behavior across more instruments, dates and market conditions | Permissioned point-in-time datasets and manifests, time and source validation, broader costs and liquidity scenarios, locked holdouts and model registry | Data rights and revision rules are documented. Regular and stressed results are reproducible with coverage, forecast error, drawdown, costs and matched alternatives. A result with no edge is acceptable. |
| **M6 · Local desktop review** | Make daily review possible without the command line | Local dashboard for reports, evidence, portfolio and paper results, comparison views, exports, accessible errors, versioned updates | A Windows user can open a run, inspect its evidence and policy reasons, compare it with cash and benchmark, and export it. Backup and restore work from the desktop workflow. Start with synthetic data. |
| **M7 · Optional read-only portfolio import** | Review actual holdings with user consent | Locally imported statement files, preview and reconciliation, duplicate detection, unknown cost flags and separate portfolio storage | The owner authorizes the source and privacy controls. Reimport is idempotent. Totals reconcile or exceptions remain visible. Paper and account data stay isolated. |
| **M8 · Local Ollama analysis and advice agent** | Help users understand research and decide what to investigate or simulate next | Opt-in local model, read-only access to approved report data, question answering, scenario comparisons, evidence-linked advice, uncertainty, and a deterministic fallback | Numeric claims match the report and citations resolve. Missing or conflicting evidence produces a qualified answer or abstention. The agent passes scope and failure tests, and a fixed benchmark compares answer quality, usefulness and elapsed time with the deterministic report. |
| **M9 · Controlled paper pilot** | Verify the full local workflow before calling it a supported product | Paper-shadow model promotion, monitoring, incident review, rollback and recovery practice | An approved evaluation protocol records outcomes and exclusions. A tested rollback works, model promotion requires human approval, and a versioned pilot report records remaining limits. |

## Dependencies and product boundaries

- **Start with M3.** M4 and M5 can progress in parallel once the release checks are in place. M6 can use synthetic data before M7 has consent or private inputs. M8 can prototype on synthetic reports after M3. Its desktop integration depends on M6, and portfolio-aware analysis depends on the policy and data approval gates in M4 and M7. M9 requires verified recovery, risk controls and documented evaluation.
- **Accounting and economics.** Use decimal arithmetic, qualified valuations and comparable benchmarks. Report actual coverage, costs and uncertainty. Keep synthetic tests, independent accounting approval and production readiness as distinct gates.
- **Privacy and responsible use.** Keep private statements and account records local under explicit consent. Review permissions and applicable obligations before introducing a source or wider distribution. Keep simulating and portfolio review separate from real-world transaction execution.
- **Desktop choices.** Choose the interface stack after testing packaging, accessibility and local data handling on Windows. Ollama is an opt-in local analysis and advice feature. Godot is a candidate only if a desktop prototype meets the same usability and operational requirements.

## Ollama agent scope

The agent answers questions such as “Why did this strategy tie cash?”, “How do costs change the comparison?”, and “What risk should I investigate before I run another paper scenario?”. Advice can suggest evidence to review, explain trade-offs, and propose hypotheses for paper testing. Each answer shows the report and as-of time it used, links material claims to evidence, and identifies missing information. HITA's deterministic engine remains the source for calculations and policy outcomes.

The first version uses synthetic reports from the local CLI. Users can keep the model off and use the full deterministic report. The agent can read selected report fields and suggest a next step, while users initiate any new simulation themselves. Access to reconciled personal holdings requires a separate opt-in, approved data scope, and review of the intended use and applicable obligations. See the [Ollama user stories and release checks](DEVELOPMENT_BACKLOG.md#m8--local-ollama-analysis-and-advice-agent).

## Verification and next work

- GitHub Actions verified the Windows and Linux builds with Python 3.11 and 3.12. Recovery preserved the wallet, shadow, and research reports and their run IDs. [M3 verification](https://github.com/miguelpc208/hita-paper-wallet/pull/1)
- The project owner reported that the tests and A0 synthetic demo integrity check passed on desktop Windows with Python 3.13.7 and SQLite 3.50.4. The desktop commands used the Python launcher. [Windows setup guide](WINDOWS_SETUP.md)
- M4-01 adds a [synthetic reconciliation review](SYNTHETIC_RECONCILIATION.md) with traceable comparisons for cash, holdings and pending obligations.
- M4-02 starts with a synthetic risk boundary table. Real-policy qualification depends on the approved capital-base denominator and reserve interpretation.
