# HITA development backlog

Each story describes a user outcome and the evidence needed to accept it. Dependencies refer to the [development roadmap](ROADMAP.md). The current release includes the synthetic paper and research CLI. The stories below are planned work.

## Plan work to the available daily tokens

Use the remaining session allowance displayed by the coding client if it is available. There is no fixed daily token quota assumed here. Estimate the combined cost of reading context, editing, and verifying changes. As a starting point, leave about a quarter of the allowance for verification and a handoff. If the client does not expose token usage, use the small, medium, and multi-session sizes as a planning proxy.

| Available room | Work to select | Evidence to leave |
| --- | --- | --- |
| Small | One narrow story or one independent acceptance check. Read the relevant files and run a focused check. | A small change with its check, or a short handoff documenting the verified behavior and remaining step. |
| Medium | One complete feature slice with its regression check. Work on a shared interface or several files only if needed. | The acceptance result, changed files, and the next ready story. |
| Large or multiple sessions | Split a dependency-heavy story into independently verifiable slices. Finish each slice before starting the next. | Evidence for each completed slice and an explicit list of checks still outstanding. |

At the start of each session pick the highest-priority story whose dependencies are satisfied. Record the story ID, a single acceptance target, files in scope, and checks to run. At the end, record the outcome, commands and results, blockers, and the first action for the next session. If verification will not fit, reduce the slice or leave it open with the missing evidence identified. Reuse this handoff instead of re-reading the full project history each day.

