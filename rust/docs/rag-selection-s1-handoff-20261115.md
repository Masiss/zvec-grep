# RAG-S1 Selection feasibility — handoff 2026-11-15

Status: **NO_GO.** Selection remains frozen. The exact-symbol fallback is useful
but cannot reach the pre-registered gate; structural member/block expansion adds
cost without dev recall. No production or Discovery change was made.

## 1. Branch and artifacts

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/rag-s1-20261115` |
| Branch | `work/rag-s1-20261115` |
| Base | `f93d0499cccb450065302f0def9ca87ea1096983` (`work/rag-requal-20261115`) |
| External root | `/home/masis/.cache/zvec-grep/rag-s1-20261115/` |
| Dev cases | `27626109…a4a9c311` (12 cases / 25 spans) |
| Held-out cases | `2d81048e…e38f6d` (8 cases / 20 spans) |
| Dev probe | `84b7593a…9188e27` |
| Held-out probe | `74a83698…255c4c51` |
| Corpus manifest | `c706c8dd…6359cdb` |
| Index manifest | `d7052d3f…473fc4a` (v3) |
| Tokenizer | `107bbd…f3d45` |

Receipts:

| Receipt | SHA-256 |
|---|---|
| `selection-baseline-dev.json` | `5f9a504a…79ebf5` |
| `selection-exact-dev.json` | `97297dfc…872305` |
| `selection-structural-dev.json` | `65bf94e2…f1bb6` |
| `selection-oracle-dev.json` | `14b6bbab…41dcc` |
| `selection-baseline-heldout.json` | `88fa7d61…dc48b` |
| `selection-exact-heldout.json` | `275b5ee0…13c9e8` |
| `selection-structural-heldout.json` | `f203200e…7ad073` |
| `selection-oracle-heldout.json` | `46727b8e…9cdfb7` |
| rerun (determinism) | `65bf94e2…f1bb6` (identical) |
| sanitized dev (gold leakage) | `1ab3e0d8…4484e` |

## 2. Gate result (dev, primary)

| Gate | Target | Exact | Structural | Result |
|---|---:|---:|---:|---|
| Recall@10 macro | ≥0.60 | 0.3611 | 0.3611 | **FAIL** |
| Strict macro | ≥0.30 | 0.3194 | 0.3194 | PASS |
| Selected spans | ≥16/25 | 6/25 | 6/25 | **FAIL** |
| Per-case tokens | ≤3,716 | max 3,389 | max 3,674 | PASS |
| Deterministic | required | PASS | PASS | PASS |
| No gold leakage | required | PASS | PASS | PASS |
| Source/range provenance | required | PASS | PASS | PASS |

Candidate selectable ceiling: **16/25**. Oracle with the same 3,716-token
budget: **15/25**, .7361/.4722. Runtime policies realize only 6/25; the gap is
selection signal, not budget.

Held-out (policy frozen before run): exact 7/20, .5313/.4063, 1,053 tokens;
structural 9/20, .6250/.5000, 2,954 tokens. Held-out does not rescue the dev
failure and was not used for tuning.

## 3. What Selection can and cannot solve

Solved by exact/simple structure (6 spans):

- D04 `EmployeeUpdateInfo`
- D07 `RestoreEmployeeById` (one of three spans)
- D08 `DeleteEmployeeByIds` (service + controller)
- D09 `EmployeeBaseViews.sql`
- D11 `UpdateEmployeeAttr`

Locatable but not selected (11 spans):

- ranked out (6): D01 controller/service, D03 service, D06 service ×2,
  D10 `CoreService`;
- no runtime signal (5): D02 service ×2, D05 service, D06
  `UpdateOrgTotalEmployee`, D12 `UpdateOrgTotalEmployee`.

Not in pool / not locatable (8 spans):

- D02 `CoreDBContext` ×2, `Employee.cs`, `EmployeeDetail.cs`;
- D07 `OrganizationCommand`/SQL;
- D10 `CoreDBContext`;
- D12 SQL.

The 8 are `NOT_SELECTABLE` by this Selection unit, not successes hidden behind
the oracle.

## 4. Hypotheses

| ID | Result |
|---|---|
| S1-H1 exact-symbol | INCONCLUSIVE: precise but 6/25 dev |
| S1-H2 structural members/blocks | NO-GO: no dev recall gain, 3.5× tokens |
| S1-H3 token-budget packing | PASS cost; loses 1 oracle span; no quality gain |

## 5. Verification

- `verify_selection_s1_20261115.py` independently recomputes recall/strict,
  checks pool containment, source SHA-256, token counts, budget, determinism
  hash and D/H isolation.
- Negative controls rejected: fabricated range, duplicate, wrong path,
  tampered token/identity, outside-pool gold-derived unit.
- Sanitized dev run (all gold/label fields removed) produced identical selected
  units.
- R1 was not re-run in S1; the previous R1 correctness checks remain the latest
  evidence (`api::evidence` 10/10, `r1_public_api` 2/2 on merged HEAD).

## 6. Dependency map

### Selection

- Do not unfreeze on this prototype.
- The exact-symbol fallback is the only safe component; it should be a
  precision fallback, not the default.
- A production Selection unit needs a new **gold-free discriminator** for the
  11 locatable spans: query-term/identifier expansion, references/call graph,
  SQL binding, or an agent loop. None is in the current index/metadata.
- If pursued, pre-register a single bounded signal experiment; do not combine
  it with Expansion.

### Expansion

- The 8 missing spans are cross-file/SQL; the current index has no reference
  graph or SQL procedure binding.
- E1 is experimental/excluded; H6/H7 resource gates failed. Do not reopen E1R
  without fixing resource failures.
- Expansion stays frozen until Selection can exploit the existing 16/25 ceiling.

### R1

- Evidence Delivery is not enabled by S1.
- Release remains blocked by the historical R1 gaps (real-source pack
  provenance, latency/PSS, server/daemon parity, full dev + held-out assembly).
  No release/install is proposed.

## 7. Minimal next step

If the owner wants to continue Selection research, the smallest useful unit is
a **single signal experiment** on the frozen 11 locatable spans: can a
deterministic, gold-free query-to-declaration bridge (identifier aliases,
reference edges or SQL names) move at least 5 of them into a 3,716-token pack
without losing the 6 solved spans? If not, close Selection as blocked on
semantic/reference retrieval.

Do not open S2, Discovery-D1, Expansion or R1 delivery as part of this task.

## 8. Scope and cleanup

- No engine, CLI, index, schema, model, daemon, installed binary or production
  data was changed.
- Prototype code is limited to the two Python tools and the tokenizer example.
- External Cargo target (~1.5 GB) is removed after verification; receipts,
  probes, corpus/index and logs are retained.
- Experiment worktree is committed/pushed, then removed; only the main
  worktree remains.

## 9. Owner decisions

| ID | Decision |
|---|---|
| **D-S1-1** | Accept the `NO_GO` verdict and keep Selection frozen. |
| **D-S1-2** | Authorize (or refuse) one bounded signal experiment for the 11 locatable spans. |
| **D-S1-3** | Keep installed `0.0.1`/v2 and defer any release/install unit. |

Final commit: recorded in the branch tip and remote ref after the documentation
commit.
