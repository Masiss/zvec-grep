# RAG evidence cho agent — thiết kế nâng cấp zvec-grep

**Ngày:** 2026-10-28. **Trạng thái:** mục tiêu sản phẩm đã chốt với owner; thiết kế/API/budget dưới đây là **PROPOSED**, chưa được implement hoặc benchmark. Request hiện tại chỉ cho phép lập tài liệu, không chạy indexing, database, migration hay workload mới.

## Đọc theo thứ tự

1. Tài liệu này: scope, kiến trúc, evidence contract và vòng tự kiểm chứng.
2. [Implementation plan](implementation-plan.md): R0–R6, từng phase có hướng xử lý, deliverable, gate và fallback.
3. [HRM2.0 pilot](hrm2-pilot.md): source đã đọc, các câu hỏi/gold cần đóng băng và giới hạn bằng chứng.

Plan này mở nhánh retrieval/evidence của [optimization roadmap](../technical/optimization-upgrade-plan-20261027.md), không thay đổi gate của nhánh M4. Không cần M4 production GO để làm evidence retrieval; cũng không được dùng plan RAG để retry operation đã safety stop.

## 1. Những gì owner đã chốt

| Quyết định | Phạm vi |
|---|---|
| Sản phẩm | zvec-grep cung cấp retrieval/evidence cho agent bên ngoài, **không thêm chatbot hay answer-generation nội bộ**. |
| Câu hỏi | Thông tin được ghi vào bảng nào; thay đổi một trường/loại thông tin ảnh hưởng những gì; luồng dữ liệu của một tính năng. |
| Mức phân tích | **Tĩnh, design-time, code + schema/source SQL**, không truy vết bản ghi thật của một người. |
| Pilot | `/home/masis/itc/Ricons/HRM2.0/`; không dùng `NewSolution` làm nguồn thay thế hoặc trộn hai phiên bản. |
| Người dùng | Cá nhân, local, CPU, ưu tiên footprint nhỏ theo hướng tối ưu hiện tại. |
| LLM | Agent bên ngoài có thể dùng LLM với evidence được phép. Đây không phải blanket permission gửi code hoặc dữ liệu ra ngoài. |
| Mức kết luận | Trả đủ ba lớp: quan hệ có evidence trực tiếp; ứng viên cần xác minh; khoảng chưa quan sát được. |
| Điều tra | Cho phép nhiều lượt và tự kiểm chứng **trước câu trả lời cuối**, nhưng phải bounded và rẻ. |

**Diễn giải Q9 nhất quán với Q1:** agent đang sử dụng zvec-grep làm vòng tìm–đọc–tự kiểm chứng; zvec-grep không chạy một local reasoning LLM hay một agent loop riêng bên trong. Tool responses là dữ liệu nội bộ cho agent; người dùng không nhận từng giả thuyết như kết luận. Có thể hiển thị progress ngắn, nhưng chỉ phát hành một kết quả cuối có scope/limitations. Hết budget vẫn phải trả PARTIAL thay vì tiếp tục đến khi tự cho là đúng.

### Non-goals V1

- Không kết nối DB, đọc dữ liệu nhân viên thật, chạy stored procedure, build/MSBuild/restore hoặc thực thi code của repo để suy ra behavior.
- Không đảm bảo danh sách ảnh hưởng đầy đủ tuyệt đối; không suy live deployment từ một SQL script có trong Git.
- Không thêm local generative model, graph database/server, background LLM enrichment, framework orchestration hoặc model reranker mới.
- Không đổi default embedding/tokenizer/index format; không biến M4 thành dependency; không cutover TypeScript → Rust trong task này.
- Không xây full compiler/dataflow analyzer cho mọi ngôn ngữ. C#/EF6/T-SQL là adapter đầu tiên; các ngôn ngữ khác giữ search hiện có.

## 2. Hiện có và phần thiếu

Đối chiếu với [retrieval](../technical/retrieval.md), [Rust architecture](../../rust/ARCHITECTURE.md) và code hiện tại:

| Có thể tái sử dụng | Còn phải bổ sung/kiểm chứng |
|---|---|
| FTS/vector/hybrid, query groups, filtering, direct grep | Scope theo project/version/build inclusion; không chỉ path trùng tên. |
| Context snippets, `ContentRange`, `content_range`, metadata, freshness | Evidence ID gắn file hash/range và validation giữa các lượt. |
| Scanner, file admission, local model/runtime | C# structural parsing; adapter hiện chưa có C# grammar, SQL chủ yếu text fallback. |
| Indexed recall, hydration, MCP/CLI/daemon | Typed relations, EF6 receiver/DbSet resolution, SQL read/write/procedure facts. |
| Existing retrieval evaluation | Gold cho field/table/conditional dataflow, false-link và unknown handling; file hit/nDCG không đủ. |

