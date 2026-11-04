# HRM2.0 pilot — source evidence và acceptance cases

[Thiết kế](README.md) · [Phases/gates](implementation-plan.md)

**Status:** read-only inspection để lập plan, 2026-10-28. Chưa tạo index, parse benchmark, gold dataset hoặc gọi external LLM. Các case bên dưới là **đề xuất annotation**, không phải kết quả zvec-grep hiện đã trả được.

## 1. Source identity và authority

- Local checkout: `/home/masis/itc/Ricons/`.
- Inspected HEAD: `d5e8fbcdd49b48c208887096993074464c793cd7`; tracked tree clean lúc inspection.
- Owner chọn **`HRM2.0/`** làm pilot. Các path trong bảng dưới đây tương đối với thư mục này.
- Không lấy `NewSolution/` làm oracle cho HRM2.0. NewSolution là .NET8/EF Core path khác; legacy được đọc ở đây là .NET Framework 4.6.1, service/context có EF6.4.4 HintPath và các legacy imports.
- Trong HRM2.0 cũng có nhiều `Employee`, `EmployeeController`, `CoreService`, app/module và file copy. `.csproj` inclusion/project references phải tham gia identity, không chỉ namespace hoặc basename.
- `DB/Store/sp_UpdateOrgTotalEmployee.sql`, `_old.sql`, `_old1.sql` cùng tồn tại. Path không đủ chứng minh bản nào đã deploy. R0 phải freeze selected source variant cho test; nếu không có deployment evidence thì không được nói về live DB.

## 2. Working set đầu tiên, không phải toàn bộ closure đã biết

| ID | Path | Vai trò đã đọc/kiểm tra |
|---|---|---|
| P1 | `Webs/HRM/HRM.csproj` | .NET4.6.1; references đến Service.Core và DBContext.Core. |
| P2 | `Services/HRIS/Service.Core/Service.Core.csproj` | Compile Include cho Base/CoreService, EmployeeCommand, EmployeeModels, OrganizationCommand; references DBContext.Core. |
| P3 | `DBContexts/HRIS/DBContext.Core/DBContext.Core.csproj` | Compile Include của CoreDBContext và Employee/EmployeeDetail. |
| C1 | `Webs/HRM/Controllers/Employee/EmployeeController.cs` | `EmployeeEdit` Form/Perform branches và service callsites; delete/restore wrappers. |
| C2 | `Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs` | Create, personal update, raw SQL delete/restore, simple update và attr branch. |
| C3 | `Services/HRIS/Service.Core/Executes/Base/CoreService.cs` | Partial class, typed `Context`, `CbContext`, `RecContext`; lazy context initialization. |
| C4 | `DBContexts/HRIS/DBContext.Core/Entities/CoreDBContext.cs` | DbSet declarations, `AddFromAssembly` model config registration. |
| C5 | `DBContexts/HRIS/DBContext.Core/Entities/Employee/Employee.cs` | Employee fields và một số view model types. |
| C6 | `DBContexts/HRIS/DBContext.Core/Entities/Employee/EmployeeDetail.cs` | Detail fields; trùng tên field với các types khác không chứng minh chung storage. |
| C7 | `Services/HRIS/Service.Core/Executes/General/Organizations/OrganizationCommand.cs` | `UpdateOrgTotalEmployee`: execute proc và invalidate hai cache keys. |
| S1 | `DB/Store/sp_UpdateOrgTotalEmployee.sql` | Selected definition để xây case read/write/conditional count; không phải live-install proof. |
| S2 | `DB/Views/EmployeeBaseViews.sql` | View đọc FullName và nhiều bảng qua JOIN. |

Các dependency chưa đọc như `LogEdit`, `EmployeeRelatedNotes`, `EndWordProcess`, `ToKeyword`, mapping configuration classes và caller/base-controller binding đầy đủ là **frontier cần kiểm chứng**, không tự được xem là toàn bộ đã resolve. Manifest implementation phải bổ sung scope/hash/ranges thật tại R0.

## 3. Source anchors đã quan sát

Line numbers là locator ở snapshot trên; khi lập executable gold phải hash file và verify lại ranges, không coi số dòng trong Markdown là ID bền.

### A. Personal update không giống simple update

