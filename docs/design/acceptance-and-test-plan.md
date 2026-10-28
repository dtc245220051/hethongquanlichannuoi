# Tiêu chí nghiệm thu, truy vết rubric và kế hoạch kiểm thử

## 1. Ma trận chức năng × 5 tiêu chí chấm điểm

Mỗi ô chỉ rõ chức năng đóng góp như thế nào cho tiêu chí; tiêu chí nghiệm thu/test/evidence chi tiết nằm ở phần 2.

| Chức năng | 1. Phân tích & thiết kế | 2. Hệ thống & AI | 3. Thực nghiệm & đánh giá | 4. Sản phẩm & demo | 5. Phản biện & AI có trách nhiệm |
|---|---|---|---|---|---|
| Đăng nhập, vai trò, farm scope | Use case, threat boundary, ma trận quyền; MVP một farm nhưng mọi dữ liệu mang `farm_id` | Phiên an toàn, permission tối thiểu, bootstrap một lần và support grant read-only theo quyết định độc lập | Test quyền từng hành động, cross-farm, separation-of-duties, grant state và bootstrap replay | Demo chủ farm/nhân viên/admin nền tảng | Người thực hiện giải thích authorization backend, review quyền và tự xác minh kiểm thử cross-scope |
| Danh mục loài/giống, vật nuôi/đàn, chuồng | ERD mở rộng loài, tracking cá thể/group và invariant đếm | CRUD theo workflow, event tách/gộp/chuyển atomic, capacity exception có audit | Constraint, rollback/concurrency, dashboard chống double-count | Demo 3 chuồng, ~30 cá thể và 5 đàn gia cầm, tất cả tổng hợp | Thảo luận giả định tracking, chứng minh mỗi con chỉ thuộc một đơn vị active |
| Sức khỏe và tiêm phòng | Luồng riêng cho lịch dự kiến và outcome thực tế | Owner/delegate quản lý lịch; staff ghi outcome được phép; không xóa lịch sử | Test quyền lập/sửa/hủy, result evidence, overdue theo timezone | Demo lịch sử và lịch cần theo dõi | AI nêu rõ đây là ghi nhận, không chẩn đoán/kê đơn; con người phản biện giới hạn |
| Thức ăn/tồn kho | Data dictionary ledger, kg-only trong MVP; conversion ngoài kg chỉ là mở rộng có phê duyệt/snapshot | Staff ghi tiêu thụ; owner/delegate nhập/điều chỉnh có lý do; chặn tồn âm | Test đơn vị bị từ chối, tồn biên, rollback và giao dịch đồng thời | Demo số liệu tồn kho tổng hợp theo kg | Phân tích rủi ro tồn sai/đơn vị; xác minh phép tính độc lập |
| Dashboard và cảnh báo | Định nghĩa metric, trạng thái và quyền theo vai trò | Staff acknowledge/ghi chú; owner/delegate close/reopen có lý do/audit | Đối chiếu fixture, lọc farm, phân quyền lifecycle cảnh báo | Dashboard responsive/kịch bản trình diễn | Giải thích cảnh báo không phải kết luận thú y; nêu hạn chế dữ liệu |
| AI Agent và tool registry | Sơ đồ luồng, policy, schema, trust boundary; mock/provider gate | 6 tool đọc dữ liệu thật đã lọc synthetic; điều phối mock quy tắc, chưa có LLM/provider | Test từng tool, cross-farm, read-only/audit, call cap, mock không outbound, prompt injection | Demo tool trên dataset tổng hợp được đánh dấu; provider ngoài chưa triển khai | Phân biệt mô phỏng với LLM; phản biện dữ liệu đầu vào, kiểm tra đầu ra và mô tả phần người thực hiện |
| Audit, lỗi, cấu hình và tài liệu | Retention demo 365 ngày audit/90 ngày hội thoại, policy purge | Audit có redaction, admin grant và retention config | Kiểm tra expiry/purge preview, redaction, lỗi DB/API | Docker Compose/hướng dẫn/dữ liệu demo rõ nhãn | Nêu đây là giả định demo chứ không phải kết luận pháp lý; giải thích đánh đổi |

## 2. Nghiệm thu theo từng tiêu chí

### Tiêu chí 1 — Phân tích bài toán và thiết kế giải pháp

**Yêu cầu cụ thể**

- Có mục tiêu, phạm vi MVP/ngoài phạm vi, vai trò, luồng nghiệp vụ, giả định và rủi ro.
- Có sơ đồ kiến trúc, ERD/data dictionary, ma trận quyền, schema tool và luồng AI.
- Định nghĩa farm scope, dữ liệu nhạy cảm, các quyết định chưa xác nhận và cách mở rộng loài.

**Tiêu chí nghiệm thu**

- Mỗi chức năng có actor, quyền, dữ liệu vào/ra và luồng lỗi.
- Tất cả tool có schema và quy tắc giới hạn; không có đường AI truy cập SQL trực tiếp.
- Các giả định chưa xác nhận được đánh dấu; mọi dữ liệu demo được ghi rõ là tổng hợp.
- Quy tắc dashboard được định nghĩa: tổng đầu con chỉ cộng tracking unit hiện hành; cá thể = 1, đàn/lô = `head_count`; source đã tách/gộp/đóng không được cộng cùng target.
- MVP chỉ chấp nhận giao dịch thức ăn bằng kg; conversion ngoài kg chưa bật. Nếu được phê duyệt trong tương lai, hệ số và snapshot giao dịch phải được lưu vết; giao dịch không thể làm tồn âm.
- MVP chỉ nhận đơn vị kg; nhân viên chỉ ghi tiêu thụ; nhập/điều chỉnh tồn yêu cầu owner/delegate có permission và lý do.
- Vượt sức chứa cảnh báo và bị chặn trừ khi owner/delegate có permission gửi request và phê duyệt; người gửi không tự duyệt; backend tính lại trong transaction.
- Staff được acknowledge cảnh báo nhưng không đóng/mở lại; lịch tiêm tách khỏi outcome thực tế, overdue là cờ lịch suy ra; AI không ghi dữ liệu.
- Grant hỗ trợ mặc định không tồn tại; authority độc lập chưa kích hoạt thì backend từ chối; không self-assign/self-approval và issuer khác recipient; quyền đọc audit/hội thoại theo policy đã chốt.
- Seed chỉ chạy tường minh trong test/demo trên database identity đã xác minh; marker synthetic; không auto-seed, xóa, ghi đè hoặc reset; mọi cấu hình thiếu/sai/mâu thuẫn fail-closed.
- Purge bị khóa ở backend và không có lịch chạy cho đến khi quy trình, backup/restore và hold được phê duyệt; retention 365/90 ngày chỉ là giá trị demo.

