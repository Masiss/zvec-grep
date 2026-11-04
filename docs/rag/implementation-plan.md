# RAG implementation plan — phases R0–R6

[Thiết kế và mục tiêu](README.md) · [HRM2.0 pilot và source anchors](hrm2-pilot.md)

**Status: PROPOSED, 2026-10-28.** Chưa phase nào dưới đây được thực thi bởi việc viết plan. R0–R6 là nhánh RAG mới, không phải đổi tên P0–P7 hoặc M4 Gate A/B/Phase 3. Không thay safety/resource/authorization contract của operation khác.

## 1. Thứ tự và definition of done

```text
R0: frozen pilot + questions + budgets
 → R1: source/evidence contract, chưa cần graph
 → R2: scoped C# facts
 → R3: SQL/EF/cross-file relations có giới hạn
 → R4: agent loop + API integration
 → R5: quality/resource qualification cho pilot
 → R6: opt-in release + rollback
```

Có thể làm fixtures của R4 song song với R2/R3, nhưng không gọi đó là integration PASS trước khi source/relations thật qua gate. Không cần chờ M4 GO. Không edit các đoạn native-lock/containment đang được agent khác làm chỉ để hoàn thành RAG.

Mỗi phase có hướng khuyến nghị và phương án thay thế; **không phải implement hết mọi phương án để pass**. Gate có disposition `PASS`, `REWORK`, `PARTIAL/INCONCLUSIVE`, `OPERATION_BLOCKED` hoặc `DEFERRED`. Candidate bị loại và baseline được giữ là kết quả hợp lệ, nhưng không được gọi capability chưa có là MVP complete.

## 2. R0 — Freeze scope, source authority và acceptance

**Mục tiêu:** biết cái gì đang được phân tích, có thể chứng minh điều gì và giới hạn tài nguyên nào được dùng.

### Hướng xử lý

- **A — Scope nhỏ nhưng thật (khuyến nghị):** root HRM2.0; bắt đầu HRM web Employee controller → Service.Core → DBContext.Core → các SQL definitions liên quan. File/project khác là expansion có admission, không tự gom toàn monorepo.
- **B — Project-aware inventory:** parse `.csproj` Compile/ProjectReference và các conditions dưới dạng dữ liệu. Phân biệt included, excluded, conditional/unknown; same FQN ở assembly khác là entity khác. SQL definitions có source variant identity riêng.
- **C — Gold và baseline trước optimization:** chốt 16 development cases từ pilot doc và 8 held-out cases do reviewer chọn độc lập trước tuning. Record source hashes, expected direct supports, conditions, candidates/gaps và forbidden claims. Dùng existing search/grep + source reading làm control; không lấy một LLM tự chấm chính nó làm gold.

**Deliverables đề xuất khi implement:** `benchmarks/rag-impact/` chứa schema, scope manifest, cases và report contract; fixture code synthetic/minimized, không commit toàn bộ repo private vào zvec-grep. Ghi corpus root ngoài repo vào local config, không đưa credentials/absolute developer paths vào portable fixtures.

**Gate R0:** source manifest/selected project variants và exclusions rõ; 24 cases được review/split/freeze; numeric targets ở §9 được chấp nhận hoặc điều chỉnh **trước candidate measurement**; xác nhận pilot repo read-only, no DB/no build; có baseline route/protocol và cost budget. Không tự chọn runtime/deployed SQL authority từ tên file `_old`.

**Fallback:** thiếu mapping/SQL body thì scope/gold ghi gap; không truy DB để lấp chỗ trống. Nếu corpus không đủ authority cho một case, thay bằng một câu hỏi source-observable trước khi freeze, không sửa gold sau khi candidate fail.

## 3. R1 — Evidence/source pack trên retrieval hiện có

**Mục tiêu:** agent nhận source đáng dẫn chứng và có thể tự kiểm trước khi đòi graph tự động.

### Hướng xử lý

- **A — Typed evidence assembler (khuyến nghị):** reuse `ContextItem`, ranges, file selection và hydration; bổ sung source identity/expected hash, support refs, explicit gaps/truncation. Cache chỉ theo file hash + extraction/rule version + project scope.
- **B — Batch source validation:** group requests theo file, hash/đọc mỗi file admitted một lần cho batch, dedup excerpts/overlap. Bằng chứng đổi/missing/out-of-root → fail closed cho chính evidence đó, không thay bằng source mới cùng line number.
- **C — Bắt đầu internal API:** dùng engine/test adapter để kiểm contract; nếu chưa đủ source provenance, giữ existing search và agent đọc source trực tiếp. Chưa đổi MCP search defaults hay public response shape một cách âm thầm.