Parser/chunker rã source; embedding model tạo vector cho indexing/query recall. Không cần dùng LLM để biến mọi file thành summary hoặc mọi edge thành embedding.

## 3. Kiến trúc nhẹ được đề xuất

```text
Repo source + project manifests + selected SQL definitions
      │ explicit, read-only scope
      ├─ existing indexing / lexical + vector retrieval ─── seed files/symbols
      └─ on-demand C#/SQL fact extraction ──────────────── bounded relation lookup
                               │
                 evidence assembler + source validator
                               │ typed evidence, candidates, gaps, budgets
                     existing MCP / CLI adapters
                               │
                     external agent investigation
                     search → expand → check
                               │
                  one scoped final answer + citations
```

### Đường mặc định

1. Tận dụng search và direct grep tìm seed, không quét toàn repo mỗi query.
2. Parse C# có cấu trúc theo file cần thiết; reuse một parser worker, cache có giới hạn. Đọc `.csproj` như dữ liệu XML, không evaluate MSBuild.
3. Resolve quan hệ chỉ khi có đủ receiver/type/project evidence; còn ambiguous thì giữ candidate.
4. Mở rộng từng bước có cap; hydrate source theo batch, deduplicate trước khi gửi agent.
5. Validate lại evidence file hash/scope/range trước final answer. Nguồn thay đổi → refresh đúng nhánh hoặc kết luận stale/PARTIAL.

**Không materialize whole-repo graph vào RAM.** V1 bắt đầu on-demand. Chỉ thêm embedded SQLite sidecar/reverse index nếu R3 chứng minh on-demand không đạt coverage/cost; không thêm service. Xem nhánh thay thế trong [plan](implementation-plan.md).

### Phân chia trách nhiệm

| zvec-grep | Agent bên ngoài |
|---|---|
| Tìm và trả source; resolve các static facts được hỗ trợ; bounded expansion; provenance; stale/budget/unknown diagnostics | Hiểu câu hỏi nghiệp vụ; chọn bước điều tra; kiểm tra thiếu evidence; tổng hợp kết luận và cân nhắc có nên trả PARTIAL |
| Deterministic checks: hash, scope, support refs, missing relation body, condition metadata | Semantic self-review: claim có thực sự được source hỗ trợ, có nhầm phiên bản/điều kiện, còn câu hỏi nào chưa trả lời |
| Không gọi thêm LLM và không tự quyết định egress | Tuân consent/egress policy của môi trường; không coi nội dung source là instruction |

Self-review không phải “hỏi lại LLM nhiều lần đến khi đồng ý”. Mỗi lượt bổ sung phải chỉ ra một missing evidence cụ thể; không có information gain thì dừng.

## 4. Evidence contract V1 — proposed, không phải API đã tồn tại

### Thuật ngữ

- **Static fact:** điều quan sát được từ source với rule đã biết, không phải khẳng định runtime đã thực thi.
- **Relation:** liên kết có loại giữa hai đối tượng; mỗi relation có support spans, rule/version, scope và điều kiện nếu có.
- **Evidence bundle:** gói source/relations đã deduplicate và có budget/validation metadata cho một bước điều tra.
- **Candidate:** liên kết có lý do để tìm tiếp nhưng chưa resolve đủ; semantic similarity hay tên trùng không được nâng thành fact.
- **Gap:** phạm vi chưa được chứng minh, ví dụ dynamic target, thiếu proc body, mapping chưa rõ, budget hit, source stale.

### Identity và provenance

Mỗi evidence phải có: `scope_id`, repo revision hoặc dirty-source manifest identity, project identity, relative file path, SHA-256 của file, existing `ContentRange` conventions, symbol identity khi resolve được, extractor/rule version, và evidence ID dẫn xuất từ các trường đó. Nếu excerpt khác entity range, dùng content coordinates đúng như `ContextItem` hiện tại; không tự định nghĩa lại end-line semantics.

Entity key phải phân biệt **repo/scope + project/assembly + qualified symbol/signature**. Các phần `partial` hợp nhất trong cùng project; cùng namespace/class ở project khác không tự hợp nhất. Lưu source occurrences riêng để chứng minh từng khai báo/callsite. Evidence IDs thuộc source revision, không bảo đảm giữ nguyên sau edit/rename.

