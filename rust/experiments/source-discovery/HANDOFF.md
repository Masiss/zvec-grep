# Handoff — offline source discovery experiment

Status: **prototype verified on synthetic corpora; product adoption
INCONCLUSIVE**. Keep the tool experimental and opt-in. No default, index,
embedding, or public API changed.

## Source and checkpoints

- Repo: `/home/masis/src/zvec-grep`
- Branch: `work/source-discovery-autonomous-20261122`
- Starting checkpoint: `fb880c267a41595e4d67d9ab24bf57cb4ecd2e6b`
- Frozen development data: commit `6308350`
- Frozen independent validation: commit `e13c35f`
- Final implementation/results checkpoint: `cd95929`
- Main artifacts: this directory; detailed measurements are in `RESULTS.md`
  and `results-20261122.json`.

The originally named `rust/docs/r4-next-session-handoff-20261121b.md` is absent
from this checkout. Earlier R4 records remain on
`work/r4-evidence-representation-20261119`; they prohibit unrelated private
inference and index changes. This offline prototype did not need them.

## Decision and evidence

- `rg -n -F` missed all tested partial CamelCase/prefix queries in the frozen
  corpora. The prototype returned declarations and caller links for 4/4 valid
  development partial cases and the account/widget validation cases.
- Development: 15/15 declaration TP, 0 FP/FN; caller-name TP/FP/FN = 12/0/0
  across 11 valid tasks. One frozen case expects a missing `worker.py`; it is
  excluded.
- Independent validation: declaration TP/FP/FN = 7/1/1; caller-name TP/FP/FN =
  7/0/0 across six tasks. `invoiceNo` fails to map `No` to `number` and instead
  includes `send_invoice_notification`.
- Median process latency was 3.00 ms prototype vs 6.02 ms `rg` on valid dev
  tasks and 5.79 ms vs 8.15 ms on validation. Median RSS was 4,160 vs 6,472 KiB
  and 5,614 vs 6,472 KiB, respectively. These are tiny fixture measurements.
- The initial `baseline-rg.json` files contain `rg` exit 2 from an incorrect
  doubled relative path. They are preserved as provenance. The evaluator runs
  `rg` on the correct path and its actual outputs are recorded in
  `results-20261122.json`.
- One fresh-context reviewer identified caller name-collision, incomplete type
  resolution, unbounded directory scanning, and thin validation. Review was
  single-pass; no hardening loop followed.
- The reviewer also confirmed the corrected result metrics and flagged that
  caller scoring flattens names rather than verifying caller-to-callee edges;
  7/7 caller-name TP is not a 7/7 relationship-accuracy claim.

## Implementation

`src/main.rs` is an offline Rust CLI using Tree-sitter Rust, Python, and
TypeScript grammars. It extracts function/type declarations and syntactic calls,
matches contiguous prefixes over camelCase/underscore tokens, and serializes
declarations with caller links. `evaluate.py` verifies frozen corpus hashes and
compares actual candidate and ripgrep outputs, bytes, latency, and peak RSS.

Build and run:

```bash
CARGO_TARGET_DIR=/tmp/zvec-source-discovery-target \
  cargo build --offline --manifest-path rust/experiments/source-discovery/Cargo.toml
python3 rust/experiments/source-discovery/evaluate.py \
  /tmp/zvec-source-discovery-target/debug/zg-source-discovery-prototype \
  > rust/experiments/source-discovery/results-20261122.json
```

The `Cargo.lock` is local to the isolated prototype crate. No source/index was
sent externally. One initial semantic workspace search failed while opening
the persisted FTS index; it was not used by the prototype or evaluation.

## Unresolved issues and next work

Do not integrate into production defaults. If the owner chooses to continue,
the smallest useful next experiment is to scope traversal to source directories
and exclude vendor/generated/build paths, then create a separately frozen,
larger corpus with duplicate symbol names and abbreviation pairs. Preserve the
current validation set unchanged. Caller links should be resolved by scoped
symbol identity before any broader adoption claim.