**Gate R1:** deterministic tests cho Unicode/CRLF/range/EOF, symlink/traversal, stale line/renamed/deleted file, cùng tên khác project, snippet budget và query error vs empty result. Mọi cited span khớp đúng source hash/content coordinates. Existing search/rg regression cases giữ nguyên; source refs từ cache cũ không được validate như current. Chưa cần performance win để pass correctness slice này.

**Deliverable:** một evidence pack + validator nhỏ, không có generative model hoặc persistent graph.

## 4. R2 — C# facts đúng scope, không chạy compiler

**Mục tiêu:** lấy được call/assignment/context facts đáng tin từ HRM2.0 với chi phí thấp.

### Các phương án

- **A — Tree-sitter C# side parser (khuyến nghị):** bổ sung grammar tương thích runtime hiện có; parse file cần thiết để lấy namespace/class/partial/method/signature/receiver/declarations/assignments/invocations và branch guards. Không đổi ngay chunking/index format của ordinary `.cs` search.
- **B — Lexical fallback có nhãn:** khi syntax error, unsupported construction hoặc file vượt cap, trả source matches + candidate/gap. Regex không được sinh resolved `calls` hay `writes_table` như semantic fact.
- **C — Roslyn/semantic sidecar (defer):** chỉ xem xét nếu type/overload resolution là blocker có evidence và owner chấp nhận dependency/footprint. Không require .NET/MSBuild/restore cho MVP. Không suy “Tree-sitter parse được” là “resolve type đúng”.

### Work items bắt buộc của hướng A

1. Scope `.csproj`, signature/namespace/project identity; hợp nhất partial trong cùng project.
2. Resolve receiver khi khai báo/type/assignment đủ đơn giản để chứng minh: `_coreService`, `Context`, `u` từ `Context.Employees`, `d` từ `Context.EmployeeDetails`.
3. Extract `assigns_from` theo biểu thức: FullName tham gia Keyword; phân biệt `EmployeeUpdateInfo` với `EmployeeEditThongTinCaNhan` dù cùng sửa FullName.
4. Carry guards: Form/Perform branches, null early return, `changeType`, nested status branch; không flatten thành mọi effect luôn chạy.
5. Record `SaveChanges` theo context và vị trí; không gom hai lần save thành transaction atomic hoặc coi lời gọi là bằng chứng commit runtime thành công.
6. Extension method/dynamic dispatch/multiple overloads chưa resolve → candidate với source callsite, không chọn target theo nearest filename.

**Gate R2:** minimal fixtures và development cases chứng minh đúng project/partial/receiver/branch/commit boundaries; zero false resolved edges trên critical negative cases. Source comments/string content không trở thành executed calls. Parse failure không làm mất lexical fallback. Chạy thêm existing-language extraction tests; C# side parsing không buộc reindex hoặc thay format hiện có.

**Fallback:** ship evidence pack R1 với C# candidate-only, báo chưa đạt automated relation MVP; không mở local LLM để bù parser.

## 5. R3 — EF6, SQL và cross-file expansion

**Mục tiêu:** nối được handler/service/entity → persistence và downstream read/cache/proc effects mà không dựng whole graph trong RAM.

### Hướng xử lý bổ sung nhau

- **A — EF6 adapter:** extract DbSet declarations, explicit annotations/fluent mapping nếu có, context receiver, mutations và `SaveChanges`. Xử lý `AddFromAssembly` là mapping-resolution requirement. Chỉ dùng convention mapping khi rule/preconditions được kiểm chứng; nếu thiếu config scope thì entity write supported nhưng physical table vẫn candidate.
- **B — SQL adapter:** đọc statement/alias/column từ raw SQL string template và source `.sql`; phân biệt UPDATE/INSERT/DELETE với SELECT/JOIN. Ghép constant fragments an toàn, để parameter/value symbolic; dynamic table/procedure target hoặc unsupported T-SQL control flow là gap. Proc call và proc body là hai support riêng. T-SQL subset phải được kiểm bằng fixture; không giả định một SQL parser dependency hỗ trợ toàn stored-procedure grammar.
- **C — Targeted relation lookup:** lấy seed từ search, lookup forward/reverse bằng bounded exact references và cached per-file facts, merge/dedup tại query boundary. Preserve SQL source variants và guard trên cả chain. Alias từ vựng Việt–identifier chỉ hỗ trợ discovery, không chứng minh dependency.

### Quyết định lưu trữ — không làm cả hai mặc định