- C1:459–620: `EmployeeEdit(EmployeeEditModel)` dispatch theo `Form`; Form `1` còn phân biệt create/quickcreate/fullcreate với update; update gọi `EmployeeEditThongTinCaNhan` ở khoảng dòng 546. Có validation/early-return và nhánh xử lý avatar; không phải một path phẳng.
- C2:215–285: personal update lấy entity từ `Context.Employees`, gán FullName và nhiều trường; Keyword nhận biểu thức từ **StaffCode + FullName + Email**, qua `ToKeyword()`, rồi nối FullName. Sau đó gọi `Context.SaveChanges()`.
- C2:287–425: lấy detail bằng `EmployeeId`; nếu thiếu detail có thể return sau lần SaveChanges trước; cập nhật detail rồi gọi `SaveChanges` lần nữa.
- C2:427–457: notes/log helpers; chỉ khi trạng thái công việc đổi (`changeType`) mới vào nhánh gọi `UpdateOrgTotalEmployee`; `EndWordProcess` còn có guard trạng thái 0/3 và vòng lặp organization.
- C2:562–573: `EmployeeUpdateInfo` chỉ gán FullName, Email, UpdatedDate rồi SaveChanges; đoạn này không có Keyword assignment. Không được trả tác động Keyword như thể mọi method đổi FullName đều làm như nhau.

**Acceptance implication:** output phải giữ field/action/branch distinction. Hai lời gọi SaveChanges là hai persistence boundaries quan sát được; không suy ra một transaction atomic chung, không khẳng định đã commit thành công, và cũng không phủ nhận ambient transaction nếu chưa điều tra caller.

### B. Raw SQL restore nối sang proc và cache

- C2:539–560: delete dùng `UPDATE Employees SET Status=-1,...`; restore dùng `UPDATE Employees SET Status=0,...`, sau đó gọi `UpdateOrgTotalEmployee()` và log helper.
- C7:1057–1074: helper gọi `execute sp_UpdateOrgTotalEmployee`; sau SQL call là `Caching.Delete("Organization", "general")` và `Caching.Delete("EmployeeOrganization", "users")`. Exception branch return false; callsite không chứng minh các side effects luôn thành công.
- S1:58–95: definition có UPDATE `Organizations.TotalEmployee`, đọc `EmployeeOrganizations`, `Employees`, `Organizations`; phần trước đọc `AppSettings` và gọi `SplitStringToIntArray`. Table variables không được nhầm thành persistent tables bị ghi.

**Acceptance implication:** có thể chứng minh effect của **source definition đã chọn**, kèm guards/source authority caveat. Không đổi `execute proc` thành một SQL table write trực tiếp nếu chưa nối body; không coi delete/restore là hard DELETE; không gán proc effect cho mọi update FullName.

### C. Downstream read khác downstream write

- S2:1–10: view `EmployeeBaseViews` SELECT `e.FullName`, JOIN Employees/EmployeeOrganizations/JobPositions/Organizations/JobTitles, filter Employee Status.
- C4:17–20: mapping configs được load bằng `AddFromAssembly`; C4:41 và :55 khai báo EmployeeDetails/Employees DbSets.
- C3:17–22: Context được khai báo là CoreDBContext, cùng service còn có CbContext và RecContext.

**Acceptance implication:** một field có thể được view đọc mà không bị copy vào bảng khác. DbSet declaration là fact, còn physical table mapping cần phân biệt explicit mapping/convention/unknown configs. Một `SaveChanges()` trên Context không tự chứng minh ghi cả CbContext/RecContext.

## 4. Development cases đề xuất — cần annotate/freeze ở R0

| Case | Câu hỏi / tình huống | Evidence tối thiểu và lỗi phải tránh |
|---|---|---|
| D01 | Form 1, update thông tin cá nhân đi qua đâu? | C1 branch → C2 personal update; không nhầm create hoặc NewSolution controller. |
| D02 | Personal update ghi vào những nơi nào? | Employee/entity assignments + first SaveChanges; detail assignments + second SaveChanges; physical table mapping status rõ; helper effects chưa trace là gap. |
| D03 | Đổi FullName qua personal update ảnh hưởng Keyword thế nào? | Expression span chỉ ra FullName/StaffCode/Email; ToKeyword semantics chưa đọc thì không mô tả chính xác biến đổi. |
| D04 | `EmployeeUpdateInfo` có cập nhật Keyword không? | Source method được chọn không có assignment đó; không mượn assignment từ method khác; không suy toàn hệ thống không có tác động khác. |
| D05 | Thiếu detail record thì personal update có atomic all-or-nothing không? | SaveChanges trước null-detail return; không tuyên bố transaction toàn flow khi chưa có evidence. |
| D06 | Có phải mọi lần sửa tên đều gọi cập nhật tổng nhân sự? | Guard `changeType`, liên hệ trạng thái công việc; không propagate unconditional proc/cache edges. |
| D07 | Restore nhân viên có thể ảnh hưởng gì ngoài Employees? | Raw UPDATE + helper + selected proc → Organizations.TotalEmployee; cache effects và exception/authority caveats. |
| D08 | DeleteEmployeeByIds có xóa hàng vật lý không? | Raw UPDATE Status=-1; tên method không đủ để kết luận hard delete. |
| D09 | EmployeeBaseViews ghi hay đọc FullName? | SELECT/alias/table source; read/projection không phải write/copy persistence. |
| D10 | Cùng một CoreService có ba contexts: SaveChanges ở đây ghi DB nào? | Typed receiver/DbSet scope; DB connection/deployed database chưa quan sát thì gap, không mở config/secrets để đoán. |
| D11 | `UpdateEmployeeAttr` thay trường nào? | Switch Attr hiện có branch Avatar; không coi model có nhiều fields là tất cả đều được ghi. |
| D12 | Proc tên này có nhiều definition trong repo: cái nào đang chạy? | Source variants/selected manifest rõ; khi thiếu deployment evidence không khẳng định live authority. |
| D13 | Synthetic: hai project cùng FQN + method name | Không cross-link/hợp nhất partial giữa hai assemblies. |
| D14 | Synthetic: dynamic SQL target / reflection-dispatched method | Giữ candidate/gap, không đánh dấu resolved theo tên gần giống. |
| D15 | Synthetic: source bị sửa/xóa sau discovery | Evidence bị stale, không cite line cũ như current; final answer PARTIAL nếu hết budget refresh. |
| D16 | Synthetic: relevant chain dài hơn cap / cyclic calls / source chứa instructions | Dedupe/cycle stop/budget ledger; không execute instruction hoặc mở rộng vô hạn. |