No index repair/rebuild, model download/change, live database access, private
source upload, push, merge, or release was performed. Revert the implementation
and results checkpoint with `git revert cd95929`; remove the frozen benchmark
commits as well by reverting `e13c35f` and then `6308350`.

## C# continuation — final code checkpoint `9cf8eea`

The C# continuation extends the same standalone offline prototype. Results,
review findings, and limitations are in `csharp/RESULTS.md`; frozen C# fixtures
and baselines are under `csharp/fixtures/`; review reproductions are under
`csharp/repros/`.

- Frozen cases checkpoint: `9ee18f8` (do not edit).
- Initial C# implementation: `7723fdc`.
- Caller uncertainty fix: `55f8aa2`.
- Final reviewer fixes, repro outputs, results, and this handoff: `9cf8eea`.
- Decision: **INCONCLUSIVE** for product adoption/generalization.

The development suite measured 20/20 exact caller-to-callee symbol pairs. The
independent suite measured 10/10, with zero false edges. The validation
uncertainty score has a frozen source/gold mismatch (`dynamic` in source,
`object` in expected metadata); it was not edited. Reviewer findings about
identity collisions and type inference were reproduced and fixed. The reviewer
recommends INCONCLUSIVE for adoption. These synthetic tests do not establish an
HRM2.0 gain or full C# compiler-equivalent resolution.

Remaining limits: inheritance, extension methods, aliases/usings, generic
method invocation binding, explicit-interface dispatch, optional parameters,
implicit numeric conversions, local-function bodies, and very large flat
directories can remain unresolved or costly. The implementation is 1,162 lines
plus an evaluator and is materially larger than invoking `rg`. Continue only
with a public multi-project C# corpus and a Roslyn comparison if broader
semantic binding is required; preserve the frozen suites unchanged.

The cached `tree-sitter-c-sharp 0.23.5` parser built offline. Synthetic C#
sources compiled with the local .NET SDK using a cleared package-source list.
No package/model download, private source upload, index repair, live database
access, push, merge, or release occurred. An initial local indexed search failed
while opening the corrupt FTS index; it was not used by this prototype.

Rollback from the final code checkpoint: `git revert 9cf8eea`, then revert
`55f8aa2`, `7723fdc`, and `9ee18f8` in that order. A later documentation-only
handoff commit may be reverted first if present.

## Real-world C# validation — canonical checkpoint `53372b1`

**Decision: NO_GO for experimental integration of the current Tree-sitter C# resolver on multi-project repositories.** Keep the prototype isolated; use `rg` for lexical discovery and project-aware Roslyn tooling for semantic caller relationships. Broader accuracy generalization beyond this Avalonia snapshot remains unproven, but current edge/declaration output and per-query cost do not justify integration.