| Phương án | Chọn khi | Chi phí/giới hạn |
|---|---|---|
| **On-demand + bounded cache** — MVP mặc định | Targeted reads đạt coverage và latency với file-read budget | Không có whole-repo reverse completeness; mọi search/budget boundary phải thành gap. Không scan tất cả file mỗi round. |
| **Embedded SQLite sidecar** — chỉ nếu cần | On-demand phải quét lặp, vượt budget hoặc miss critical reverse edge dù lookup rules đúng | Thêm dependency/schema/invalidation/disk; cần endpoint/kind/file indices, bounded page cache, atomic transaction cho facts của file, metadata generation và rebuild-owned-sidecar story. Không daemon/server riêng. |
| Whole graph JSON/custom store | Không chọn cho MVP | Dễ nhân bản RAM hoặc phát sinh index/recovery framework tự chế. |

Nếu chọn SQLite, sidecar là derived metadata, không nhét relation vào native zvec collection để làm tăng optimize debt. Store file hash/project/rule dependency; failed extraction không publish partial file facts. Query không join facts khác source version; stale sidecar fallback R1/lexical với gap. Startup không bắt rebuild toàn bộ trước mọi search. Sidecar rebuild chỉ xóa operation-/feature-owned metadata sau explicit action, không đụng vector index/live source. Đường on-demand vẫn hoạt động khi sidecar unavailable.

**Gate R3:** tìm đúng restore → helper → stored-procedure definition → Organizations update/cache invalidation chain trên source variant đã chọn; tìm personal-update branches/two save boundaries; phân biệt EmployeeBaseViews read với table write; false sibling-version/project/old-script links bằng 0 trên critical fixtures. Search miss/truncation/unknown mapping đều được báo. Chọn một storage route có evidence về coverage/cost; không mở vô hạn cache để đạt latency.

**Fallback:** missing dynamic/SQL/mapping branch vẫn trả candidate/gap và source để agent điều tra. Không đánh dấu cả feature “complete” chỉ vì một path đã resolve.

## 6. R4 — Multi-turn agent loop và public integration

**Mục tiêu:** trước final answer, agent tự kiểm được chất lượng evidence với ít lượt nhất.

### Hướng xử lý

- **A — Adapter/prompt protocol trên agent sẵn có (khuyến nghị):** DEFINE/DISCOVER/EXPAND/CHECK/FINAL như [README](README.md); thông thường 2 rounds, thêm tối đa 1 corrective round khi có missing evidence cụ thể. Không tạo một reasoning service bên trong zvec-grep.
- **B — Structured engine/MCP capabilities:** typed `relations` và `evidence`; request caps/cancellation phía core; session ledger phía agent adapter. Batch support fetch thay vì một tool call cho mỗi node. Update daemon wire DTOs và CLI/MCP contract tests ở owning crates.
- **C — Host không có adapter:** cung cấp manual tool workflow và budgets trong instructions; ghi rõ enforcement chỉ có per-request caps, không claim session-loop bounded. Không thêm local loop daemon để bù host.

Self-check phải gồm: đúng scope/action/field, direct-vs-candidate, condition và commit boundary, source freshness, uncovered dynamic dependencies, evidence cho từng claim. Core validator chỉ xác minh cấu trúc/support/source, không thay semantic reasoning của agent. Đừng dùng generic “hãy suy nghĩ lại” lặp nhiều lần.

**Gate R4:** end-to-end bằng deterministic scripted agent/mock LLM cho thiếu mapping, sai branch, stale evidence, budget exhausted, duplicate search, prompt-injection source và cancellation. Không emit answer trung gian như kết luận; hard-stop session tại cap và trả PARTIAL có lý do. Một bài đi qua đủ chain phải kết thúc với một final answer dẫn chứng; thiếu body/ambiguous scope phải dừng trung thực. Hosted LLM evaluation riêng chỉ được chạy khi egress/cost được phép; unit tests không gửi code ra ngoài.

**Deliverable:** agent protocol/integration example và capability docs; không phụ thuộc một vendor/model. Public additions phải có version/unsupported behavior rõ; existing TS/CLI/MCP behavior không đổi.

## 7. R5 — Đánh giá chất lượng và footprint trên pilot

**Mục tiêu:** chứng minh điều tra tốt hơn, không chỉ có thêm tool hay thêm graph.

### Hướng xử lý