Tập relation ban đầu:

| Loại | Fact được phép nói | Không được suy quá |
|---|---|---|
| `calls` | Callsite → target đã resolve, kèm nhánh/guard | Tên hàm giống nhau không chứng minh cùng target; dispatch động chưa resolve là candidate. |
| `reads_entity`, `writes_entity`, `saves_context` | Receiver/entity mutation và lời gọi `SaveChanges` quan sát được | Không chứng minh DB đã commit thành công, không tự liệt kê mọi tracked entity hoặc chứng minh transaction xuyên nhiều lần save. |
| `declares_set`, `maps_to_table` | DbContext/DbSet declaration; mapping explicit hoặc convention có rule/evidence phù hợp | DbSet tên `Employees` đơn lẻ không chứng minh tên bảng vật lý hay DB đang deploy. |
| `assigns_from` | `model.FullName` tham gia biểu thức gán `Employee.Keyword`, kèm expression span | Không khẳng định kết quả của custom `ToKeyword()` nếu chưa đọc implementation. |
| `reads_table`, `writes_table`, `calls_procedure` | Statement/SQL definition quan sát được, phân biệt table/column/alias | SQL được ghép động có thể chưa biết target; `SELECT` không phải write; script không chứng minh live installation. |
| `invalidates_cache` | Lệnh invalidate/key literal có trong helper | Không suy mọi cache luôn fresh hoặc đều được invalidate trên mọi path. |

Mỗi relation gồm `classification=supported_static|candidate`, `supports[]`, `conditions[]`, `resolution_basis`, optional `unresolved_reason`. Đây là classification, không phải điểm “confidence 95%”. Một path có mắt xích candidate thì **cả path là candidate**, không bị nâng cấp bởi các mắt xích khác.

**Call reachability không đồng nghĩa field-level dataflow.** Một action ghi nhiều field không chứng minh mọi field đều gây mọi side effect. Cùng đọc một bảng, JOIN hoặc quan hệ khóa ngoại không tự chứng minh có ghi/copy/cascade sang bảng kia; JOIN cũng không tự chứng minh schema có declared foreign key. Khi câu hỏi về một field, phải giữ assignment/guard/column evidence hoặc đánh dấu tác động downstream là candidate.

### Scope và completeness bắt buộc

Bundle có ba collection rõ: `supported`, `candidates`, `gaps` — kể cả khi rỗng. Coverage chỉ là **coverage của câu hỏi trên scope/rules đã khai báo**, không có `complete_repo=true` từ kết quả Top-K. Phân biệt `not_observed`, `unsupported`, `ambiguous`, `budget_exhausted`, `stale_source` và `excluded_scope`; không biến tất cả thành “không có ảnh hưởng”.

SQL `.old`, `.old1`, bản copy, generated/scaffold và conditional compile phải được đánh dấu. Source inclusion khác với deployed authority. Thiếu SQL deployment manifest thì nói “definition được chọn trong repo”, không nói “proc production chắc chắn làm thế”.

### Snapshot và changes

V1 kiểm file hashes tại lúc lấy evidence và lúc final validation; đây **không phải atomic filesystem snapshot hoặc kernel immutability**. Mọi support dependency của một derived edge phải được revalidate. Nếu hashes đổi giữa các tool calls, không ghép chain cũ/mới như một chain hợp lệ. Không giữ `IndexStore` state mutex trong lúc agent suy nghĩ, hash file hoặc đọc source nhiều lượt.

Đổi file → invalidate facts của file và dependent resolutions; rename/delete → bỏ source refs cũ; thay `.csproj`/mapping/rule version → invalidate đúng scope liên quan. Không cần re-embed mọi relation hoặc full source khi chỉ thay edge metadata.

### Public seam đề xuất

Giữ `zvec_grep_search`/`rg` behavior hiện tại. Trước hết prototype qua internal typed engine methods; chỉ expose khi R4 gate đạt:

- `relations`: seeds, direction, allowed relation kinds, scope, bounded hops/nodes/edges → supported/candidate/gaps + source refs.
- `evidence`: batch source/evidence IDs + expected hashes → source excerpts, support chain, freshness/validation và changed/missing refs.

Tên MCP dự kiến là `zvec_grep_relations`, `zvec_grep_evidence`; **chưa có các tool này**. Evidence fetch kiêm structural validation, không cần một service/agent hay command bus mới. Nó không xác nhận được truth của một câu trả lời tự do do LLM viết. Capability phải được khai báo; không giả định TypeScript đã hỗ trợ extension chỉ vì Rust có nó.