**Current slice** is M4-01. Compare a reconciled synthetic statement and a statement with exceptions. Verify each difference against the statement row and the opening balance or ledger postings. M3 verification evidence is recorded in the [roadmap](ROADMAP.md#verification-and-next-work).

## M3 · Windows and local reliability

| Story | User need | Acceptance checks | Size and dependency |
| --- | --- | --- | --- |
| **M3-01** | As a Windows user, I can unpack the release and run the paper and research examples from Anaconda PowerShell. | A fresh environment follows the documented commands. The demo reports €7.00 P&L and research reports 11 matched holdout outcomes on the released fixtures. Record the Windows, Python, and SQLite versions. | Small for the guide. Windows confirmation is a separate check. |
| **M3-02** | As a maintainer, I can see whether the same release works on Windows and Linux. | A clean CI job on each OS runs the existing suite and both CLI smoke commands with expected numeric outputs. Job logs identify the runtime and failure. | Medium. Can run alongside M3-01. |
| **M3-03** | As a user, I can recover my saved reports after moving or restoring a database. | Back up a populated synthetic SQLite database, restore to a different path, and read both reports by original run ID. Verify database integrity and exercise a damaged or incompatible copy. | Medium. Split backup creation and restore verification if needed. |

## M4–M7 · Risk, data, desktop, and portfolio review

| Story | User need | Acceptance checks | Size and dependency |
| --- | --- | --- | --- |
| **M4-01** | As a reviewer, I can explain why cash, holdings, and pending movements fail to reconcile. | Synthetic ties and mismatches produce a trace to the input and ledger event. Unresolved differences remain visible and keep the affected report unqualified. | Medium after M3. |
| **M4-02** | As a user, I can tell which policy rule allowed or blocked a hypothetical action. | Tests cover exact limit, over-limit, missing or stale inputs, and overlapping exposure. The result links measured value, limit, source and policy version. Real-policy use depends on explicit approval of the capital-base and reserve calculation. | Multi-session. Start with synthetic cases and a boundary decision table. |
| **M5-01** | As a researcher, I can replay what a market dataset showed at a historical decision time. | An authorized, versioned dataset preserves publication times, late corrections, corporate actions, and delistings. A correction changes only evaluations whose cutoff permits it. | Multi-session after M3. Start with the source contract and one authorized fixture. |
| **M5-02** | As a researcher, I can assess whether a model beats simple alternatives after costs. | A locked holdout reports matched coverage, forecast error, after-cost return and drawdown under declared cases, with reproducible seed, data digest and cost settings. | Multi-session after M5-01. Start with baseline parity. |
| **M6-01** | As a desktop user, I can open a saved report, inspect its evidence and compare outcomes. | A local Windows workflow opens a synthetic run, shows dates, evidence and blocked cases, compares with cash and benchmark, and exports a report. | Multi-session after M3. First slice opens one saved run. |
| **M7-01** | As a portfolio owner, I can preview a local statement import before using it in analysis. | An explicitly authorized file stays local. Reimport creates no duplicate entries. Unknown acquisition costs and unreconciled amounts remain visible, and paper storage stays separate. | Multi-session after M4 and source authorization. First slice uses a synthetic statement. |

## M8 · Local Ollama analysis and advice agent

**First scenario.** A user opens a synthetic research result and asks why ridge tied cash, then asks what to check before comparing strategies again. The agent explains that there were zero ridge trades, cites the strategy and coverage fields, and suggests reviewing the matched holdout and cost assumptions. The user can open those facts in the underlying report.

| Story | User need | Acceptance checks | Size and dependency |
| --- | --- | --- | --- |
| **M8-01 · Evidence intake** | As a user, I can choose the report Ollama will analyze and see which information it receives. | An allowlisted input packet contains the selected synthetic run ID, as-of time, financial facts and evidence references. A snapshot test covers the input fields and excludes unrelated records. | Small after M3. First M8 slice. |
| **M8-02 · Local connection** | As a user with Ollama installed, I can enable a local model or keep using the deterministic report. | A user-selected model works through the local Ollama endpoint. Off mode makes zero model calls. Missing model, service unavailable, timeout and malformed response each leave the report usable. Automated tests use a stub, followed by a Windows smoke test with an installed model. | Medium after M8-01. |
| **M8-03 · Analysis and advice** | As a user, I can ask why an outcome occurred and get a practical suggestion for what to review or simulate next. | Each material factual claim links to an allowlisted field or source. Figures match deterministic outputs. The response identifies whether it covers a hypothetical scenario, relevant risks, and uncertainties. A missing source yields a qualified response or abstention. Suggested checks and paper hypotheses require user initiation. | Medium after M8-02. Split explanation from advice if needed. |
| **M8-04 · Evaluation and resource controls** | As a user, I can judge whether the agent adds enough value to turn it on. | A fixed question set compares it with the deterministic report for citation validity, numeric consistency, usefulness, contradictions, unsupported advice and refusal to follow instructions embedded in evidence. Record model version, prompt version, runtime, and reported input and output tokens where available. Exercise bounded context and timeout behavior, with fallback when evidence does not fit. | Multi-session after M8-03. Start with one reference question and one failure case. |
| **M8-05 · Desktop and consent** | As a desktop user, I can open the source of each advice item and control whether selected portfolio facts are in scope. | Desktop explanations link back to source fields, show model and as-of time, and can be disabled. Any portfolio-aware mode passes explicit local-data consent and approved-use review before it can receive reconciled holdings. | Multi-session after M6 and M8-04. Portfolio scope also depends on M4 and M7. |

The initial advice scope is research interpretation, risk questions and paper scenarios. Examples include checking costs against a matched benchmark or rerunning a paper simulation under a narrower risk budget. The agent has read-only access to authorized report data. It cannot alter the ledger, approve a policy decision or execute a transaction. Review applicability of financial advice and suitability requirements before expanding the intended use to personalized recommendations. Ollama context and output limits are runtime settings, separate from the coding client's daily token allowance.

## M9 · Controlled paper pilot

| Story | User need | Acceptance checks | Size and dependency |
| --- | --- | --- | --- |
| **M9-01** | As a pilot reviewer, I can approve a specific candidate for paper shadowing and return to the previous version. | Record the approved version, evaluation window, coverage rules and rollback trigger. Exercise rollback and produce a report with admitted, blocked, missing and failed cases. | Multi-session after the documented risk, research and recovery gates. |
