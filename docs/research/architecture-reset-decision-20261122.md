# Quyết định kiến trúc — 22-11-2026

## Kết luận

**SIMPLIFY.** Dừng đầu tư vào resolver C# Tree-Sitter hiện tại và workflow đóng gói evidence bằng rg có giới hạn ngân sách đã thử. Tìm literal hoặc partial identifier bằng lexical search; chỉ dựa vào indexed retrieval cho câu hỏi semantic/cross-file sau khi xác nhận index hoạt động bình thường.

Đây là quyết định về các implementation đã đo, không bác bỏ nhu cầu semantic retrieval hoặc caller navigation nói chung. Prototype Tree-Sitter offline riêng cho Rust/Python/TypeScript vẫn ở trạng thái experimental, opt-in; quyết định này không đánh giá prototype đó.

## Giả thuyết và bằng chứng

| Giả thuyết | Bằng chứng | Kết luận |
|---|---|---|
| Resolver C# dựa trên cú pháp có thể thay rg/Roslyn với chi phí thấp trên repo nhiều project | Snapshot Avalonia đã đóng băng: 1.959 file C#; 0/168 caller edges chính xác, 0/168 definite edges, 168/168 vị trí cần tìm bị báo unresolved; tìm partial type chỉ được 3/11 vị trí. Resolver mất 5,7–19,1 giây/query so với khoảng 13–15 ms của rg. Có 44 file lỗi parse khiến implementation xóa toàn bộ definite edges. Roslyn bind được cả 168 frozen sites trong project context rút gọn. | **NO_GO** cho resolver hiện tại. Dùng lexical search cho literal; nếu cần semantic binding thì dùng Roslyn có project context. Mới đo một repo công khai nên chưa biết mức phổ biến ngoài corpus này. |
| Workflow đóng gói evidence từ rg với budget thu được source spans HRM2 cần thiết | Báo cáo D01–D12 đã sanitize: arm pack tốt nhất có 1/25 annotated spans, baseline first-hit có 0/25; lexical search không giới hạn tìm được 11/25 nhưng output 8,91 MB. Gold hiện có không cung cấp declaration IDs hay caller-edge labels. | **NO_GO** cho workflow có giới hạn đã thử; không suy rộng thành bác bỏ mọi local-first retrieval. Raw artifacts private vẫn nằm local. |
| Partial identifier qua rg là đường tìm source hữu ích, chi phí thấp | Probe public đã đóng băng 5 declaration targets trước khi chạy. Native rg và zg --rg đều tìm thấy 5/5; negative control có 0 source matches. Bốn query positive hẹp: median native rg 5,0–5,7 ms, managed zg --rg 13,6–23,5 ms. Negative control: 5,6 / 11,3 ms. Query mơ hồ format: 5,3 / 88,9 ms; output 22,9 KB / 276 dòng so với 17,7 KB / 365 dòng. Peak RSS query đó: 7.300 KiB so với 39.032 KiB. | **GO** cho khả năng literal search trong task set nhỏ này; **INCONCLUSIVE** về recall tổng quát, false-positive đã adjudicate hoặc tác động lên hành vi agent. Native rg là baseline đơn giản, ít tốn kém hơn. Managed formatting giảm số byte trong các quan sát này nhưng tốn thời gian và memory hơn. |
| Indexed search vốn không tìm được partial identifier | Báo cáo trước ghi FTS exact search tìm searchWorkspaceIndex nhưng partial searchWork không có kết quả. Kiểm tra sau đó xác nhận index generation 6e3c3a4d-4690-4868-8f9e-1023bbb7577c bị CORRUPT; truy vấn FTS tiếp theo lỗi khi mở storage. | **INCONCLUSIVE**; kết quả cũ không phân biệt được query semantics với index failure. Báo cáo cũ đã được đính chính trong [source-discovery-autonomous-20261122.md](source-discovery-autonomous-20261122.md). |
| Hướng dẫn mới khiến external agents chọn đúng route | Contract tests xác nhận nội dung hướng dẫn có trong MCP/installer. Một subagent exploratory dùng native rg cho một task và tìm được bốn dòng liên quan. Không có paired agent run hay trace/accounting riêng theo agent. | **INCONCLUSIVE**; chưa đo được thay đổi hành vi. |