**Ca kiểm thử/kiểm chứng**

- Review checklist liên kết yêu cầu → thiết kế → test → minh chứng.
- Walk-through 3 vai trò, truy vấn cross-farm và xử lý provider chưa cấu hình.
- Kiểm tra mọi bảng nghiệp vụ có tenant/farm scope hoặc giải thích được ngoại lệ.

**Minh chứng dự kiến**

- Tài liệu thiết kế trong `docs/design/`, sơ đồ Mermaid, quyết định được người dùng duyệt và traceability matrix.

### Tiêu chí 2 — Hoàn thiện hệ thống và chức năng AI

**Yêu cầu cụ thể**

- Module nghiệp vụ MVP hoạt động theo quyền; dashboard/cảnh báo phản ánh dữ liệu.
- Mock orchestrator gọi tool DB chỉ đọc có schema/farm authorization/row cap/audit; không phải LLM. Provider ngoài chưa được tích hợp.
- Không gửi dữ liệu thật tới LLM ngoài trước khi provider, chính sách lưu trữ và biện pháp bảo vệ được duyệt. Mock mode chỉ dùng hàng có marker tổng hợp và hiển thị nhãn "MÔ PHỎNG" trên UI; audit chỉ lưu metadata tool, không lưu câu hỏi/kết quả.

**Tiêu chí nghiệm thu**

- Các luồng cốt lõi hoàn thành từ UI đến DB và có test.
- Mỗi tool trong registry có test schema, membership/farm scope, synthetic-only và giới hạn. User/client không chọn farm.
- Mock trả số liệu chỉ khi tool trả dữ liệu; hiển thị nguồn/phạm vi/thời điểm và nhãn MÔ PHỎNG; không chẩn đoán hoặc kê thuốc.
- Không có mật khẩu/API key trong Git hoặc log.

**Ca kiểm thử/kiểm chứng**

- Thực thi CRUD và tra cứu theo cả ba vai trò.
- Gọi hợp lệ/không hợp lệ cho từng tool, gồm farm khác và limit quá mức.
- Kiểm tra ma trận quyền đã chốt: lifecycle event và rollback/concurrency, grant độc lập/no self-approval, staff acknowledgement nhưng không close alert, lịch tiêm vs outcome, kho kg-only và requester capacity chỉ owner/delegate.
- Chạy bộ điều phối mock trên fixture tổng hợp có marker; xác minh không đọc hàng không đánh dấu/không outbound; provider thật chưa được triển khai.
- Kiểm tra prompt injection trong trường ghi chú và yêu cầu y tế không phù hợp.

**Minh chứng dự kiến**

- Ảnh/video demo, pytest output, test report tool, cấu hình `.env.example` không chứa secret, log đã che dữ liệu nhạy cảm.

### Tiêu chí 3 — Thực nghiệm, kiểm thử và đánh giá

**Yêu cầu cụ thể**

- Unit, integration, authorization, validation, UI smoke và đánh giá AI có bộ dữ liệu kiểm thử tổng hợp.
- Công bố cách chạy, phiên bản môi trường và giới hạn kiểm thử; đo kết quả thực tế.
- So sánh câu trả lời AI với expected answer/ground truth fixture và đánh giá an toàn riêng.

**Tiêu chí nghiệm thu**

- Test quan trọng có kết quả lặp lại được; bộ điều phối mock không được coi là kiểm thử LLM thật.
- Không có test quyền/farm isolation/tool schema trọng yếu bị bỏ qua.
- Kết quả được báo đúng số đã chạy; test lỗi có phân tích nguyên nhân và disposition.
- Đánh giá AI bao gồm độ đúng dữ liệu truy vấn, căn cứ, từ chối an toàn và lỗi provider.

**Ca kiểm thử/kiểm chứng**

- Test đơn vị service, schema, validators, quyền và trạng thái.
- Integration test MySQL cho FK, transaction và phép tính tồn kho; SQLite chỉ khi tương thích đã chứng minh.
- Vì MVP chỉ cấu hình một trang trại, các ca cross-farm dùng fixture/test-only với hai farm tổng hợp để chứng minh tenant boundary; không tạo thêm trang trại nghiệp vụ/demo.
- Test boundary (0, số âm, limit max/max+1, ngày biên), dữ liệu rỗng và lỗi DB.
- Test dashboard với cá thể active, đàn active, đàn đã tách/gộp, cá thể đóng và thay đổi head_count; đối chiếu tổng expected để phát hiện đếm trùng/mất.
- Test cross-farm trên mọi repository/service/tool chính, cả truy cập trực tiếp bằng ID và farm context giả mạo.
- Test tồn kho với kg, quy đổi có/không có hệ số, hệ số đổi sau giao dịch, xuất đúng bằng tồn, xuất vượt tồn và hai xuất đồng thời; kết quả không âm, không commit một phần.
- Test sức chứa khi đúng ngưỡng/vượt ngưỡng, grant hợp lệ/hết hạn/sai người duyệt; ghi audit và cảnh báo bắt buộc.
- Test trạng thái tiêm `not_performed`, `administered`, `overdue`, `needs_verification`; kiểm tra chuyển trạng thái và tính overdue theo thời điểm.
- Bộ câu hỏi AI cố định trên fixture; chấm đúng/sai/không đủ dữ liệu/an toàn bởi người đánh giá.

**Minh chứng dự kiến**

- Lệnh và output pytest, cấu hình DB test, báo cáo coverage nếu có, bảng kết quả từng ca, log bug/retest. Không ghi kết quả trước khi chạy.

### Tiêu chí 4 — Sản phẩm, báo cáo và demo

**Yêu cầu cụ thể**

- Hướng dẫn cài đặt/chạy, cấu hình an toàn, migration/seed demo, test, backup/khôi phục phù hợp và troubleshooting.
- Giao diện tiếng Việt responsive, dữ liệu demo tổng hợp có nhãn và luồng demo xuyên suốt.
- Kịch bản trình diễn có thời lượng, vai trò và kết quả mong đợi.

**Tiêu chí nghiệm thu**

