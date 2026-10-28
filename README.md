# Hệ thống quản lý chăn nuôi

Ứng dụng Flask/Jinja tiếng Việt quản lý chuồng, đàn/cá thể, thức ăn, sức khỏe, lịch và kết quả tiêm phòng, cảnh báo, audit và trợ lý đọc dữ liệu theo farm scope.

## Kiến trúc và trạng thái

- Flask app factory, SQLAlchemy/Flask-Migrate, MySQL và migration hiện có; giao diện Jinja/HTML/CSS/JavaScript.
- Đăng nhập, session, CSRF, farm membership và permission được kiểm tra ở backend. Workflow nghiệp vụ ghi qua service; lịch sử không xóa cứng.
- `/assistant` dùng sáu tool SQLAlchemy chỉ đọc; mỗi lần gọi xác minh membership/farm, schema và row cap; chỉ đọc dữ liệu thuộc `SyntheticDataset` đã đăng ký.
- `/assistant` hỗ trợ `disabled`, `mock` offline và Gemini API thật. `mock` chỉ trong `test`/`demo`; Gemini dùng tool calling có giới hạn và không tự chuyển sang mock khi API lỗi.
- `livestock_app/demo_seed.py` cung cấp lệnh seed tường minh; từ chối nếu không xác minh được môi trường/database, không có auto-seed, purge, reset hoặc ghi đè.
- `Dockerfile` và `docker-compose.yml` dành riêng cho demo. Không tự chạy migration/seed khi container khởi động; đây không phải cấu hình production.

## Các trang

| Đường dẫn | Chức năng |
|---|---|
| `/login` | Đăng nhập bằng mật khẩu đã hash |
| `/` | Dashboard truy vấn DB, không cộng trùng cá thể thuộc đàn |
| `/barns`, `/barns/<id>` | Danh sách, tìm kiếm, chi tiết; owner/manager được tạo và chỉnh sửa chuồng |
| `/livestock`, `/livestock/herds/<id>`, `/livestock/animals/<id>` | Đàn/cá thể, workflow thay đổi và lịch sử nghiệp vụ |
| `/health-records` | Ghi nhận quan sát sức khỏe, không chẩn đoán |
| `/feed` | Tồn kho kg và ledger; thay đổi qua service/transaction |
| `/vaccinations` | Lịch dự kiến tách biệt kết quả thực tế |
| `/alerts` | Cảnh báo; quyền xác nhận/ghi chú/đóng theo vai trò |
| `/audit` | Nhật ký chỉ đọc, owner trong farm scope |
| `/assistant` | Sáu tool chỉ đọc, trạng thái mock/Gemini và dữ liệu nguồn theo farm |

Mọi POST cần CSRF; GET chỉ đọc. ID do trình duyệt gửi không quyết định farm scope. Agent không có tool ghi, SQL tùy ý, shell, audit reader hay support grant.

### Vai trò

- `owner`: quản lý trang trại và thành viên, xem nhật ký kiểm toán.
- `manager`: quản lý nghiệp vụ trang trại, nhưng không quản lý thành viên hoặc nhật ký kiểm toán.
- `care_staff`: vai trò hiện có; hành vi và các grant quyền hiện tại được giữ nguyên.
- `veterinarian`: chỉ đọc danh sách/chi tiết vật nuôi, hồ sơ sức khỏe và lịch tiêm; không ghi dữ liệu, quản lý thành viên, hoặc xem kho/cảnh báo. Trợ lý AI chỉ được cung cấp công cụ tổng quan vật nuôi, lịch tiêm và lịch sử sức khỏe.
- Admin/quản trị nền tảng là `PlatformAuthorization` riêng, không phải farm membership và không thể được cấp từ biểu mẫu thành viên. Hệ thống không tự tạo tài khoản Admin mặc định.

Owner có thể tạo `manager`, `veterinarian` hoặc `care_staff` tại **Thành viên**. Mật khẩu ban đầu tối thiểu 12 ký tự được băm ở backend. Thay đổi role constraint cần cập nhật schema bằng migration mới `f173c942d5a1`; triển khai bằng lệnh `flask --app wsgi:app db upgrade` sau khi xác minh đúng database và có bản sao lưu phù hợp. Không tự chạy migration khi khởi động ứng dụng.

## Cấu hình local

Yêu cầu Python/dependencies trong `requirements.txt` và MySQL riêng cho môi trường không phải test. Cấu hình `APP_ENV`, `SECRET_KEY`, `DATABASE_URL` (`mysql+pymysql`) và `AGENT_MODE` từ secret manager hoặc môi trường riêng; không đưa secret vào Git, lệnh có secret hoặc log. `.env.example` chỉ chứa placeholder.