- **A — Deterministic quality first:** source-span validity, relation resolution, conditions, anti-false-link, unknown/gap handling, repeatability và cache invalidation. Seed facts đúng mới đo answer/loop.
- **B — Paired retrieval/agent comparison:** control = existing search/rg + same agent workflow budget; candidate = thêm evidence/relations/check loop. Cùng corpus revision, local embedding, agent model/settings nếu có, query set, budgets và fresh/warm protocol. So nguồn tìm được, unsupported claims, số lượt/source bytes và time; không đổi nhiều yếu tố để gọi là cùng comparison. Freeze riêng source/diff, executable/native-library identities và config của control/candidate trước measurement; kiểm drift tại các mốc trước/sau, không rebuild hoặc tự chọn lại artifact mới nhất giữa operation. Đây không phải kernel-immutable snapshot.
- **C — Resource measurement riêng:** cold parsing, warm lookup, edit/reopen/invalidation và repeated investigation. Host/process state trước/sau; CPU time, RSS/PSS/swap, latency/source bytes/cache size. Khác biệt cache/host order phải ghi, không ép host rảnh bằng cách dừng việc khác của owner.

**Gate R5:** các hard correctness gates và numeric targets ở §9 đạt trên frozen development + held-out cases, báo riêng mỗi split. Không sửa gold hay nâng budget sau khi nhìn candidate rồi vẫn gọi là cùng experiment. Nếu measurements thiếu/không hợp lệ → INCONCLUSIVE, không suy performance win. Nếu relation route không đem lại giá trị hoặc footprint vượt budget → giữ R1 lightweight mode, defer expanded capability.

**Evidence limits:** 24 pilot cases không chứng minh toàn HRM2.0 hoặc mọi ngôn ngữ. Answer quality do judge LLM chấm chỉ là diagnostic phụ; người review đối chiếu source quyết định disputed cases. Một run/pair chỉ là exploratory; không cần lặp hàng loạt trừ khi uncertainty/order effect thực sự chặn decision.

## 8. R6 — Opt-in release và rollback

### Hướng xử lý

- **A — Rust opt-in capability (khuyến nghị):** pilot package/feature riêng, giữ native storage/backend hiện tại, không đổi installed runtime hoặc enable cho mọi workspace.
- **B — Promote theo scope:** sau R5, chỉ bật cho ngôn ngữ/rules có evidence; unsupported languages giữ existing search/explicit gaps. Owner quyết định có mirror TS API hay đợi Rust cutover; không nhân đôi engine mặc định.
- **C — Rollback:** disable evidence/relations capability và quay về search/rg; discard/rebuild chỉ derived sidecar khi được yêu cầu. Không require vector reindex chỉ để rollback RAG metadata.

**Gate R6:** actual process-boundary/packaging tests; capability discovery và unsupported-version behavior; upgrade/sidecar schema/reopen/cancel/rollback; docs khớp runtime được phát hành. Owner phê duyệt public surface/default/format/migration nếu bị tác động. Không tự deploy Ricons hoặc sửa DB/schema ứng dụng để làm tool đạt gate.

## 9. Budget và acceptance đề xuất để freeze ở R0

Các số dưới đây là **design defaults/targets đề xuất**, không phải số đo đã đạt và không thay resource policy của M4. Điều chỉnh một lần dựa actual pilot baseline trước experiment; sau đó không nới để tạo PASS.

| Dimension | Default/target cho pilot | Khi không đạt |
|---|---|---|
| Local LLM/model | 0 generative/critic/reranker model mới; reuse embedding hiện tại | Defer model-heavy design; không chuyển reflection vào local CPU. |
| Loop | ≤3 rounds, ≤6 tool calls, 1 targeted corrective round | Final PARTIAL + unresolved gaps, không reset counter. |
| Graph expansion | ≤2 hops/request, total path depth ≤6; 128 nodes/256 edges/session | Truncation + frontier refs, không claim complete. |
| Source I/O | ≤32 distinct source files/session; batch requests, existing file admission caps | Narrow query/scope hoặc PARTIAL; không silent full-tree scan. |
| Content | Final source pack ≤24 KiB UTF-8, cumulative tool content ≤64 KiB | Dedupe/select supports; explicit omitted evidence. Token counts chỉ estimate nếu không dùng đúng tokenizer của external agent. |
| CPU/concurrency | 1 parser worker; không background LLM/full-graph recomputation; cancellation at file/batch boundaries | Giảm scope/work, không tăng workers để giấu cost. |
| Memory | Cache fact/AST ≤32 MiB; evidence-component incremental peak PSS ≤64 MiB so cùng search baseline | Evict bounded cache, stream facts; không load whole graph. Cần đo process peak, không chỉ counters tự khai. |
| Optional sidecar | ≤256 MiB/scope trên pilot, SQLite page cache tính trong memory budget | Dừng publication phần vượt cap + gap; không xóa evidence cũ hoặc tăng cap ngầm. |
| Warm operation | p95 ≤1 s trên bounded local tool operation; local-tool wall tổng ≤3 s/investigation | Phân rã parse/lookup/hash/hydrate; không bao gồm external LLM latency vào claim local. |
| Existing search | Evidence mode off không regress contract; target p95 không xấu >10% cùng workload | Bỏ work/overhead khỏi hot path; không điều chỉnh search defaults. |
| Evidence integrity | 100% cited spans valid; 0 false critical project/version/write/condition links | REWORK bất kể aggregate quality. |
| Supported-edge coverage | ≥85% của annotated supported gold edges, báo riêng mỗi split; critical chains trong selected scope đầy đủ | Narrow capability hoặc fix resolution; không nâng candidate thành direct. |
| Unknown handling | Tất cả critical unresolved/adversarial cases được giữ candidate/gap, không khẳng định no impact | REWORK. |
| Product value | Giữ/tăng supported evidence coverage so control và giảm ≥25% distinct source files đọc **hoặc** source bytes/tool payload trên cùng cases/budget | Không promote expanded relation mode chỉ vì nó tồn tại. Claim savings phải tính cả search/expand/check overhead. |