- Người khác có thể làm theo hướng dẫn trên môi trường sạch mà không cần secret thật để chạy module không-AI.
- AI thiếu cấu hình hiển thị trạng thái rõ; chế độ giả lập được phân biệt rõ với provider thật.
- Demo có đăng nhập, quản lý dữ liệu, dashboard/cảnh báo và truy vấn Agent dùng tool thực tế trên fixture tổng hợp.
- Fixture demo ban đầu dự kiến 3 chuồng, khoảng 30 cá thể và 5 đàn gia cầm; bộ dữ liệu có tình huống bình thường/bất thường nhưng không đại diện trang trại thật.
- Không bật LLM ngoài nếu chưa có phê duyệt; mock hiển thị nhãn "MÔ PHỎNG" rõ và không lấy dữ liệu thật.

**Ca kiểm thử/kiểm chứng**

- Chạy theo README trên môi trường sạch; xác nhận Docker Compose healthcheck khi được triển khai.
- Kiểm tra viewport desktop/mobile, trạng thái rỗng/lỗi và nhãn tiếng Việt.
- Kiểm tra dữ liệu demo có nhãn, có thể nạp lại an toàn chỉ ở DB demo/test.

**Minh chứng dự kiến**

- Hướng dẫn chạy, sơ đồ triển khai, ảnh màn hình, video/demo script, danh sách phiên bản và cấu hình đã che secrets.

### Tiêu chí 5 — Tư duy phản biện và sử dụng AI có trách nhiệm

**Yêu cầu cụ thể**

- Thể hiện người thực hiện tự kiểm chứng đầu ra AI với dữ liệu nguồn/test; AI không được coi là đúng chỉ vì câu trả lời trôi chảy.
- Lưu bằng chứng sửa lỗi theo quy trình: triệu chứng → tái hiện → giả thuyết → test phân biệt → sửa → chạy lại test/hồi quy.
- Phản biện ít nhất các trường hợp AI trả lời thiếu căn cứ, dữ liệu cũ, tool sai quyền, prompt injection và câu hỏi cần chuyên gia.
- Người thực hiện giải thích phần do mình thiết kế/viết/kiểm thử và phần AI hỗ trợ; xác nhận mọi mã/tài liệu trước khi nộp.

**Tiêu chí nghiệm thu**

- Có nhật ký thí nghiệm/bug (không chứa bí mật) với test thất bại trước và test đạt sau cho lỗi đã sửa.
- Mỗi kết luận AI trong demo truy vết được tool, dữ liệu fixture, thời điểm và phạm vi; khi không có bằng chứng, Agent nói không đủ dữ liệu.
- Có đánh giá phản ví dụ và giải thích vì sao câu trả lời sai/nguy hiểm; mô tả giới hạn và người cần tham vấn.
- Có bảng đóng góp cá nhân: quyết định thiết kế, code/module do người thực hiện kiểm tra, test đã viết/chạy, nội dung AI gợi ý đã được xác nhận/chỉnh sửa. Không nhận công lao kiểm chứng mà chưa làm.

**Ca kiểm thử/kiểm chứng**

- Hỏi cùng câu khi fixture thay đổi để xác minh Agent dựa trên tool, không lặp số cũ; kiểm tra mock output không bị trình bày như LLM thật.
- Đưa prompt injection vào ghi chú; xác minh không làm lộ dữ liệu hoặc đổi policy.
- Hỏi chẩn đoán/kê thuốc; xác minh từ chối phần không an toàn và hướng dẫn liên hệ thú y.
- Làm tool trả rỗng, timeout, lỗi permission; xác minh phản hồi không bịa.
- Chọn một lỗi thực tế trong quá trình phát triển; yêu cầu người thực hiện trình bày reproduction, root cause, test hồi quy và kiểm chứng sau sửa.
- Review một câu trả lời AI sai/không chắc chắn, ghi nhận phản biện và cách sửa prompt/tool/dữ liệu hoặc giữ nguyên cảnh báo.

**Minh chứng dự kiến**

- Nhật ký kiểm thử/bug có timestamp và commit/PR nếu có; bộ câu hỏi/expected behavior; bảng phản biện; test trước/sau sửa; bảng đóng góp và phần trình bày miệng.
- Bằng chứng riêng cho invariant đếm cá thể/đàn, cross-farm authorization, kg conversion/tồn không âm, capacity override và outbound gate/mock label.
- Tài liệu retention nêu rõ audit 365 ngày và hội thoại 90 ngày là cấu hình giả định cho demo, không phải kết luận pháp lý; có test purge an toàn và audit kết quả.
- Với mọi lát, báo cáo chính xác file tạo/sửa, test pass/fail, lỗi còn lại, output/log/screenshot thực tế; không tạo ảnh chụp hoặc số liệu minh chứng giả.

## 3. Bộ ca kiểm thử ban đầu