## Probe lexical đã đóng băng

Thử nghiệm dùng source revision 663566546f03768b9b021c65eb3accc4aa0720a1; target và cách chấm được đóng băng trước khi chạy sáu query. SHA-256 của freeze là:

**81a98b6b3bc99565ddba6456bcc5019c8a6177c8a0d94b74e9b81cc30c43430e**

Task freeze và kết quả theo từng task: [partial-id-freeze.json](../../research/architecture-reset-20261122/partial-id-freeze.json), [partial-id-results.json](../../research/architecture-reset-20261122/partial-id-results.json).

Runtime là /home/masis/.local/bin/zg, version 0.0.1, SHA-256 6db0f67e05e61ad6d50e8e9ea7b3e4593cfb07c99ac0af9f5064f48398db7ed4. Chưa xác minh binary này được build từ TypeScript checkout hiện tại. Native ripgrep là 15.2.0, SHA-256 e62198eb19b136b88c330af83647b5a962cb99b6b1f066758568f12de1974849. Mỗi query có bảy cặp process runs, thứ tự hai lệnh luân phiên; peak RSS được đo bằng các lượt /usr/bin/time riêng.

Hai route đều tìm đúng 5/5 declaration targets đã chọn trước. Đây không phải mẫu đại diện để ước lượng recall. Số dòng/byte output không đồng nghĩa với false-positive reduction.

Subagent fresh-context được giao một partial-name task trên source công khai. Nó dùng native rg và tìm được definition cùng ba import/call sites. Đây là probe về khả năng thực hiện một task; không phải agent A/B hay tool-call count có thể kiểm toán độc lập.

## Phản biện độc lập

Fresh-context reviewer xác định hai giới hạn trọng yếu:

1. Kết quả FTS “No matches” cũ không thể gán cho semantics của partial query vì index đó về sau được xác nhận là corrupt. Báo cáo đã được sửa; hành vi indexed search trên partial query chưa được đo hợp lệ.
2. Kết quả của resolver C# và workflow capped-pack chỉ bác bỏ đúng các thiết kế đã thử. Chúng không bác bỏ semantic retrieval, toàn bộ caller-navigation use cases hay mọi local-first design.

Reviewer cũng nhắc rằng contract tests chỉ xác nhận hướng dẫn được cài đặt, không chứng minh agent sẽ tuân theo. Các giới hạn này đã được giữ trong kết luận.

## Roadmap

1. **STOP** mở rộng resolver C# Tree-Sitter và workflow capped-pack đã thử. Giữ artifact để bảo toàn provenance và có rollback.
2. **SIMPLIFY** exact source lookup bằng native ripgrep khi host có công cụ đó. Dùng managed zg --rg khi AST/context formatting đáng giá hơn overhead đo được; kết quả hiện tại chỉ áp dụng cho binary và repo cụ thể này.
3. **BUILD sau khi môi trường sẵn sàng:** tạo đường đánh giá indexed search local đã được cho phép và khỏe mạnh, không đụng persisted index trong mission này. Sau đó đóng băng task semantic/cross-file trên source công khai và so với lexical baseline mạnh. Trước lúc đó, lợi ích semantic retrieval vẫn là **INCONCLUSIVE**.
4. Nếu default MCP host không có shell/native search và dùng toolset agent mặc định, quyết định riêng có cần expose capability no-index zvec_grep_rg hiện có hay không. Mission này không đổi public toolset/API.

## Thay đổi, checkpoint và rollback

Không sửa runtime, source, API, production defaults, index, embedding, frozen benchmark hay private source. Mission thêm memo này, frozen public lexical probe và một correction vào báo cáo sanitized cũ. Các untracked artifact đã tồn tại từ trước gồm research/m4-native/, session retrospective và real-C# evaluation directory được giữ nguyên. Không có source rollback; nếu cần hoàn tác thì revert riêng commit tài liệu của mission này. Không gọi API, remote inference, install, push, merge hay release.

Canonical handoff: [HANDOFF.md](../../rust/experiments/source-discovery/HANDOFF.md).