1. Tạo database development riêng, xác minh đúng server/database trước khi ghi.
2. Nạp cấu hình an toàn; dùng `disabled` mặc định, `mock` chỉ trong test/demo, hoặc bật Gemini theo mục bên dưới sau khi cho phép gửi dữ liệu tới nhà cung cấp ngoài.
3. Sau khi xác minh database riêng và đúng mục đích, áp dụng migration tường minh:

   ```powershell
   flask --app wsgi:app db upgrade
   ```

4. Tạo farm/owner đầu tiên bằng CLI có prompt mật khẩu ẩn, không có credential mặc định:

   ```powershell
   flask --app wsgi:app provision-owner
   ```

5. Chạy local chỉ bind localhost:

   ```powershell
   flask --app wsgi:app run --host 127.0.0.1
   ```

Không chạy migration trên DB đang dùng/production. Cookie `HttpOnly` và `SameSite=Lax`; `Secure` bật cho production. Triển khai internet-facing cần HTTPS và các kiểm soát vận hành phù hợp.

## Gemini API

SDK chính thức `google-genai` dùng `client.models.generate_content()` với khai báo function calling từ tool registry hiện có. Đặt `AGENT_MODE=gemini`, `GEMINI_MODEL` và `GEMINI_API_KEY` **ở máy chủ**; ứng dụng không gửi key tới trình duyệt. Chọn model còn được hỗ trợ trong tài khoản/region của bạn (ví dụ alias `gemini-flash-latest` trong `.env.example`). Nếu key hoặc model thiếu, giao diện báo chưa cấu hình và không gọi API.

Khi người dùng gửi câu hỏi, nội dung câu hỏi cùng dữ liệu mà các tool trả về sẽ được truyền tới Google Gemini API. Hiện tool registry chỉ trả bản ghi trong farm hiện hành đã gắn `SyntheticDataset`; không gửi toàn bộ database, thông tin đăng nhập, session, API key hay dữ liệu ngoài kết quả công cụ. Lịch sử tối đa bốn lượt chat được giữ trong `sessionStorage` của tab trình duyệt và gửi lại như ngữ cảnh cho Gemini; backend xác thực role/định dạng/kích thước, không tin lịch sử để cấp quyền tool. Lịch sử không ghi vào database và có thể xóa bằng nút **Xóa hội thoại**. Chỉ bật Gemini khi đã được phép xử lý dữ liệu với nhà cung cấp bên ngoài.

Thiết lập local bằng secret manager hoặc biến môi trường của tiến trình, không đưa giá trị key vào terminal command, source, trình duyệt hoặc log. Không dùng placeholder làm API key. Với Docker Compose, giữ key trong file cấu hình riêng đã được bảo vệ/ignore hoặc secret manager; Compose truyền key vào môi trường riêng của app container. Chỉ sau khi đã cấu hình riêng, cập nhật app (không nhắm tới service DB và không xóa volume) bằng:

```powershell
docker compose --env-file .env.demo -p livestock-ai-agent-l7-pv-20261027 -f docker-compose.yml build app
docker compose --env-file .env.demo -p livestock-ai-agent-l7-pv-20261027 -f docker-compose.yml up -d --no-deps app
```

Sau đó mở `/assistant`; trạng thái sẵn sàng chỉ xác nhận có cấu hình key/model, không xác minh kết nối. Gửi một câu hỏi không nhạy cảm; câu trả lời có nhãn **GEMINI API** là câu trả lời từ chế độ Gemini. Lỗi xác thực, timeout, lỗi dịch vụ và HTTP 429 được trình bày bằng thông báo tiếng Việt; ứng dụng không fallback sang mock. Không có API key đồng nghĩa không thể xác nhận kết nối thật.

## Demo Docker Compose (hướng dẫn, không tự chạy)

Compose khai báo project name `livestock-ai-agent-l7-pv-20261027`, MySQL `8.0.46`, volume và network theo namespace Compose, DB không publish ra host và app bind `127.0.0.1:5187`. `APP_ENV=demo` và `AGENT_MODE=mock` là mặc định của service app; có thể chọn `gemini` qua cấu hình riêng. Thông tin kết nối DB được truyền dưới dạng thành phần riêng và SQLAlchemy dựng URL để mã hóa đúng password có ký tự đặc biệt. Healthcheck MySQL dùng `mysqladmin ping` không truyền password trên command line; healthcheck Flask xác nhận process HTTP còn phản hồi, không xác minh kết nối DB. Phiên bản image là target cấu hình, không phải tuyên bố hỗ trợ mọi phiên bản MySQL.