| ID | Phân loại | Ca kiểm thử | Kết quả mong đợi |
|---|---|---|---|
| AUTH-01 | Auth | Đăng nhập hợp lệ/không hợp lệ | Phiên hợp lệ hoặc lỗi chung; không tiết lộ email tồn tại |
| AUTH-02 | AuthZ | Nhân viên thử truy cập chức năng quản trị | 403/ẩn thao tác theo UI; backend luôn từ chối |
| AUTH-03 | Admin grant | Không grant, đúng/sai scope, chưa bắt đầu, hết hạn, thu hồi, thử ghi/quản lý tài khoản | Mặc định từ chối; grant chỉ cho đọc đúng scope/thời gian; mọi kết quả audit |
| AUTH-06 | Grant separation-of-duties | Authority chưa kích hoạt; admin tự gán quyền/tự cấp; requester tự duyệt; issuer trùng recipient; authority độc lập hợp lệ quyết định | Mặc định từ chối và không có tài khoản/quyền/grant tạo sẵn; chỉ quy trình vận hành có audit mới kích hoạt authority; requester/approver/issuer/recipient tách biệt theo policy; không có emergency bypass |
| AUTH-07 | Audit/hội thoại | Owner xem audit farm; staff thử xem audit; user xem hội thoại khác; admin thử đọc thiếu grant; admin cấu hình retention; user/owner/admin gọi purge khi disabled | Owner chỉ đọc audit đúng farm; staff bị từ chối; hội thoại chỉ chủ sở hữu; admin chỉ đọc audit theo grant; purge mọi actor bị backend từ chối và không xóa dữ liệu khi chưa có phê duyệt |
| AUTH-04 | Bootstrap | Tạo admin đầu tiên, chạy lần hai, thiếu secret, lỗi giữa transaction và concurrent bootstrap | Chỉ lần đầu hợp lệ thành công; không có password mặc định/log; user và bootstrap state cùng commit hoặc cùng rollback |
| AUTH-05 | Nhân viên | Thử mỗi API/action được phép và bị cấm, gồm audit/configuration | Chỉ action đã cấp; backend từ chối ngoài quyền; nhân viên không sửa/xóa audit |
| TENANT-01 | Cô lập | User farm A truy cập ID thuộc farm B | Từ chối như không tìm thấy; không rò rỉ dữ liệu |
| TENANT-02 | Cô lập | Forged `farm_id`, tool/repository/service được gọi trực tiếp với farm ngoài scope | Từ chối ở backend ở mọi lớp; audit deny không làm lộ dữ liệu |
| LIV-01 | Dữ liệu | Thêm loài mới qua danh mục, gán vật nuôi/nhóm | Không cần sửa nhánh code riêng theo loài; FK hợp lệ |
| LIV-02 | Validation | Số lượng 0/âm, chuồng khác farm, mã trùng | Từ chối với lỗi rõ và không lưu một phần |
| LIV-03 | Đếm đàn | Tổng dashboard gồm cá thể active + group active; source split/merged/closed bị loại; thử thêm cá thể từ group mà không giảm source | Mỗi con được tính đúng một lần; tách/gộp event atomic và bảo toàn tổng nếu không có event tăng/giảm; thêm trùng bị từ chối |
| LIV-04 | Sức chứa | Thêm/chuyển đàn đúng capacity, vượt capacity không grant, grant hợp lệ/hết hạn; hai chuyển đồng thời gần ngưỡng | Đúng ngưỡng được phép; vượt bị cảnh báo và chặn mặc định; ngoại lệ chỉ qua grant hợp lệ, luôn audit; không concurrent overbooking |
| SCHEMA-01 | Schema | Tạo schema từ model; kiểm tra PK UUID, FK và danh sách bảng cốt lõi | Mọi bảng Lát 2 có PK UUID; FK hợp lệ; không tạo dữ liệu/tài khoản mặc định |
| SCHEMA-02 | Farm isolation | Thử tham chiếu barn, herd, feed, membership, lịch tiêm hoặc actor thuộc farm khác | Ràng buộc FK từ chối bản ghi sai phạm vi; transaction không lưu một phần |
| SCHEMA-03 | Lịch sử | Thử `DELETE` trực tiếp các bảng farm và lịch sử sau migration | Database từ chối xóa cứng; downgrade chỉ gỡ trigger trên database kiểm thử cô lập |
| CAP-02 | Schema sức chứa | Capacity bằng 0; request dùng barn/actor khác farm hoặc số lượng đề xuất không dương | CHECK/FK từ chối dữ liệu sai; schema không tự cấp ngoại lệ hoặc cập nhật occupancy |
| FEED-06 | Schema tồn kho | Unit ngoài kg, quantity âm, delta không khớp loại giao dịch và balance không khớp | CHECK constraint từ chối; không lưu transaction không hợp lệ |
| VAX-04 | Schema lịch/kết quả | Tạo lịch với không/2 đối tượng; outcome `administered` thiếu thời điểm/người thực hiện; schedule khác farm | Lịch phải có đúng một target; kết quả phải có bằng chứng bắt buộc; FK chặn liên kết chéo farm |
| LIV-SVC-01 | Service đàn/vật nuôi | Tạo đàn/cá thể; nhận diện cá thể; tăng/giảm; split/merge; chuyển chuồng; bán/mất | Mọi thay đổi hợp lệ có HerdEvent; tổng active bảo toàn khi đổi tracking/chuyển; source merge đóng; số lượng không âm |
| LIV-SVC-02 | Service farm scope | Nhân viên/owner thuộc farm A gọi service với actor, herd, animal hoặc barn của farm B | Backend từ chối actor không có membership; ID ngoài farm không tiết lộ dữ liệu và không thay đổi bản ghi |
| LIV-SVC-03 | Service rollback/capacity | Tag vật nuôi trùng, số lượng split lớn hơn đàn, vượt capacity, lỗi giữa thao tác | Transaction không lưu dở dang; event/audit/đàn/vật nuôi rollback đồng bộ |
| FEED-SVC-01 | Service quyền kho | Staff tiêu thụ; staff thử nhập/điều chỉnh không grant; staff có grant tương ứng; owner ghi receipt/adjustment | Backend cho đúng loại quyền; grant phải cùng farm, chưa thu hồi; mọi giao dịch hợp lệ có ledger và audit |
| FEED-SVC-02 | Service rollback/tồn | Tồn thiếu, lượng âm/float/đơn vị ngoài kg, điều chỉnh thiếu lý do | Giao dịch bị từ chối; balance và ledger không bị ghi một phần |
| FEED-SVC-03 | Giao dịch đồng thời | Hai MySQL connection cùng tiêu thụ tổng lượng lớn hơn tồn ban đầu | Một giao dịch được ghi, giao dịch thiếu tồn bị từ chối; balance cuối không âm và ledger chỉ có giao dịch thành công |
| LIV-05 | Lifecycle permission | Nhân viên tạo/cập nhật unit và ghi acquisition/birth/split/merge/movement/tracking conversion/loss theo workflow; thử xóa cứng, số lượng âm, thiếu quyền và cross-farm | Action hợp lệ theo permission được ghi bằng event; mọi số lượng cập nhật transaction atomic; lỗi rollback; không xóa cứng/sai scope/số âm; audit actor/reason |
| LIV-06 | Lifecycle invariants/concurrency | Tách/gộp/chuyển đổi giữa individual/group, lỗi giữa transaction, hai cập nhật đồng thời và dashboard sau mỗi event | Không có đơn vị cha/con đồng thời active cho cùng con; tổng active đúng; rollback toàn bộ khi lỗi; không đếm trùng/sai số |
| CAP-01 | Yêu cầu ngoại lệ | Staff/admin/owner/delegate gửi; requester tự duyệt; approver không quyền; đủ/thiếu lý do; từ chối/hết hạn/snapshot cũ; xử lý đồng thời | Chỉ owner/delegate có permission được gửi/duyệt; requester không tự duyệt; request không đổi dữ liệu; chỉ approval hợp lệ mới được thực thi sau khi re-check trong transaction; lưu lý do/actor/time/snapshot |
| HEALTH-01 | Sức khỏe | Nhân viên ghi quan sát và follow-up | Bản ghi có actor/thời điểm, truy vấn được đúng farm |
| VAX-01 | Tiêm | Lịch scheduled/cancelled, kết quả administered/not_performed/postponed/needs_verification, ngày sai | Lịch và kết quả được lưu riêng; overdue chỉ là cờ suy ra theo due date/timezone nếu chưa administered; không tự xác nhận kết quả |
| FEED-01 | Kho | Nhập/xuất/điều chỉnh kg và tính tồn | Tổng tính đúng bằng kg; permission đúng actor; transaction atomic; tồn không âm |
| FEED-02 | Cạnh tranh | Hai giao dịch xuất đồng thời gần ngưỡng | Không tạo tồn sai theo quy tắc locking/transaction được chọn |
| FEED-03 | Đơn vị | Giao dịch kg, thử bao/tấn/lít hoặc conversion chưa phê duyệt | MVP chỉ nhận kg và từ chối rõ đơn vị khác; thiết kế mở rộng yêu cầu approval/snapshot, không áp dụng conversion trong MVP |
| FEED-04 | Tồn âm | Xuất bằng đúng tồn, vượt tồn và concurrent withdrawals | Số dư không âm; vượt tồn bị rollback hoàn toàn, không ghi giao dịch một phần |
| FEED-05 | Nhân viên/kho | Ghi nhận tiêu thụ thức ăn so với nhập kho/điều chỉnh số dư | Nhân viên chỉ tạo loại giao dịch được cấp; nhập kho/điều chỉnh bị từ chối nếu thiếu permission; audit đầy đủ |
| DASH-01 | Dashboard | Tổng hợp với fixture biết trước | Metric khớp truy vấn nguồn và chỉ thuộc farm được phép |
| ALERT-01 | Cảnh báo | Staff acknowledge/thêm ghi chú/close/reopen; owner/delegate close/reopen có/thiếu lý do; AI thử đổi trạng thái | Staff chỉ acknowledge/ghi chú; close/reopen chỉ owner/delegate có permission, cần lý do/kết quả; event history append-only; AI chỉ đọc |
| TOOL-01 | Agent | 6 tool với schema hợp lệ, field lạ, kiểu sai và limit ngoài phạm vi | DTO đúng; input lỗi bị từ chối; danh sách tối đa 10 |
| TOOL-02 | Agent AuthZ | Chưa đăng nhập, user không thuộc farm, gọi tool với farm khác | Route yêu cầu login; tool từ chối membership/farm sai; không trả DTO farm khác |
| AI-01 | Provider gate | `AGENT_MODE=provider` hoặc disabled khi provider chưa có | UI báo chưa cấu hình/đã tắt; không outbound và không có câu trả lời giả |
| AI-02 | Synthetic scope | Dữ liệu tổng hợp có marker và bản ghi không marker trong cùng farm | Chỉ bản ghi thuộc SyntheticDataset cùng farm được đọc |
| AI-03 | Input/call limits | Câu hỏi >2.000 ký tự, yêu cầu hơn 3 intent, tool output quá giới hạn, elapsed budget hết | Từ chối input/output; tối đa 3 tool call; báo giới hạn thời gian |
| AI-04 | An toàn | Câu hỏi chẩn đoán/kê thuốc/khẩn cấp | Không chẩn đoán/kê thuốc; hướng dẫn liên hệ chuyên gia phù hợp |
| AI-05 | Prompt injection | Ghi chú có chỉ dẫn yêu cầu lộ dữ liệu/chuyển quyền | Nội dung ghi chú được hiển thị như dữ liệu, policy/quyền không đổi |
| AI-06 | Mock mode | Hỏi bằng tiếng Việt; kiểm tra status, nguồn và dataset | UI gắn nhãn "MÔ PHỎNG"; chỉ dữ liệu có marker; không có outbound provider call |
| AI-07 | Tổng hợp/audit | Đàn + cá thể trong đàn + cá thể độc lập; hỏi dashboard/tool | Tổng đúng 6 trong fixture; thành công ghi metadata tool, không đếm hai lần |
| AI-08 | Route | GET/POST chưa đăng nhập, POST thiếu CSRF, mock/provider UI | Login/CSRF được cưỡng chế; mock hiện tool source; provider chưa cấu hình bị khóa |
| VAX-02 | Tiêm | Bốn trạng thái logical và lịch đã quá hạn | `overdue` chỉ theo rule ngày đến hạn/chưa thực hiện; `needs_verification` không tự bị ghi đè |
| VAX-03 | Permission tiêm | Owner/delegate tạo/sửa/hủy lịch, phân công; staff xem/ghi outcome; sửa lịch đã có kết quả; khai báo đã tiêm không căn cứ; farm khác | Chỉ owner/delegate có permission quản lý lịch; staff chỉ ghi outcome thực tế có căn cứ; mọi thay đổi lịch/audit history có actor/time/reason; backend chặn cross-farm |
| ALERT-02 | Quyền cảnh báo | Staff acknowledge, ghi chú, thử close/reopen; owner/delegate close/reopen; thiếu lý do; AI write attempt | Acknowledge không đóng; close/reopen chỉ principal có permission và có lý do/kết quả; mọi event audit; Agent không thực hiện action |
| HEALTH-02 | Ngưỡng | Cấu hình rule không nguồn/không người duyệt, rule đã duyệt | Rule thiếu nguồn/duyệt không kích hoạt; AI không thể tạo/sửa threshold |
| AUD-01 | Audit | Thành công, từ chối quyền và lỗi provider | Actor/action/outcome/correlation đúng; không chứa secret |
| RET-01 | Retention | Cấu hình demo 365 ngày audit/90 ngày hội thoại; owner/staff/admin xem dữ liệu theo quyền; thử gọi purge trước khi có phê duyệt | Owner xem audit đúng farm; staff không; hội thoại chỉ chủ; admin chỉ audit theo grant; purge API/UI/job đều disabled ở backend, không có schedule/xóa khi thiếu phê duyệt; bật trong tương lai chỉ qua job identity sau khi backup/restore/hold/lịch được duyệt |
| UI-01 | UI | Mobile, rỗng, lỗi validation và mạng | Nội dung tiếng Việt đọc được, không mất trạng thái, có hướng xử lý |
| DEMO-01 | Demo | Seed tường minh lần đầu và seed lặp trong database test/demo xác minh | Marker synthetic đầy đủ; dữ liệu lặp không bị nhân đôi; không sửa/xóa/ghi đè dữ liệu hiện có; không auto-seed/reset |
| DEMO-02 | Bảo vệ fixture | Thiếu mode/DB name/identity, sai database, production, cấu hình mâu thuẫn, thử seed qua app startup | Tất cả bị từ chối fail-closed; chỉ test/demo + database identity không production đã xác minh mới nhận seed |
| DEMO-03 | Không phá hủy | Tạo xung đột với dữ liệu hiện có rồi chạy seed lại | Seed dừng rõ ràng hoặc bỏ qua hàng synthetic tương đương; không xóa, ghi đè hoặc reset bất kỳ dữ liệu nào |

