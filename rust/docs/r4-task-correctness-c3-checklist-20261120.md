# R4 task-correctness C3 required-step checklist (2026-11-20)

Status: **FROZEN BEFORE any answer-quality evaluation.** This is a versioned
addendum to `r4-task-correctness-contract-20261118.md`; the original rubric and
gold are unchanged. No answerer or grader run has occurred.

## Freeze identity and protocol

- Base experiment checkpoint: `86463826717ccdf11454755d99fa2acd57ce3c1c`
  on `work/r4-evidence-representation-20261119`.
- Dev case file SHA-256:
  `27626109fe4d2bf02beec474809424bbd58dd5a2b709263c6dde0926a4a9c311`.
- Base correctness rubric SHA-256:
  `330c796d0e51ed5e02d8ca02f34a763dac06f6abd055eb4eee3ecc8488fe5e15`.
- RAG plan SHA-256:
  `28d8d21037038469b79b9e8a903988836d0976466e4d1a6963c2db87d2e072c0`.
- Scope: frozen development cases D01–D12 only. Synthetic D13–D16 are not
  answer-quality cases. No held-out case was opened or used.
- Authoring used only frozen case facts/source anchors to make C3 scorable;
  these checklists, gold, and expected answers must **not** be sent to the
  answerer. The grader receives them only after the per-case answer and pack
  have been frozen and hashed. Use a separate grader context. No self-grading.
- C3 score remains the base contract's 0–2: `0` required step absent, `1` step
  only implied/not cited, `2` every applicable listed step directly cited from
  the verified pack. A path beyond the explicitly listed scope is not required.

A citation for an assignment, call, guard, `SaveChanges`, or SQL effect must
cover the relevant source span. A negative/absence claim (e.g. “this method does
not assign Keyword”) requires the complete relevant method body in the pack;
a small excerpt cannot establish absence. Source citations establish static
code facts only, never runtime execution, database commit, deployment, or
production authority.

## Frozen per-case required steps

| Case | Required C3 steps; each applicable step must be cited |
|---|---|
| **D01** | 1. In `EmployeeEdit`, `Form == "1"` and `Perform` branch select `EmployeeEditThongTinCaNhan` for the non-create values. 2. The call resolves to the C2 `EmployeeEditThongTinCaNhan` declaration, not `EmployeeCreate` or the excluded `NewSolution` controller. 3. If describing what that method does, cite its `Context.Employees` lookup and missing-employee early return. No later persistence effect is required to answer dispatch. |
| **D02** | 1. C2 assigns employee fields and calls `Context.SaveChanges()`. 2. It then loads `EmployeeDetails`; missing detail returns before detail assignment. 3. It assigns detail fields and calls a second `Context.SaveChanges()`. 4. C4 ties `Employees` and `EmployeeDetails` DbSets to `CoreDBContext`. 5. State that physical table mapping, helper/trigger effects, commit success, and transaction scope are not established by this pack. Do not collapse the two saves into one atomic transaction. |
| **D03** | 1. In the personal-update method, cite the exact `Keyword` expression: `StaffCode + " " + FullName + " " + Email` through `ToKeyword()`, followed by appending `" " + FullName`. 2. State that `ToKeyword()` semantics are outside the selected scope; do not invent normalization behavior or downstream consumers. No separate SQL/persistence chain is required for this expression question. |
| **D04** | 1. Identify the C2 `EmployeeUpdateInfo` method. 2. Cite its `FullName`, `Email`, and `UpdatedDate` assignments and `SaveChanges()`. 3. If asserting no Keyword assignment, the pack must contain the complete method body. 4. Do not import the Keyword assignment from `EmployeeEditThongTinCaNhan` or claim no other system path writes Keyword. |
| **D05** | 1. Cite the employee `SaveChanges()` before loading the detail record. 2. Cite the null-detail return after that first save and before detail writes/second save. 3. Cite that the method contains no transaction/rollback. 4. Conclude only that this method's source order does not show all-or-nothing behavior; runtime transaction scope outside the method is unknown. |
| **D06** | 1. Cite that `changeType` is set only when `TrangThaiCongViec` changes. 2. Cite the guard requiring `changeType` and new status `0` or `3` before `EndWordProcess`/`UpdateOrgTotalEmployee()`. 3. Cite the helper's `execute sp_UpdateOrgTotalEmployee` and cache-key deletions. 4. Distinguish a FullName/Email-only edit from this status-change path. 5. Do not claim proc/cache success at runtime; helper returns/catches failure as recorded in the frozen facts. |
| **D07** | 1. Cite `RestoreEmployeeById`'s raw `UPDATE Employees` (`Status = 0`, audit fields), not a hard-delete operation. 2. Cite its call to `UpdateOrgTotalEmployee()`. 3. Cite the helper's named procedure call and cache invalidations. 4. Cite selected S1's recomputation of `Organizations.TotalEmployee` from `Employees` and `EmployeeOrganizations`, including applicable `PrivateCount` branch. 5. Mark deployment identity, triggers/jobs/external consumers, and exception-hidden runtime success as unknown; `_old`/`_old1` are not authoritative. |
| **D08** | 1. Cite `DeleteEmployeeByIds`'s raw `UPDATE Employees SET Status = -1` and audit fields. 2. Cite `LogDelete` if describing follow-up. 3. State the observed method contains no SQL `DELETE` and no `UpdateOrgTotalEmployee()` call; a negative claim requires the complete method body. 4. Keep trigger/downstream effects unknown. |
| **D09** | 1. Cite S2 as `CREATE VIEW EmployeeBaseViews` selecting `e.FullName` from `dbo.Employees` (and the selected projection/alias). 2. If included, cite the CoreDBContext DbSet declaration. 3. Classify the observed statement as a read/projection, not a write/copy. 4. Deployed view, privileges, trigger, or indexed-view behavior remain unknown. |
| **D10** | 1. Cite `CoreService` declarations of `Context: CoreDBContext`, `CbContext: CbDBContext`, and `RecContext: RecruitmentDBContext`. 2. Cite the personal-update path's use of `Context` for `Employees`/`EmployeeDetails` and `Context.SaveChanges()`. 3. Cite `CoreDBContext`'s connection-name argument `HRMContext`. 4. If mentioning the helper proc call, cite that it uses `Context.Database.ExecuteSqlCommand`. 5. Do not read secrets or claim the actual configured/deployed database identity. |
| **D11** | 1. Cite `UpdateEmployeeAttr`'s switch on `model.Attr`. 2. Cite the observed `Avatar` case assigning `e.Avatar`. 3. Cite `Context.SaveChanges()` for an existing employee. 4. Do not infer every model field is updated; unobserved attr values/partial types remain out of scope. |
| **D12** | 1. Cite C7's `execute sp_UpdateOrgTotalEmployee` call by name. 2. Cite selected S1's definition and its `Organizations.TotalEmployee` effect. 3. Identify S1 as the selected repository source variant only; `_old`/`_old1` are excluded/unselected. 4. State that database catalog/deployment and runtime-selected definition are not observed; do not answer “this is the one currently running” as fact. |

## Limitations

- This checklist improves the rubric's C3 reproducibility; it is not a correctness
  result and does not satisfy Gate C.
- C1/C2/C4/C5/C6 criteria and scoring thresholds remain those in the base
  contract. Disputed fact truth still belongs to an independent grader using the
  frozen gold and source anchors.
- The current inference gate is separately blocked: there is no confirmed
  privacy-approved external/local answerer environment in this session. Do not
  transfer source, packs, cases, or this checklist to a remote provider.

Freeze hash for this addendum must be recorded before any future answerer
execution; a later correction requires a new versioned amendment.
