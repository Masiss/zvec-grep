# Native Codex subagent R4 pilot — readiness and owner gate

Date: 2026-11-22
Checkpoint examined: 135844edcf7baf6b481f9d8827e839fb1b76022e, branch work/r4-evidence-representation-20261119.

## Outcome

**Delegation and local tool availability are verified. No HRM2.0 answerer or evaluator was run.** No case question, private source, evidence, gold or checklist was passed to any subagent. One fresh-context reviewer received only the sanitized pilot design below. No Responses API call or private-data API billing occurred.

This prepares a possible exploratory pilot. Native subagents, as exposed in this session, do not provide enough tool isolation or parent-visible accounting to claim the frozen R4 budgets were enforced across all three arms or to make a formal trusted evaluation.

## Capability verification

- The collaboration interface is available. A fresh agent was spawned with fork_turns=none, completed a sanitized review, and did not inspect the workspace. This verifies delegation works in the current session.
- Each agent inherits the available tool surface. The spawn interface accepts task/model/reasoning/fork-turns/message, but exposes no per-agent tool allowlist or separate filesystem root.
- Native ripgrep is available (15.2.0). No zvec_grep_rg MCP tool is listed in this host.
- The MCP zvec_grep_search tool is available. The current workspace FTS index has the previously recorded storage failure; do not use it as proof the frozen R4 corpus route works.
- The R4 adapter executable is present and loads with the pinned native library. Its CLI exposes search, symbol, facts, evidence, verify, workflow and status. The prior readiness check exercised FTS/vector/hybrid on a disposable local copy of the frozen index; this is local capability evidence, not a task run.
- Arm A can use native rg and source reads. Arms B/C can invoke the real R4 adapter CLI. No arm is simulated in this report.

## Prepared exploratory pilot

Use D01, D06 and D12, selected by index spacing before seeing any answer. Keep H01–H08 sealed.

For each case, create three separate fresh-context answerers (nine total), all with the same owner-approved requested model and reasoning effort. Use fork_turns=none for every answerer. Do not pass prior conversation, gold, C3 checklist, expected outcomes, another arm’s answer, or another arm’s evidence to an answerer.

- **Arm A:** ordinary rg search and source reads only. Do not invoke the R4 adapter or zvec search.
- **Arm B:** R4 engine-managed workflow only. For each case, a separate blind planner receives only the question and permitted folder hints; freeze its plan before the paired Arm-B answerer uses the workflow. Do not expose plans to Arms A/C.
- **Arm C:** agent-directed R4 search/symbol/facts/evidence/verify operations; no planner or selector.

Frozen R4 resource targets for each answer session are at most 6 calls, 3 rounds, 32 distinct files, 64 read operations, 64 KiB delivered envelope and a 24 KiB/10-item evidence pack. The adapter can account for operations that pass through it. It cannot enforce or account for Arm A shell reads, unmediated Arm C paths, or native model turns. These values are therefore targets for the subagent pilot, not verified equal budgets.

After all nine answerers finish, use a separate fresh evaluator context for each answer packet. Each evaluator receives only its own frozen question, answer, delivered evidence pack, public correctness contract and report instructions. Do not send gold, C3 checklist, answerer transcript, peer answers or other-arm results. The existing evaluator instruction digest is 4e9e20413c8ae2172844298ed743ab24c5fb79c0a36defeb957bee0c4f28cd55; that digest is not owner approval or trusted registration. Do not register an evaluator. A subagent judge is exploratory; the existing structural grader remains the source of C1–C6/C3 structural scores, and no formal semantic PASS may be claimed without the approved trusted evaluator.

The pilot outputs do not count as a formal full-benchmark result. If any setup changes after inspecting them, preserve the three cases as calibration and preregister a new run before claiming comparative results.

## Limits that prevent formal claims

1. **Private data authorization:** native subagent inference still sends the prompt and any tool-returned source text to the Codex/OpenAI service. It is not local inference and does not bypass the private-data approval gate. No HRM2.0 content may be passed until the owner approves this recipient and scope.
2. **No hard blinding/tool isolation:** all agents share the workspace and inherit tools. Prompt instructions can say “do not read gold” and “use only this arm’s tools,” but the current interface cannot enforce either rule. Fresh context prevents conversation inheritance; it does not prevent filesystem discovery or use of another available tool.
3. **No complete parent-visible trace:** the parent sees the subagent invocation/result and can time the whole agent task, but the collaboration interface does not expose a reliable per-tool transcript, exact input/output token usage, cost, served model fingerprint or full evidence-delivery history. Agent self-reports are not independent evidence.
4. **Arm accounting differs:** B’s adapter ledger is authoritative only for adapter-mediated work. A’s rg/file reads and any work outside the adapter are not automatically reconciled. A final citation list cannot reconstruct every excerpt shown to an answerer. The existing grader requires frozen answer/pack artifacts, so manual reconstruction from final answers would not prove delivered-evidence reachability.
5. **Evaluator trust:** no evaluator is registered. A separate subagent can provide qualitative exploratory judgments, but cannot produce a trusted formal semantic verdict without owner approval and registration.
6. **Three cases:** results would be descriptive pilot evidence only, not a stable estimate or a claim of arm superiority.

To make a later pilot useful, each answerer should emit the strict answer structure plus an evidence manifest, and preserve its actual delivered pack locally. Any usage/tool-call values that the platform does not expose must be marked unknown, never estimated from final prose. If complete budget enforcement or formal blinding is required, the native subagent surface needs a mediated tool/workspace boundary before private execution; do not describe the current pilot as budget-verified.

## Independent review

One fresh-context reviewer (OpenAI GPT-6.2 Astra, high reasoning) reviewed only the sanitized facts above. Findings:

- Exploratory use can show whether each arm completes these three tasks and produces gradeable evidence; it cannot establish a comparative advantage or causal effect.
- Lack of per-agent tool/filesystem restrictions is a blocker for audited arm separation and protection from gold/peer-output leakage.
- Native subagent results do not expose sufficient usage and served identity for verified equal-cost/model claims.
- R4 adapter ledgers only cover adapter-mediated operations; final packs cannot prove what the agent was shown outside them.
- Keep the semantic evaluator exploratory unless D1 registration is explicitly approved. Report unknown usage as unknown and maintain the private-data gate.

## Current action

Prepared and stop at the owner gate. No answerer subagents were launched with private inputs. No evaluator was invoked. No benchmark, index, embedding, production default, API, source code or frozen case was changed. No push, merge or release occurred.

The next authorized action, if the owner grants the specific private-data scope, is the fixed 3×3 exploratory pilot above. Preserve its exploratory label and report the missing parent-visible usage/ACL controls; do not promote it to formal R4 evidence.