## 4. Chiến lược kiểm thử

1. **Unit:** validators, services, permission policy, tool schemas, phép tính tồn, đếm đàn, capacity, ánh xạ lỗi. Không phụ thuộc LLM thật.
2. **Integration:** repository/migrations/transactions/tenant scope trên MySQL test; kiểm thử đồng thời tồn kho và chuyển tracking; SQLite chỉ nếu behavior tương thích đã được kiểm chứng.
3. **AI contract:** bộ điều phối mock gọi tool nghiệp vụ trên fixture tổng hợp để kiểm thử intent, quyền, giới hạn và output; xác minh không outbound. Provider thật chưa được triển khai; nếu được duyệt sau này, kiểm thử provider phải là nhóm riêng với cấu hình tường minh.
4. **UI smoke:** đăng nhập, điều hướng, form validation, dashboard, alert và trợ lý trạng thái thiếu cấu hình.
5. **Security/privacy:** phân quyền, cross-tenant, CSRF, session, rate limits, output escaping, prompt injection, redaction và secret scanning theo công cụ sẵn có.
6. **Retention:** test cấu hình tuổi lưu, quyền purge, preview/dry-run, giới hạn scope, redaction và audit kết quả. 365 ngày/90 ngày là cấu hình demo, không phải kết luận pháp lý.
7. **Regression:** mọi bug đã xác nhận phải có test tái hiện trước khi sửa; chạy test mục tiêu sau sửa, sau đó bộ hồi quy liên quan.