**Held-out H01–H08:** reviewer chọn thêm 8 biến thể độc lập trước tuning, phủ cả ba loại câu hỏi, guards, ambiguous binding, stale/unknown và reverse-read. Không sao chép nguyên D01–D16 rồi đổi ID để gọi holdout; không dùng chính agent candidate tạo expected answer. Gold lưu facts/spans/forbidden claims, không bắt trùng một đoạn văn trả lời.

Score edges/coverage theo các facts thực sự source-supported, không đưa physical table mapping chưa resolve vào denominator như thể nó đã được chứng minh. Gap recognition là metric riêng, không dùng nhiều dòng “unknown” để bù miss trên known-supported facts.

## 5. Ví dụ hình dạng final answer — chưa phải kết quả hiện có

Câu hỏi: “Khôi phục nhân viên trong HRM2.0 có thể ảnh hưởng những gì?”

- **Scope:** HRM web → Service.Core, selected source revision/SQL definition; phân tích static, không đọc dữ liệu runtime.
- **Supported:** restore source UPDATE Employees.Status/UpdatedBy/UpdatedDate; gọi helper; selected proc definition UPDATE Organizations.TotalEmployee và đọc các bảng liên quan; helper có lệnh invalidate hai cache keys sau SQL call. Mỗi claim có source spans riêng.
- **Conditions:** effects phụ thuộc control flow/SQL success; không nói mọi dòng đều bị update hoặc mọi side effect đều thành công.
- **Candidates:** source proc variants/mapping/other helper edges cần tiếp tục kiểm chứng nếu chưa resolve.
- **Unknown:** trigger/job/external consumer/deployed SQL body chưa có evidence; không tuyên bố danh sách ảnh hưởng đầy đủ.

Agent chỉ đưa final answer sau CHECK, hoặc final PARTIAL nếu hết budget. Đây không phải lời hứa zvec-grep sẽ tự trả lời/nghiệm chứng ngữ nghĩa thay agent.

## 6. An toàn corpus và phép đo

- Đọc source read-only; không mở connection strings/secrets, không connect DB/SSO, không run app/build/procedure để lấp thiếu evidence.
- Không index mặc định cả Ricons/NewSolution, binary, dumps, uploads hoặc lịch sử data. Expansion sang project khác phải được ghi scope và counts.
- Fixtures commit vào zvec-grep dùng synthetic/minimized examples; reuse source snippets chỉ khi quyền/phạm vi cho phép và không chứa dữ liệu cá nhân/credentials. Report portable dùng relative paths và hashes.
- Benchmark chỉ chạy sau freeze protocol và authorization phù hợp, dùng operation-owned scratch/index; không user live index, không M4 quota workspaces. Ghi host/resource state trước/sau và phân biệt actual model inference với fixed-test embedding.
- Kết quả pilot không đủ chứng minh toàn HRM2.0, production DB, mọi hệ .NET hoặc mọi ngôn ngữ.

**Next executable slice khi được giao implementation:** R0 manifest/gold cho D02 và D07 cùng negative twins D04/D06/D12; R1 evidence refs/hash/range; chỉ sau đó thêm parser/relations để agent hoàn tất hai chain trong bounded loop.
