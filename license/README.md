# OmniVoice — License & Build System

Hệ thống activation online dùng Cloudflare Worker + KV để quản lý license key theo tháng.

## 1. Deploy Worker

### Yêu cầu
- Tài khoản Cloudflare (free)
- Node.js (để cài wrangler)

### Các bước

```bash
# Cài wrangler
npm install -g wrangler

# Tạo KV namespace
wrangler kv:namespace create LICENSE_KV
# -> Output: { "binding": "LICENSE_KV", "id": "abc123" }
```

Tạo file `wrangler.toml`:
```toml
name = "omnivoice-license"
main = "worker.js"
compatibility_date = "2026-07-01"

[[kv_namespaces]]
binding = "LICENSE_KV"
id = "abc123"  # ID từ lệnh trên
```

Sửa `ADMIN_SECRET` và `SIGN_KEY` trong `worker.js` thành random string (ít nhất 32 ký tự).

```bash
# Deploy
wrangler deploy

# Lấy URL worker
# -> https://omnivoice-license.<your-subdomain>.workers.dev
```

## 2. Sinh license key

Mỗi tháng sinh license key cho khách hàng:

```bash
# Sinh 1 key 30 ngày
python admin_gen.py gen --days 30 --count 1

# Sinh 5 key 90 ngày (cho gói quý)
python admin_gen.py gen --days 90 --count 5

# Liệt kê tất cả keys
python admin_gen.py list
```

Set biến môi trường (hoặc sửa trong file):
```bash
set LICENSE_WORKER_URL=https://omnivoice-license.<your-subdomain>.workers.dev
set LICENSE_ADMIN_KEY=your-admin-secret
```

## 3. Build exe cho khách

```bash
# Install Nuitka
pip install nuitka zstandard

# Build standalone exe (mất 20-60 phút)
python build_nuitka.py

# Output: dist/omnivoice-demo.exe (~3-5 GB)
```

### Sửa SERVER_URL trước build

Trong `omnivoice/_license.py`, dòng:
```python
SERVER_URL = "https://omnivoice-license.YOUR-SUBDOMAIN.workers.dev"
```

→ Sửa thành URL worker thật của bạn.

### Yêu cầu máy khách
- Windows 10/11
- NVIDIA GPU (CUDA driver)
- Dung lượng trống > 10GB (cho exe + model weights)

## 4. Luồng hoạt động

```
User double-click .exe
  → License check:
    - Có license cached? → Check online (revoke/expiry)
    - Không → Hiển thị màn hình activation
  → User nhập license key
  → POST /activate → trả token (signed + expiry)
  → Cache token → Load model + Gradio UI
  → Mỗi lần chạy: verify online nhẹ
  → Hết hạn: block, yêu cầu nhập key mới
```

## Cấu trúc file

```
license/
├── worker.js          # Cloudflare Worker (deploy lên CF)
├── client.py          # License client (copy vào omnivoice/_license.py)
├── admin_gen.py       # Tool sinh license key
├── build_nuitka.py    # Build .exe
└── README.md          # File này
omnivoice/
├── _license.py        # Copy của client.py (import bởi demo.py)
└── cli/demo.py        # Có license gate