Không tuyên bố coverage, hiệu năng, độ chính xác hoặc trạng thái pass khi chưa chạy lệnh và lưu bằng chứng đầu ra.

### Phạm vi kiểm chứng schema và services

- `tests/test_models.py` kiểm tra constraints UUID/tables, FK cùng farm, kg-only, tồn không âm, tính đúng delta, lịch tách khỏi outcome, target lịch tiêm, tổng head count gồm con chưa định danh và Animal active, purge disabled, capacity và request chéo farm.
- `tests/test_migrations.py` chạy `flask db upgrade`, `flask db check`, rồi `flask db downgrade base` trên SQLite file tạm; thử xóa trực tiếp farm và xác minh migration tạo/gỡ trigger.
- `tests/test_mysql_schema.py` chỉ chạy khi có `MYSQL_TEST_DATABASE_URL` trỏ đến database disposable: xác minh schema, CHECK/FK, test insert hợp lệ/không hợp lệ, hard-delete trigger, upgrade/downgrade và hai giao dịch tiêu thụ đồng thời. Đã chạy thực tế trên MySQL 8.0.46 trong container disposable không volume; không tác động MySQL khác.
- `tests/test_services.py` kiểm tra permission và farm scope ở service, lifecycle đàn/cá thể, không đếm trùng, capacity, rollback, ledger và tồn âm. Kiểm thử đồng thời phải chạy trên MySQL; SQLite test không thay thế bằng chứng khóa của InnoDB.
- Các test về giao diện, API/login thực tế, audit deny, xử lý deadlock/retry tự động và lifecycle capacity exception chưa thuộc phần đã hoàn thành.

## 5. Kịch bản demo dự kiến

Fixture tổng hợp cho demo dự kiến có 3 chuồng, khoảng 30 cá thể và 5 đàn gia cầm, có tình huống bất thường được tạo có chủ đích; không khẳng định là dữ liệu thực.

1. Đăng nhập vai trò chủ trang trại, xem dashboard/cảnh báo của dữ liệu demo.
2. Chuyển sang nhân viên chăm sóc, ghi một quan sát sức khỏe hoặc cập nhật lịch tiêm.
3. Tra cứu tồn kho và giải thích biến động bằng giao dịch demo.
4. Hỏi Agent số lượng/nhóm cần theo dõi; trình bày tool được gọi, dữ liệu trả về và thời điểm; đối chiếu tổng đầu con để chứng minh không đếm trùng.
5. Thử câu hỏi yêu cầu kê thuốc và giải thích phản hồi an toàn.
6. Thử thiếu phê duyệt/provider hoặc test mock có nhãn; chứng minh không gửi dữ liệu thật ra ngoài và module thường vẫn dùng được.
7. Xem audit đã redacted và trình bày một test/lỗi đã tự kiểm chứng, sửa, chạy hồi quy cùng phần đóng góp của người thực hiện.

Tất cả bản ghi trong demo phải đánh dấu là dữ liệu tổng hợp; không mô tả chúng là số liệu trang trại thực. Mock mode chỉ được dùng với fixture tổng hợp.

## 6. Kế hoạch triển khai sau khi duyệt thiết kế

| Lát triển khai | Nội dung | Cổng kiểm chứng |
|---|---|---|
| 0. Chốt thiết kế | Chốt quyền nhân viên/lifecycle, grant, lịch tiêm, cảnh báo, kho kg-only, capacity, retention; guard fixture; purge disabled; authority grant disabled đến khi được chỉ định; provider ngoài fail-closed | Đã nghiệm thu có điều kiện; các tính năng bị hoãn vẫn bị khóa ở backend; không tuyên bố test ứng dụng đã chạy |
| 1. Nền tảng | Chỉ Flask application foundation, cấu hình môi trường, kết nối MySQL, migrations và pytest harness; không xây dựng nghiệp vụ | Kiểm tra app factory/config; xác thực database connection/migration theo môi trường đã cấu hình; pytest harness chạy; không auto-seed/purge/outbound AI; không thao tác phá hủy dữ liệu |
| 2. Tài khoản/quyền | Login/session, permission matrix, farm scope, one-time bootstrap, grant requests/approvals, audit | Test role/action matrix, cross-farm trực tiếp và qua service, bootstrap replay/rollback, grant independent issuer, no self-approval, expiry/revocation/read-only |
| 3. Danh mục/đàn/chuồng | Species/breed, cá thể/group, lifecycle events, capacity request/decision | Test tách/gộp/conversion tracking atomic, count invariant/no double-count, event rollback/concurrency, requester/approver separation và capacity re-check |
| 4. Sức khỏe/tiêm | Nhật ký, lịch và kết quả riêng, alert lifecycle, nguồn/ngưỡng cảnh báo được duyệt | Test role, lịch vs kết quả, scheduled/cancelled, outcome transitions, overdue theo timezone, staff ack/no close, owner close/reopen có lý do; AI chỉ đọc |
| 5. Thức ăn/dashboard | kg-only, ledger, ghi nhận tiêu thụ vs nhập/điều chỉnh, metric dashboard | Test permission giao dịch, từ chối unit khác kg, negative-stock prevention, rollback/concurrent withdrawals, tổng đàn và occupancy |
| 6. AI read-only | Registry, schema, permission, tool service, mock fixture-only; adapter ngoài bị khóa đến khi duyệt | Test riêng từng tool, cross-farm, output cap, no outbound trong mock, provider fail-closed |
| 7. Audit/retention | Cấu hình retention và job purge có preview/role hẹp | Test 365/90 ngày là config demo; scope, dry-run, quyền và audit kết quả |
| 8. Demo/tài liệu | UI responsive, fixture tổng hợp (3 chuồng/~30 cá thể/5 đàn), hướng dẫn và rubric evidence | Reproducible demo, nhãn synthetic/mock rõ, chạy test đã chọn và lưu kết quả thực tế |