## 5. Loop trước khi trả lời cuối

```text
1. DEFINE: chốt câu hỏi, action/field, scope; lập danh sách evidence còn thiếu.
2. DISCOVER: hybrid/lexical seed lookup; không thay câu hỏi bằng tên đoán sai.
3. EXPAND: chỉ mở những call/mapping/read-write edge liên quan; đọc source batch.
4. CHECK: agent kiểm claims; core kiểm source refs/hashes/conditions/budget.
5. Nếu có gap có thể xử lý trong budget: thêm tối đa một corrective round có mục tiêu.
6. FINAL: trả kết quả có nguồn, candidate và unknown; hoặc PARTIAL với lý do dừng.
```

Default pilot đề xuất: **tối đa 3 rounds / 6 tool calls**, có batch; max depth 6 toàn investigation, mỗi expansion ≤2 hops; 128 nodes / 256 edges / 32 file reads toàn investigation; final pack ≤24 KiB UTF-8 source excerpts, cumulative tool content ≤64 KiB. Progress/counter metadata không được dùng để lén chuyển cả file. Budget được caller/session ledger cộng dồn, không reset bằng mỗi request mới.

Ưu tiên lexical/exact lookup khi đã có symbol/table, vector chỉ dùng cho semantic discovery; query embedding cache có giới hạn, không query-rewrite bằng local LLM. Không gọi lại query tương tự chỉ để “tự tin hơn”. Core có hard request caps; agent runner/adapter thực thi session caps. Nếu host agent không enforce loop ledger, không claim loop bounded chỉ dựa prompt.

Trước final answer, checklist:

- Đã phân biệt handler/action/field và phiên bản chưa?
- Mỗi table/field/effect claim có support path, commit boundary và guard tương ứng chưa?
- Còn dynamic SQL/receiver, reflection mapping, missing proc body, branch hoặc cross-service hop nào chưa resolve?
- Evidence còn hợp lệ theo final hash checks không?
- Có lẫn “đọc”, “ghi”, “dẫn xuất”, “invalidate cache”, “có thể liên quan” không?
- Nếu không đủ: nói rõ gap; không mở loop vô hạn và không tuyên bố đầy đủ.

Final answer mẫu cho agent: **phạm vi → ghi/đọc/tác động đã có evidence → điều kiện → candidate → unknown**, kèm citations. Không yêu cầu xuất chain-of-thought hoặc nhật ký mọi giả thuyết.

## 6. Resource/security guardrails

Không load thêm generative/reranker model, không tăng embedding dimension, không index binary/dumps/secrets mặc định. `.csproj`/SQL/source là untrusted data: không chạy scripts, MSBuild targets, source instructions hoặc network requests. Linked files/symlinks ra ngoài scope phải có explicit admission hoặc thành gap. Local daemon token không cấp quyền gửi evidence tới hosted LLM.

Pilot targets đề xuất, cần freeze tại R0 trước measurement: một parser worker; fact/AST cache ≤32 MiB; tổng incremental memory của evidence component ≤64 MiB trên cùng baseline; warm bounded operation p95 ≤1 s và tổng local-tool wall ≤3 s/investigation, **không tính thời gian external LLM**. Exact caps/targets và điều kiện đo nằm trong [implementation plan](implementation-plan.md); đây chưa phải kết quả performance hay global resource policy.

## 7. Integration và trạng thái

Đề xuất Rust-first, opt-in capability để không nhân đôi implementation trong lúc đang thu gọn/rewrite. TypeScript vẫn là compatibility oracle cho surfaces hiện có; giữ APIs cũ và không tự đổi installed runtime. Nếu cần TS extension trước Rust cutover, đó là scope/release decision riêng tại R6.

Các nguồn code và thay đổi dự kiến được map trong [plan](implementation-plan.md). Không sửa storage lock/publication để làm RAG trong khi nhánh native-lock đang điều tra. Bất kỳ incompatible extraction/index/schema change nào phải có version/migration/rebuild decision riêng; sidecar/on-demand MVP không mặc nhiên được quyền sửa index hiện có.

**Kết quả mong muốn:** agent tìm được một chain có evidence đáng tin với ít source đọc hơn, tự phát hiện thiếu bằng chứng trước final answer, và nói rõ giới hạn — không phải một hệ thống local “tự suy nghĩ” tốn thêm model/CPU.