**Kiểm tra cấu hình demo trong lượt này:** `.env.demo` tồn tại cục bộ và bị Git ignore; các biến Compose bắt buộc đã hiện diện (không đưa giá trị vào log). `DEMO_DATABASE_SERVER_UUID` và `DEMO_DATABASE_ATTESTATION` chưa được cấu hình; đây là guard cần thiết trước khi seed. `docker compose -p livestock-ai-agent-l7-pv-20261027 --env-file .env.demo config --quiet` thành công, nhưng chỉ xác minh cấu hình tĩnh. Docker runtime, database, migration và seed chưa chạy. Tại lần kiểm tra chỉ đọc, có stack Docker khác đang chạy, cổng 3306 bận, cổng 5187 trống; container/network/volume theo project name trên chưa tồn tại.

1. Dùng `.env.demo` chỉ cục bộ; đặt secret ngẫu nhiên ngoài Git. Password MySQL có thể chứa ký tự đặc biệt vì app dựng URL qua `URL.create`. Chưa đặt `DEMO_DATABASE_SERVER_UUID` hoặc `DEMO_DATABASE_ATTESTATION` cho đến khi database mới được kiểm tra.
2. Trước khi khởi động sau phê duyệt operator, kiểm tra lại container/network/volume và cổng. MySQL không publish ra host; chỉ app bind localhost. Lệnh khởi chạy đúng project đã kiểm tra:

   ```powershell
   docker compose -p livestock-ai-agent-l7-pv-20261027 --env-file .env.demo up --build -d
   ```

   Lệnh này tạo container/volume và khởi động MySQL; không có migration/seed tự động.
3. Sau khi xác minh `up` chỉ tạo tên tài nguyên project duy nhất, kiểm tra MySQL health. Ping healthcheck chỉ xác nhận server phản hồi, không chứng minh schema hoặc app DB access. Xác minh trong DB container tên database và UUID MySQL bằng lệnh tương tác (mật khẩu được hỏi, không truyền trong command):

   ```powershell
   docker compose -p livestock-ai-agent-l7-pv-20261027 --env-file .env.demo exec db mysql -uroot -p --database=livestock_demo_l7_pv_20261027 -Nse "SELECT DATABASE(), @@server_uuid;"
   ```

   Chỉ tiếp tục nếu stack là demo riêng vừa tạo; cập nhật UUID thật vào file riêng và đặt attestation chính xác `VERIFIED_PRIVATE_NON_PRODUCTION_DEMO_DATABASE`. Đồng bộ lại biến app sau cập nhật.
4. Chỉ sau khi xác minh database mới, riêng và trống, áp dụng migration tường minh rồi seed:

   ```powershell
   docker compose -p livestock-ai-agent-l7-pv-20261027 --env-file .env.demo exec app flask --app wsgi:app db upgrade
   docker compose -p livestock-ai-agent-l7-pv-20261027 --env-file .env.demo exec app flask --app wsgi:app seed-demo --confirm-database-name livestock_demo_l7_pv_20261027
   ```

   Seed hỏi mật khẩu tài khoản demo ẩn, yêu cầu tối thiểu 12 ký tự, kiểm tra tên DB/server UUID/attestation và từ chối bảng nghiệp vụ đã có dữ liệu. Rerun hợp lệ là no-op; dataset không hợp lệ bị từ chối, không sửa/xóa.
5. Mở `http://127.0.0.1:5187`, đăng nhập bằng `demo-owner@example.invalid` và mật khẩu vừa nhập. Dữ liệu là tổng hợp; trợ lý chỉ mô phỏng.
6. Sau demo, chỉ dừng stack đã xác minh bằng `docker compose -p livestock-ai-agent-l7-pv-20261027 --env-file .env.demo down`; không thêm `-v`.

Không dùng lệnh này với project/volume/database khác; Compose không phải production deployment.

## Kịch bản demo

Kịch bản 7 bước với dữ liệu tổng hợp:

1. Đăng nhập tài khoản demo đã seed.
2. Mở dashboard và nêu rõ mọi số liệu là tổng hợp.
3. Xem 3 chuồng, 5 đàn và 4 cá thể định danh; đối chiếu tổng đàn/cá thể không đếm trùng.
4. Xem kho thức ăn với đơn vị kg.
5. Xem lịch sắp đến hạn/quá hạn, kết quả thực tế và cảnh báo mở; lịch dự kiến không được coi là đã tiêm.
6. Hỏi trợ lý: “Cho tôi tổng quan số lượng vật nuôi”, “Tồn kho thức ăn hiện tại là bao nhiêu?” và “Lịch tiêm sắp đến hạn và kết quả thực tế”. Kiểm tra tool nguồn và nhãn **MÔ PHỎNG**.
7. Thử “Truy cập trang trại khác” hoặc “Kê thuốc và liều dùng”; xác nhận Agent từ chối và không gọi tool. Injection được kiểm tra trong test fixture riêng.

Chi tiết bước demo và kết quả định trước nằm ở [docs/demo-script.md](./docs/demo-script.md). Không có LLM accuracy/evaluation claim; bộ ca đánh giá chỉ chạy trên mock hiện tại.

## Kiểm thử

```powershell
python -m pytest -q
```

Test mặc định dùng SQLite cô lập và mock Gemini SDK; không gọi Internet hoặc dùng quota. Bài kiểm thử API thật là opt-in: cấu hình `GEMINI_API_KEY` và `GEMINI_MODEL` trong môi trường tiến trình, rồi đặt `RUN_GEMINI_INTEGRATION=1` trước khi chạy riêng `tests/test_agent.py`; bài này chỉ gửi câu hỏi chào ngắn không có dữ liệu trang trại và có thể tiêu tốn quota. Hai bài kiểm thử MySQL disposable được bỏ qua nếu không cấu hình `MYSQL_TEST_DATABASE_URL`. Các test MySQL có thể sửa schema (upgrade/downgrade), nên chỉ chạy khi có đủ `MYSQL_TEST_DATABASE_URL`, `MYSQL_TEST_DATABASE_NAME`, `MYSQL_TEST_SERVER_UUID` và `MYSQL_TEST_DISPOSABLE_ATTESTATION=I_CONFIRM_THIS_IS_A_DISPOSABLE_MYSQL_TEST_DATABASE`; preflight xác minh URL/driver/prefix, database thực tế, UUID và schema trống trước khi migration. Không tự kết nối DB khác.

Compose kiểm tra bằng `docker compose -p livestock-ai-agent-l7-pv-20261027 --env-file .env.demo config --quiet` (exit 0; dịch vụ `db`, `app`). Đây là kiểm tra cấu hình, không chứng minh image build hoặc runtime. Trong lần kiểm tra này, cổng host `5187` và tên resource theo project không va chạm; cổng `3306` đang được sử dụng nhưng DB Compose không publish cổng đó. Không start service, tạo volume hoặc chạy migration/seed.

## Giới hạn còn lại

- Gemini yêu cầu API key/model hợp lệ và quyền gửi câu hỏi, lịch sử trong tab cùng kết quả tool ra dịch vụ Google. Lịch sử chỉ nằm trong `sessionStorage` của trình duyệt; không lưu trong database. Agent không ghi dữ liệu nghiệp vụ và không thay thế đánh giá độc lập câu trả lời.
- Mock là nhận diện ý định theo luật + truy vấn synthetic-only; Gemini cũng chỉ có sáu tool đọc synthetic-only hiện có. Mỗi yêu cầu tối đa 3 lần gọi tool, 75 giây tổng thời gian, 2.000 ký tự input và 8.000 ký tự output; mỗi request HTTP Gemini có timeout 20 giây và chỉ retry tối đa một lần cho HTTP 502/503/504.
- Quy trình tạo thành viên đã có trong UI dành cho owner; luồng mời/xác nhận email, thu hồi membership, rate limit đăng nhập và khôi phục mật khẩu chưa có.
- `species` là danh mục dùng chung; một số mã loài cần được cấu hình sẵn.
- Thành viên và audit vẫn owner-only; quản lý nghiệp vụ chuồng/lịch tiêm/cảnh báo dành cho owner và manager. Không có ngoại lệ sức chứa, correction outcome, alert reopen hoặc ngưỡng cảnh báo sức khỏe cấu hình được.
- MySQL/Compose runtime chưa được chạy trong Lát 7; cấu hình không phải bằng chứng container hoạt động. `/health/live` chỉ kiểm tra Flask process, không chứng minh truy vấn DB; sau khi được operator cho phép phải xác minh thêm đăng nhập/trang DB.
- `tzdata` được khai báo để `zoneinfo` hoạt động trên Windows; test toàn suite ban đầu phát hiện dependency thiếu và đã chạy lại sau khi cài.