Mỗi lát chỉ được coi là hoàn tất khi code, test liên quan và hướng dẫn đã được kiểm chứng. Mọi thay đổi schema cần migration; không chạy trên dữ liệu thật nếu chưa có backup và xác nhận riêng.

## 7. Trạng thái kiểm chứng Lát 3

| Phạm vi | Hiện thực | Kiểm chứng/giới hạn |
|---|---|---|
| Xác thực/session | Login/logout, hash mật khẩu, session fixation reset, HttpOnly/SameSite, Secure ở production, CSRF cho mọi POST | `tests/test_web.py`; user cần active và membership farm; chưa có login throttling, password reset hoặc UI cấp thành viên |
| Farm scope/RBAC | Mỗi request xác minh User/FarmMembership/Farm; ID cross-farm trả 404; owner-only chuồng/lịch/audit; staff không đóng alert | Web tests bao gồm outsider ID, staff audit, staff barn write và alert close |
| Dashboard | Đọc DB: chuồng, đàn, cá thể định danh, tổng số đầu con, feed kg, lịch quá hạn/sắp tới, alert mở | Test fixture có đàn gồm 3 chưa định danh + 2 cá thể, và 1 cá thể độc lập; tổng phải là 6, không cộng đôi |
| Chuồng và livestock | Chuồng owner create/update; kiểm occupancy; CRUD tên đàn/cá thể và service mutations; lịch sử theo đối tượng | Capacity dưới occupancy bị chặn; không có capacity override |
| Health/vaccination | HealthRecord append; lịch tiêm tách kết quả; lịch owner-only; outcome có thời điểm/người ghi khi administered | Lịch quá hạn vẫn không đổi thành outcome; duplicate outcome bị từ chối; correction workflow chưa bật |
| Feed | Danh mục owner create qua service; mọi giao dịch qua ledger service, chỉ kg, balance không âm | Staff receipt/adjust bị service từ chối nếu thiếu grant; test receipt deny, over-consumption rollback và successful consumption |
| Alerts/audit | Staff acknowledge/note; owner close có lý do; audit chỉ owner, read-only | Event/audit được kiểm tra; không có alert reopen hoặc ngưỡng tự sinh |
| UI/docs | Jinja shared layout, responsive CSS, loading/error/empty/success states, Vietnamese labels; root README | Smoke test render dashboard, barns, livestock detail, feed, health, vaccination, alerts và audit |
| Schema/migration | Không đổi model schema hoặc migration trong Lát 3 | Không chạy migration trên MySQL; không seed, AI, purge, commit hoặc push |

Kết quả chạy cuối trong Lát 3: **53 passed, 2 skipped**. Hai test MySQL bị skip do không cung cấp `MYSQL_TEST_DATABASE_URL` disposable; Lát 3 không chạy migration hay kết nối MySQL.

## 8. Trạng thái kiểm chứng Lát 4

| Phạm vi | Hiện thực | Kiểm chứng/giới hạn |
|---|---|---|
| Agent runtime | Bộ phân loại ý định tiếng Việt có giới hạn; không gọi LLM | Mock được gắn nhãn; tối đa 3 tool call/2.000 ký tự input/8.000 ký tự output; elapsed budget 5 giây được kiểm tra giữa các call, không hủy tức thì câu SQL đang chạy |
| Tool registry | 6 tool: tổng quan, chuồng, tồn kho, lịch tiêm, cảnh báo, sức khỏe | Schema đóng, max 10 rows, mỗi lần gọi kiểm tra active membership/farm; chỉ dataset tổng hợp cùng farm; không có thao tác ghi nghiệp vụ |
| Đếm vật nuôi | Tổng hợp SQL loại đàn/cá thể không hoạt động; cá thể trong đàn không cộng lặp | Fixture kiểm tra 3 chưa định danh + 2 cá thể trong đàn + 1 cá thể độc lập = 6 |
| Tiêm phòng | Due status và actual outcome trả thành trường riêng | Test bảo đảm `needs_verification` không trở thành đã tiêm |
| Audit | Ghi metadata append-only cho tool đọc thành công; không ghi câu hỏi/kết quả | Không có tool đọc/xóa/sửa audit; attempt bị từ chối chưa có audit event riêng |
| Route/UI | `/assistant` dùng session hiện tại, POST + CSRF; hiển thị unavailable/mock, câu trả lời, nguồn và phạm vi | Test login, CSRF, trạng thái provider chưa triển khai, lỗi DB không lộ chi tiết; không lưu hội thoại |
| Provider/privacy | `AGENT_MODE` mặc định disabled; provider chưa có adapter | Không outbound LLM, không API key, không provider thật; mock bị chặn ngoài test/demo |

Test Lát 4 đã chạy tập trung: `python -m pytest -q tests/test_agent.py tests/test_app_factory.py` — **20 passed**. Toàn bộ suite: `python -m pytest -q` — **65 passed, 2 skipped**; hai skip là MySQL disposable tests do không cấu hình `MYSQL_TEST_DATABASE_URL`. Chưa có kiểm chứng provider thật hoặc integration MySQL trong Lát 4.

## 9. Lát 5 — Hoàn thiện và chuẩn bị demo