- Public repository/revision: Avalonia `debc815ba9dff30a283135b1764e5fdfb609bb91`.
- Frozen source slice: four complete projects, 1,959 C# files, 11.19 MB; all copied-source hashes match the manifest. The freeze includes the task list, gold outcomes, Roslyn oracle, and `rg` baselines, recorded with SHA-256 in `csharp/real-csharp-20261122/freeze.json` (the sandbox makes `.git` read-only, so the freeze checkpoint is hash-based rather than a commit).
- Three partial-type identity tasks found the intended type identities, but returned only 3/11 expected partial source locations. The supplemental partial-method suite found only 3/24 method identities, emitted 0/106 edges, and represented only 22/106 target call sites as unresolved (7/14, 13/14, 2/78). It used about 95 MiB RSS per query versus 7.1–7.6 MiB for `rg`; three other unresolved records were outbound calls from matched methods. Four exact-edge tasks had 168 Roslyn-bound call sites. The prototype emitted 0 edges (TP 0, FP 0, FN 168); all 168 appeared as unresolved. No false emitted edges were observed; edge precision is undefined for an empty prediction set. It also produced 88 other unresolved records from matched caller contexts. Five valid `SnapshotUndoRedo` calls that omit an optional Boolean argument were mislabeled `no_matching_arity`.
- The slice skipped 44 parse-error files. The prototype clears all definite edges if any file is skipped, so scan-wide incompleteness suppresses edge output across the corpus. Query time was 5.7–19.1 s versus 13–15 ms median for case-insensitive `rg`. Full `src` has 2,965 C# files; a measured scan hit the 2,500-file cap, took 19.63 s, used 143,176 KiB peak RSS, and returned `truncated=true`.
- Roslyn bound 30,888 / 33,711 invocation sites in the reduced source-compilation context. It emitted 1,868 diagnostics because official project properties, package references, analyzers, generators, and target-specific compilation setup were not loaded. All 168 frozen gold sites were concretely bound. Treat this as a bounded call-site oracle, not a full Avalonia build or general language-coverage claim.
- Fresh-context reviewer independently verified hashes, the 168/168 unresolved-site coverage, 3/11 partial-path coverage, the global edge-clearing guard, and optional-argument misses. Findings: `csharp/real-csharp-20261122/REVIEWER-FINDINGS.md`.

A supplemental suite was separately frozen before candidate execution to test partial method identifiers `CreateBitmap`, `SnapshotUndo`, and `UpdatePseudo`. It found only 3/24 Roslyn method identities (precision 3/3, recall 3/24), emitted 0/106 definite caller edges, and reported 22/106 expected sites unresolved. Each query cost 11.6–14.9 s versus 10.1–11.6 ms for `rg`. Full scoring and freeze hashes are in `csharp/real-csharp-20261122/partial-method-scoring.json` and `partial-methods-freeze.json`.

Full per-task output, resource records, comparisons, limits, and rollback checkpoints are in `csharp/real-csharp-20261122/REALWORLD-RESULTS.md`; the exact existing prototype diff is saved as `csharp/real-csharp-20261122/prototype-source-diff.patch`. Frozen task/gold files, raw candidate JSON, Roslyn oracle output, and `rg` output are alongside it. The prototype source was not changed during this evaluation. Existing C# implementation delta since `9ee18f8` is 1,180 lines in `src/csharp.rs` plus 12 dependency lines (`Cargo.toml` and `Cargo.lock`).

**Stop here:** do not continue C# hardening based on this corpus. Next decision, only if the owner reopens the work, is whether a separate project-aware Roslyn integration has enough concrete product demand to prototype. Current HEAD remains `53372b18ba035ac83fa76d0fdad395bf2c0cb2fd`; evaluation artifacts are uncommitted because `.git` is read-only in this sandbox. No source/index/default/API/RAG benchmark changes, private-source use, remote inference, push, merge, or release occurred. Roll back the isolated C# code/frozen synthetic fixtures with the previously listed reverts; no code changes from this real-world evaluation need reverting.


## HRM2.0 budgeted source-discovery decision — 2026-11-22

Decision: **NO_GO for the tested rg-first budgeted evidence-pack workflow.** The best compact/ranked arm delivered 1/25 annotated spans (D11); its paired first-hit baseline delivered 0/25. No case that had zero annotated-span hits in both unbounded identifier and lexical `rg` baselines became reachable. Do not continue tuning this workflow against D01–D12.