SLO latency không phải timeout của mọi request. Runtime timeout/cancellation và local disk/memory reserves phải được protocol cụ thể ghi trước launch; không sao chép tùy tiện 512 MiB/128 MiB qgroup envelope của M4 sang pilot khác. No benchmark/indexing được authorize bởi bảng này.

## 10. Source ownership và task map

Các path “đề xuất” chưa tồn tại, chỉ tạo khi implement đúng phase; tránh abstraction/registry mới khi concrete service đủ dùng.

| Concern | Existing owner / proposed seam | Kiểm chứng liền kề |
|---|---|---|
| Evidence DTO/API | `rust/crates/zg-engine/src/api/context.rs`; proposed `api/evidence.rs`, typed methods trên `ZvecGrep` | serialization/ranges/backward compatibility |
| Evidence assembly/lookup | `pipelines/indexed_search/`; proposed private `evidence/` service | source hash, budgets, cancellation, no state-lock pinning |
| C# facts | `extraction/code.rs`, `extraction/code/adapter.rs`; proposed evidence-specific C# parser | parser/runtime version, partial/receiver/branch tests |
| SQL/EF facts | proposed private modules dưới evidence service | T-SQL subset, mapping authority, dynamic gaps |
| Optional metadata persistence | `storage/` + `workspace/` owning lifecycle, only derived sidecar | generation/hash invalidation, atomic file-facts publication, retry/reopen |
| CLI/daemon/MCP | `zg-cli`, `zg-daemon-protocol`, `zg-daemon`, `zg-transport-mcp` | actual boundaries, capability schema, origin/auth/cancel |
| Agent loop | adapter/example + schema fixture, không nằm trong engine inference manager | ledger caps, stop conditions, one final response |
| Evaluation | proposed `benchmarks/rag-impact/`, reusable `zg-testkit` fixtures khi phù hợp | gold/split/source hashes, cost/completeness reports |

## 11. Bắt đầu bằng lát nào?

1. R0: annotate **restore → proc/cache** và **personal update → hai SaveChanges/conditional branch**; đóng băng source variants và các forbidden claims.
2. R1: batch evidence refs/hash/range + stale/secret/scope tests; thử scripted agent loop bằng tool hiện có, chưa cần graph store.
3. R2/R3: chỉ đủ C#/SQL rules để pass hai vertical cases và các negative twins; rồi mới mở reverse-read/view case.
4. R4/R5: đo lợi ích tổng thể và footprint; chỉ lúc đó quyết định có đáng thêm SQLite/semantic compiler hay không.

Độ lớn dự kiến: R0/R1 nhỏ; R2/R3 là phần khó nhất do binding/mapping/T-SQL và phải chia theo rule families; R4 vừa; R5/R6 phụ thuộc evidence và packaging. Không cam kết ngày hoàn tất hoặc nhiều experiment trước khi baseline có thật.

**Definition of MVP:** một agent bên ngoài dùng local evidence capability có thể xử lý ba loại câu hỏi trên selected HRM2.0 scope, tự kiểm trong budget, trả một final answer có source và phân biệt đủ ba lớp kết luận. Không gọi một search wrapper hay một graph demo đơn lẻ là RAG upgrade hoàn tất.