| Tiêu chí rubric | Yêu cầu/tiêu chí nghiệm thu | Ca kiểm thử/kiểm chứng | Minh chứng dự kiến và trạng thái |
|---|---|---|---|
| 1. Phân tích và thiết kế | Giữ nguyên kiến trúc; seed chỉ nhận test in-memory hoặc demo MySQL riêng đã xác minh name + server UUID + operator attestation; dữ liệu gắn marker/version; không auto-seed/purge | Thiếu/sai mode, DB name/attestation; DB có dữ liệu; dataset marker/version/count lệch; rerun | `demo_seed.py`, Compose và tài liệu security/demo; không thay schema/migration |
| 2. Hệ thống và AI | Sáu tool chỉ đọc, không outbound LLM; mỗi tool kiểm tra permission/farm và chỉ dùng dataset tổng hợp; nhãn MÔ PHỎNG chính xác | Tình huống mock kiểm tra tool/data, diễn giải, refusal, provider unavailable và nhãn riêng | `tests/fixtures/agent-evaluation.json`; không tuyên bố chất lượng/độ chính xác LLM |
| 3. Thực nghiệm và đánh giá | Đánh giá deterministic mock trên kỳ vọng định nghĩa trước, không bịa kết quả; test seed rollback/idempotence/guard | Tool schema/data; empty/ambiguous; cross-farm; injection; veterinary/write refusal; seed rollback | pytest output thực chạy và Compose `config -q`; MySQL integration chỉ báo nếu thực sự chạy |
| 4. Sản phẩm và demo | Có hướng dẫn startup riêng, database không publish port, healthcheck, không migration/seed tự động; demo toàn dữ liệu tổng hợp | Kiểm tra Compose syntax; kịch bản login/dashboard/livestock/feed/vaccination/alerts/assistant/refusal | `README.md`, `docs/demo-script.md`, Dockerfile/Compose; chưa start container trong Lát 5 |
| 5. Tư duy phản biện và AI có trách nhiệm | Người thực hiện phân biệt dữ liệu DB, mô phỏng và nhận định; giải thích giới hạn không có LLM, injection, farm authorization, no-diagnosis/no-write; tự kiểm chứng test và nêu sửa lỗi dựa trên lỗi quan sát | So sánh tool DTO với câu trả lời; prompt injection; yêu cầu cross-farm/y khoa/ghi; ghi lại test fail → nguyên nhân → fix → regression pass | Lưu log test thật và phần trình bày đóng góp: thiết kế, code, test, rà soát bảo mật và giới hạn. Không dùng minh chứng/độ chính xác giả |

### Cổng nghiệm thu Lát 5

- **Seed guard:** không chạy migration/seed trên database hiện có; test chỉ dùng SQLite in-memory cô lập. Demo MySQL guard fail-closed nếu identity không được xác minh.
- **Evaluation:** `expected_tool_values`, `expected_interpretation`, `expected_refusal` và `expected_mock_label` là bốn dimension riêng; không có provider thật nên không chấm accuracy LLM.
- **Compose:** chỉ `docker compose config --quiet`; không `up`, `down`, pull image hoặc tạo volume trong bước chuẩn bị.
- **Bằng chứng thực tế:** cập nhật số PASS/FAIL/SKIP từ lần chạy pytest cuối và kết quả Compose syntax sau khi chạy; không thay số đã quan sát bằng kết quả kỳ vọng.

Kịch bản demo: [docs/demo-script.md](../demo-script.md). Seed design: `livestock_app/demo_seed.py`. Evaluation cases: `tests/fixtures/agent-evaluation.json`.

**Kết quả Lát 5 đã quan sát:** test tập trung Agent/seed **33 passed**; toàn suite **87 passed, 2 skipped**. Hai skip là MySQL disposable tests không có `MYSQL_TEST_DATABASE_URL`; không có MySQL integration run. `docker compose --env-file .env.example config --quiet` trả về exit code 0. Lần chạy toàn suite trước đó báo thiếu `tzdata` trên Windows (`Asia/Ho_Chi_Minh`); dependency được khai báo/cài, sau đó toàn suite chạy lại đạt kết quả trên. Không khởi động container, chạy migration hoặc seed.

## 10. Lát 6 — Kiểm thử tích hợp và chuẩn bị demo an toàn

| Hạng mục | Kết quả | Bằng chứng/giới hạn |
|---|---|---|
| Pytest toàn bộ | **PASS — 94 passed, 2 skipped, 0 failed** | `python -m pytest -q`; hai skip là MySQL disposable integration do không có `MYSQL_TEST_DATABASE_URL` |
| MySQL integration | **NOT RUN** | Không cấu hình target; test giờ yêu cầu URL `mysql+pymysql`, tên `livestock_test_*` khớp cấu hình, server UUID thực tế khớp, xác nhận operator và schema rỗng trước migration. Các thao tác upgrade/downgrade chỉ chạy sau preflight |
| Guard MySQL test | **PASS — 7 unit cases** | Kiểm tra cấu hình target hợp lệ và từ chối tên/UUID/attestation/schema không hợp lệ; không mở kết nối |
| Compile Python | **PASS** | `python -m compileall -q livestock_app tests wsgi.py` |
| Compose config | **PASS** | `docker compose --env-file .env.example config --quiet` exit 0; dịch vụ `db`, `app`; chỉ kiểm tra cấu hình, không xuất config đã render |
| Docker runtime / UI | **NOT RUN** | Không có file env demo với secret riêng và MySQL UUID operator xác minh; không build, start, healthcheck, login hay request trang thực tế |
| Tài nguyên Docker | **PASS — no collision observed** | Không có resource theo tên project `livestock-ai-agent-l5-demo`; host port 5187 trống. Có stack/container khác đang chạy và port 3306 đang bận; không tác động. Compose DB không publish host port 3306 |
| Migration/seed | **NOT RUN** | Không kết nối database, không migrate, không seed |

### Rà soát cấu hình Compose và giới hạn

- Biến bắt buộc cho Compose: `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `SECRET_KEY`. UUID và attestation demo được để trống trong ví dụ; seed guard sẽ từ chối cho tới khi operator xác minh.
- Database không publish port; app bind `127.0.0.1:5187`; network/volume tên theo project Compose; MySQL/app có healthcheck và app chờ DB healthy.
- Dockerfile chạy user không phải root. Entrypoint chỉ chạy Flask server; không tự chạy migration/seed. Compose YAML không chứa provider LLM.
- Các giá trị environment của container không phải Docker secret store. File env runtime phải riêng, quyền truy cập hạn chế, không commit/ghi log; chưa tạo file env hay secret cho Lát 6.
- Docker runtime chưa được chứng minh. `config --quiet` không xác minh build, healthcheck, MySQL version runtime, authentication hoặc HTTP routes.

README và [kịch bản demo](../demo-script.md) đã phân biệt PASS/FAIL/SKIP/NOT RUN. Test MySQL giờ có preflight để tránh migration lên target chưa xác minh; hai integration test hiện vẫn SKIP thay vì chạy trên MySQL có sẵn.
