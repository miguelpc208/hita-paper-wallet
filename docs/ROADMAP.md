# HITA development roadmap

HITA is a local-first desktop research and portfolio review tool. Its goal is to let someone inspect the evidence behind a research decision, replay its hypothetical outcome against a comparable benchmark, and review portfolio risk using an approved policy. The portfolio workflow is read-only and every simulated decision stays in the paper wallet.

## Delivered

| Release | Goal | Features and evidence |
| --- | --- | --- |
| paper.1 | Verify the synthetic accounting and paper workflow end to end | A0 accounting and audit, timestamped recommendations, shadow scoring, a separate SQLite paper ledger, cost-matched benchmark and deterministic replay. 111 tests passed on Linux. |
| paper.2 | Measure out-of-sample behavior before considering more complex models | Point-in-time features and net labels, chronological ridge fits with a fixed holdout, six portfolio baselines and immutable experiment reports. 141 tests passed on Linux. The ridge strategy tied cash with zero trades across the default holdout. |

## Planned milestones

| Milestone | Goal | Features | Exit criteria |
| --- | --- | --- | --- |
| **M3 · Windows and local reliability** | Make the current software reproducible on the intended desktop | Windows/Conda startup guide, package and CLI smoke checks, Windows and Linux CI, SQLite backup and restore, project-wide diagnostic report | A fresh Windows setup runs the paper demo and research experiment. CI passes both platforms. A restored copy reproduces the original run IDs and reports. |
| **M4 · Synthetic risk and reconciliation** | Validate the accounting and risk workflow before introducing private data | Reconciliation fixtures for cash, holdings and pending transactions, clear exceptions, exposure view, allocation and reserve calculations, stable block reasons for missing or stale inputs | Independent accounting review records exceptions and dispositions. Risk boundaries, stale inputs and overlapping positions are tested. The official capital-base denominator and reserve interpretation are recorded and approved before those rules are treated as policy. |
| **M5 · Audited research data** | Test model behavior across more instruments, dates and market conditions | Permissioned point-in-time datasets and manifests, time and source validation, broader costs and liquidity scenarios, locked holdouts and model registry | Data rights and revision rules are documented. Regular and stressed results are reproducible with coverage, forecast error, drawdown, costs and matched alternatives. A result with no edge is acceptable. |
| **M6 · Local desktop review** | Make daily review possible without the command line | Local dashboard for reports, evidence, portfolio and paper results, comparison views, exports, accessible errors, versioned updates | A Windows user can open a run, inspect its evidence and policy reasons, compare it with cash and benchmark, and export it. Backup and restore work from the desktop workflow. Start with synthetic data. |
| **M7 · Optional read-only portfolio import** | Review actual holdings with user consent | Locally imported statement files, preview and reconciliation, duplicate detection, unknown cost flags and separate portfolio storage | The owner authorizes the source and privacy controls. Reimport is idempotent. Totals reconcile or exceptions remain visible. Paper and account data stay isolated. |
| **M8 · Optional local explanation with Ollama** | Assess whether local explanations improve review | Ollama adapter with an off switch, citation-linked summaries, conflicting-source and prompt-injection evaluation, deterministic report fallback | Explanations cite qualified evidence and expose uncertainty. The fixed evaluation measures accuracy and usefulness against the deterministic report. The adapter has read-only access to approved inputs. |
| **M9 · Controlled paper pilot** | Verify the full local workflow before calling it a supported product | Paper-shadow model promotion, monitoring, incident review, rollback and recovery practice | An approved evaluation protocol records outcomes and exclusions. A tested rollback works, model promotion requires human approval, and a versioned pilot report records remaining limits. |

## Dependencies and product boundaries

- **Start with M3.** M4 and M5 can progress in parallel once the release checks are in place. M6 can use synthetic data before M7 has consent or private inputs. M8 depends on a reliable evidence interface and deterministic reports. M9 requires verified recovery, risk controls and documented evaluation.
- **Accounting and economics.** Use decimal arithmetic, qualified valuations and comparable benchmarks. Report actual coverage, costs and uncertainty. Keep synthetic tests, independent accounting approval and production readiness as distinct gates.
- **Privacy and responsible use.** Keep private statements and account records local under explicit consent. Review permissions and applicable obligations before introducing a source or wider distribution. Keep simulating and portfolio review separate from real-world transaction execution.
- **Desktop choices.** Choose the interface stack after testing packaging, accessibility and local data handling on Windows. Ollama is an optional explanation feature. Godot is a candidate only if a desktop prototype meets the same usability and operational requirements.

## Next milestone checklist

- [ ] Run the released demo and research command from a fresh Windows environment.
- [ ] Add automated Windows and Linux release checks and publish the verification results.
- [ ] Back up and restore a synthetic paper database and verify that both reports replay.
- [ ] Document installation, test commands, integrity checks and recovery in one Windows guide.
