# BotHost — Hệ thống treo bot 24/7 (web + backend thật)

Web dashboard + backend Python điều khiển chạy/dừng script bot thật (.py / .js / .zip).
Một dịch vụ duy nhất, deploy free trên Render + UptimeRobot giữ thức.

## Cấu trúc
```
bothost/
├── main.py            # Backend Flask API + khởi chạy tiến trình bot thật
├── index.html         # Web dashboard (gọi API, tự fallback mock khi mở file offline)
├── requirements.txt   # Thư viện Python
├── Procfile           # Lệnh chạy trên Render
├── render.yaml        # Cấu hình deploy 1-click
└── .env.example       # Mẫu biến môi trường
```

## Chạy local
```bash
pip install -r requirements.txt
python main.py
# Mở http://localhost:8080 → đăng nhập admin / admin123
```

## Deploy lên Render (free, treo 24/7)
1. Đẩy cả thư mục này lên GitHub (1 repo riêng).
2. Vào Render → **New → Web Service** → kết nối repo đó.
   - Hoặc dùng **New → Blueprint** chọn file `render.yaml` (điền sẵn cấu hình).
3. Settings:
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn main:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1`
   - **Plan:** Free
4. **Environment Variables** (quan trọng):
   - `ADMIN_USERNAME` = admin (hoặc tên bạn muốn)
   - `ADMIN_PASSWORD` = mật khẩu mạnh (KHÔNG để mặc định admin123)
   - `SECRET_KEY` = chuỗi ngẫu nhiên dài (Render có nút Generate)
   - `MAX_FILES` = 20, `MAX_RUNNING` = 5
5. Deploy xong, mở URL Render → đăng nhập.

## Giữ cho treo 24/7 (Render free ngủ sau 15 phút)
- Đăng ký **UptimeRobot** (free) → tạo monitor loại **HTTP(s)** → trỏ vào URL `https://<app>.onrender.com/` → interval **5 phút**.
- Nhờ đó Render luôn được ping → không ngủ → bot chạy liên tục. (Giống hệt cơ chế Flask keep-alive của bot gốc.)

## Chạy file .js?
- Render Python environment **không có Node.js** sẵn. Nếu cần treo bot .js, dùng môi trường **Node** hoặc thêm build step cài node. Khuyến nghị: gom bot .js vào file .zip có `package.json` và deploy riêng, hoặc dùng nền tảng hỗ trợ đa runtime (Railway, Fly.io).

## Lưu ý quan trọng
- **Đĩa Render free là tạm thời (ephemeral):** mỗi lần redeploy/restart, file upload + log bị xoá. Bot đang chạy cũng bị dừng và phải bấm Chạy lại. Muốn lưu bền → nâng cấp Render có **Persistent Disk** (trả phí) hoặc tự backup.
- **Giới hạn phần cứng free:** 512MB RAM, CPU shared — treo được vài bot Telegram nhẹ; nhiều bot cần nâng cấp.
- **Token bot Telegram:** đặt trong biến môi trường của bot script, không hardcode trong file upload.
- **Đổi mật khẩu admin:** sửa biến môi trường rồi deploy lại (user admin được tạo lần đầu theo env).

## API (tự dùng nếu cần tích hợp)
| Method | Path | Mô tả |
|---|---|---|
| POST | /api/auth/login | Đăng nhập → trả token |
| POST | /api/auth/register | Đăng ký user |
| GET | /api/files?q= | List file + stats |
| POST | /api/files/upload | Upload file (multipart) |
| POST | /api/files/:id/start | Chạy bot |
| POST | /api/files/:id/stop | Dừng bot |
| POST | /api/files/:id/restart | Khởi động lại |
| DELETE | /api/files/:id | Xoá file |
| GET | /api/files/:id/log | Đọc log (200 dòng cuối) |

Header xác thực: `Authorization: Bearer <token>`.