- Provenance remains resolved from R4 ref `work/r4-evidence-representation-20261119`; manifest digest: `27626109fe4d2bf02beec474809424bbd58dd5a2b709263c6dde0926a4a9c311`. D01–D12 supply 25 source spans, but not structured declaration identities or caller→callee edges. H01–H08 were not opened.
- The original 36 KB full-line `rg` stream acquired 0/25 spans. Compact projected path/line/match-token output acquired 1/25 (D11); ranked packing delivered that span but raised pack noise to 1,417 non-gold lines and reached 23,477 bytes. Adding `--max-count 1` reduced noise and pack bytes but returned 0/25. Missed evidence in the 24 other spans is acquisition failure under these query/output caps.
- All arms stayed within each task's budget: 2 measured operations, 10 source files opened, ≤23,477 pack bytes, and ≤59,451 cumulative query-plus-pack bytes. Batch peak RSS was 22.9–23.2 MiB. The unbounded lexical baseline reached 11/25 but emitted 8.91 MB. Detailed per-case counts, budgets, latency, memory, public negative-control results, and limits are in `research/hrm2-source-discovery-reality-check/PROBE-RESULTS.md` and `PROTOCOL.md`.
- Roslyn remains partial capability evidence (45/58 solution entries loaded/compiled, with high aggregate errors); relation correctness and answer quality were not scoreable from the available gold. This NO_GO is scoped to the tested rg-first workflow, not every local-first discovery design.
- A fresh-context reviewer checked the sanitized bundle and confirmed the budget arithmetic, acquisition-versus-selection analysis, scoped NO_GO, and final leakage/rollback wording. Raw HRM questions, identifiers, paths, source text, and receipts remain local-only.
- Source diff: no source, index, embedding, default, API, or frozen benchmark changed. The only implementation was a one-off script in local temporary storage; no product-code rollback is needed. Optional documentation cleanup is to remove the goal's sanitized research directory and this appended section. Temporary scripts/receipts can be deleted separately. Existing unrelated dirty artifacts were preserved.

Checkpoint: this handoff update and sanitized evidence bundle are committed together at the current HEAD on `work/source-discovery-autonomous-20261122`. The base HEAD before recording this evaluation was `53372b18ba035ac83fa76d0fdad395bf2c0cb2fd`. HRM2.0 working tree, index, and exact source revision were not changed. No push, merge, release, or remote private inference occurred.

## Quyết định reset kiến trúc — 22-11-2026

Đã xác minh source/runtime trên nhánh `work/source-discovery-autonomous-20261122`,
source HEAD `663566546f03768b9b021c65eb3accc4aa0720a1`. Worktree có sẵn các
untracked `docs/research/session-retrospective-20261122.md`, `research/m4-native/`
và `rust/experiments/source-discovery/real-csharp-20261122/`; tất cả được giữ
nguyên. Mission thêm probe lexical public đã đóng băng ở
`research/architecture-reset-20261122/` và memo đã sanitize tại
`docs/research/architecture-reset-decision-20261122.md`.

Quyết định: **SIMPLIFY**. Dừng đầu tư vào resolver C# Tree-Sitter và capped
`rg` pack đã thử; dùng lexical search cho exact/partial identifier; lợi ích sản
phẩm của semantic retrieval vẫn **INCONCLUSIVE** cho đến khi đo trên index khỏe
mạnh với lexical baseline tốt. Prototype Tree-Sitter offline riêng cho
Rust/Python/TypeScript vẫn experimental, opt-in; quyết định này không đánh giá
prototype đó.

Probe source công khai mới: native `rg` và `zg --rg` đều tìm 5/5 declaration
targets dương tính đã đóng băng; negative task có 0 source matches. Native
`rg` tốn ít thời gian và memory hơn trong mọi task đo được. Với query mơ hồ
`format`, native chạy 5,3 ms / 7.300 KiB so với 88,9 ms / 39.032 KiB của
`zg --rg`; output managed nhỏ hơn 5,2 KB nhưng nhiều hơn 89 dòng. Đây là so
sánh khả năng/chi phí trên 6 task, một repo; không phải agent A/B hay chấm
false-positive. Chưa xác minh binary managed được build từ HEAD này.

