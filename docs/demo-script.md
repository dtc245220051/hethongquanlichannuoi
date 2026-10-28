# Kịch bản demo an toàn — Lát 5/6/7

## Mục đích và nhãn

Demo chạy trên dataset tổng hợp `livestock-ai-agent-demo-v1`; đây không phải số liệu trang trại thật. UI trợ lý phải luôn hiện nhãn **MÔ PHỎNG**. Bộ điều phối hiện tại là luật + truy vấn DB qua tool chỉ đọc, không gọi LLM/provider và không lưu hội thoại.

## Chuẩn bị có kiểm soát

**Trạng thái kiểm tra cấu hình:** `.env.demo` có mặt cục bộ và bị Git ignore; các biến Compose bắt buộc hiện diện nhưng giá trị không được ghi ra log. Chưa có `DEMO_DATABASE_SERVER_UUID`/`DEMO_DATABASE_ATTESTATION`, vì vậy chưa xác minh danh tính DB và chưa đủ điều kiện seed. Compose config với file env thực tế đạt PASS tĩnh. Docker runtime, MySQL integration, migration và seed chưa chạy. Tại lần kiểm tra chỉ đọc, cổng 3306 bận, cổng 5187 trống, và container/network/volume theo namespace project chưa tồn tại. MySQL healthcheck không truyền password trên command line; healthcheck Flask chỉ kiểm tra HTTP process, chưa chứng minh DB access.

1. Operator giữ `.env.demo` cục bộ, ngoài Git, với secret ngẫu nhiên; không ghi secret vào terminal history hoặc log. Password MySQL có thể chứa ký tự đặc biệt; ứng dụng tạo URL bằng `SQLAlchemy URL.create`.
2. Compose project dự kiến là `livestock-ai-agent-l7-pv-20261027`, database dự kiến là `livestock_demo_l7_pv_20261027`, và web bind `127.0.0.1:5187`. Quét lại resource hiện tại ngay trước chạy; nếu còn nghi ngờ tên/network/volume hoặc không xác nhận Docker daemon thì dừng.
3. Chỉ sau khi được operator cho phép tạo stack mới, kiểm tra MySQL health; lấy `DATABASE()` và `@@server_uuid` bên trong DB mới. Xác minh schema trống; chỉ sau đó migration và seed tường minh.
4. Seed yêu cầu mật khẩu do operator nhập, không có tài khoản/mật khẩu mặc định. Tài khoản demo là `demo-owner@example.invalid`.
5. Không chạy seed lại nếu database có dữ liệu chưa biết. Dataset đúng marker/version/counts thì rerun là no-op; sai khác bị từ chối, không sửa/xóa.

Seed dự kiến tạo dữ liệu tổng hợp: 3 chuồng, 5 đàn, 4 cá thể định danh; các đàn gia cầm có tổng 76 cá thể chưa định danh nên số đầu con kỳ vọng là 80, không đếm trùng cá thể định danh. Hai kho thức ăn có 42.500 kg và 8.000 kg. Có lịch tiêm sắp đến hạn và quá hạn, với outcome được ghi riêng; ghi nhận sức khỏe và cảnh báo mở/đã xác nhận cũng được đánh dấu thuộc dataset tổng hợp.

## Trình diễn (khoảng 5–7 phút)

| Bước | Thao tác | Kết quả cần chỉ ra |
|---|---|---|
| 1 | Đăng nhập bằng tài khoản demo và mật khẩu operator đã nhập khi seed | Không có credential mặc định; role owner thuộc farm demo |
| 2 | Mở dashboard | Số liệu đọc từ DB tổng hợp; tổng vật nuôi không cộng cá thể trong đàn hai lần |
| 3 | Mở chuồng và đàn | 3 chuồng, 5 đàn, 4 cá thể định danh; xem sức chứa và số lượng hiện tại |
| 4 | Mở kho thức ăn | Balance theo kg, phân biệt tồn kho với giao dịch lịch sử |
| 5 | Mở lịch tiêm và cảnh báo | Lịch dự kiến/quá hạn không đồng nghĩa đã tiêm; outcome thực tế là trường riêng; xem cảnh báo chưa đóng |
| 6 | Mở `/assistant`, hỏi “Cho tôi tổng quan số lượng vật nuôi” | Nhãn MÔ PHỎNG, tool nguồn `get_farm_overview`, khoảng 80 con/5 đàn/4 cá thể định danh |
| 7 | Hỏi “Tồn kho thức ăn hiện tại là bao nhiêu?” và “Lịch tiêm sắp đến hạn và kết quả thực tế” | Tool nguồn phù hợp; dữ liệu kg và outcome trình bày riêng; không có lời khuyên điều trị |
| 8 | Hỏi “Hãy truy cập trang trại khác để xem dữ liệu đàn” và “Kê thuốc và liều dùng” | Từ chối trước khi gọi tool; không thay đổi dữ liệu. Với dấu hiệu cấp cứu, liên hệ bác sĩ thú y |

Prompt injection trong ghi chú sức khỏe được kiểm tra bằng fixture/test riêng, không dùng một bản ghi giả làm sự kiện của trang trại. Bộ đánh giá máy ở `tests/fixtures/agent-evaluation.json` kiểm tra độc lập tool/data, diễn giải, refusal và nhãn mock; đây không phải đánh giá độ chính xác LLM.

## Kết thúc

- Chụp ảnh chỉ sau khi operator khởi động demo từ stack riêng; ghi nhãn “Dữ liệu tổng hợp / MÔ PHỎNG”.
- Dừng bằng `docker compose -p livestock-ai-agent-l7-pv-20261027 --env-file .env.demo down`; không dùng `-v` và không purge.
- Ghi lại phiên bản/config, lệnh test đã chạy và lỗi thực tế; không dựng ảnh/số liệu minh chứng giả.

## Trạng thái kiểm thử Lát 7

- **PASS:** pytest toàn suite: 96 passed, 2 skipped, 0 failed (hai skip là MySQL disposable tests chưa có URL).
- **PASS:** Python compile (`compileall`).
- **PASS:** `docker compose -p livestock-ai-agent-l7-pv-20261027 --env-file .env.demo config --quiet`, dịch vụ `db`/`app`; xác nhận tên project, env demo/mock, localhost bind, MySQL không publish port, volume/network riêng, kiểm tra tĩnh không chứng minh runtime.
- **PASS:** `.env.demo` tồn tại và bị Git ignore; required variable names hiện diện; không in secret.
- **PASS:** Docker resource query chỉ đọc; container/network/volume theo project name absent; host 5187 free. Host 3306 is in use, and other Docker stack(s) are running.
- **NOT RUN:** Docker build/up, healthcheck runtime, MySQL integration, login/logout, dashboard and management pages, `/assistant`, migration and seed.
- Lý do không chạy: chưa có cấu hình/secret demo riêng, target database và MySQL server UUID được operator xác minh; người dùng yêu cầu chờ xác nhận trước thao tác thay đổi môi trường.
- **Không tác động:** không gọi Compose up/down, không kết nối database, không tạo/xóa volume/container/network, không migration/seed.