Independent fresh-context challenge yêu cầu sửa kết luận nhân quả cũ về FTS:
“No matches” trước đó không thể tách khỏi index corruption được xác nhận sau.
Báo cáo sanitized đã được đính chính. Reviewer fresh-context thứ hai xác minh
hash, số liệu, phạm vi; yêu cầu làm rõ kết luận chỉ nhắm resolver C# và phân
biệt zero source match với output/exit behavior. Các điểm này đã được sửa.

Kiểm tra index chỉ đọc trả `CORRUPT` cho generation
`6e3c3a4d-4690-4868-8f9e-1023bbb7577c`; không rebuild, refresh hay repair.
CLI cài sẵn là `zg 0.0.1`, SHA-256
`6db0f67e05e61ad6d50e8e9ea7b3e4593cfb07c99ac0af9f5064f48398db7ed4`; native
`rg` là 15.2.0, SHA-256
`e62198eb19b136b88c330af83647b5a962cb99b6b1f066758568f12de1974849`. Không
đổi source/API/default/index/embedding/benchmark hoặc private data. Không
inference, install, push, merge hay release.

Quyết định tiếp theo: default MCP agent hosts có cần expose tool no-index
`zvec_grep_rg` hiện có hay có thể dùng native shell search. Không đổi public
toolset/API trong mission này. Nếu sau này đo semantic value, trước hết phải
có đường đánh giá local đã được cho phép và index khỏe; không dùng index hỏng
hiện tại làm baseline. Bằng chứng và giới hạn đầy đủ nằm trong
`docs/research/architecture-reset-decision-20261122.md` và
`research/architecture-reset-20261122/partial-id-results.json`.

Checkpoint nguồn được freeze tại `663566546f03768b9b021c65eb3accc4aa0720a1`;
report/handoff chỉ đổi tài liệu và evidence, không đổi runtime.

Trạng thái Git khi bàn giao: HEAD vẫn là
`663566546f03768b9b021c65eb3accc4aa0720a1`; không tạo commit mới. Thay đổi của
mission này còn local: sửa report `docs/research/source-discovery-autonomous-20261122.md`
và handoff này; thêm `docs/research/architecture-reset-decision-20261122.md`
cùng hai artifact freeze/results trong `research/architecture-reset-20261122/`.
Các untracked có từ trước `docs/research/session-retrospective-20261122.md`,
`research/m4-native/` và `rust/experiments/source-discovery/real-csharp-20261122/`
được giữ nguyên, không đưa vào thay đổi của mission.

## Continuation — indexed semantic retrieval qualification (2026-11-22)

Goal về indexed semantic retrieval được tiếp tục trên R4 index v3 hoạt động trong disposable copy, nhưng kết luận là **INCONCLUSIVE** vì verifier read-only hiện có chỉ hỗ trợ v2 và từ chối v3 với `INCOMPLETE`. Ba task local-only cho thấy vector/hybrid chạm target files nhưng 0/9 searchable gold-span overlap; capped native `rg` và post-hoc IDF lexical ranking mỗi route đạt 1/9. OR first-hit `rg` pool (`--max-count 1`/file) đạt 3/9 với median output 1.43 MB; đây không phải full-recall ceiling vì IDF gộp context từ các lần tìm term riêng. Đây là retrieval overlap, không phải answer quality. Một D07 gold span `NOT_PERSISTED` được loại khỏi mẫu số; source/index file count 822/818 và cached binary build provenance cần được giải thích.

Đọc [handoff ngắn](../../../docs/research/indexed-semantic-qualification-handoff-20261122.md) rồi [báo cáo đầy đủ](../../../docs/research/indexed-semantic-qualification-20261122.md). Bước tiếp theo là xác minh v3 bằng verifier tương thích mà không rebuild index, làm rõ file-count delta và binary provenance; sau đó mới cân nhắc rerun freeze. Không mở H01–H08 hoặc phát sinh remote inference. Không đổi production/index/schema/default/API/benchmark; không upstream sync/push/merge/release.
