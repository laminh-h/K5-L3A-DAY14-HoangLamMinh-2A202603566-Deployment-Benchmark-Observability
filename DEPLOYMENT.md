# Deployment Information

## Public URL

https://k5-l3a-day14-hoanglamminh-2a202603566-deployment-production.up.railway.app

## Platform

**Railway**: deploy agent Part 6 (`06-lab-complete/`) từ GitHub repo này.

| Thành phần | Cấu hình |
|---|---|
| Service agent | Source: GitHub repo, **Root Directory = `06-lab-complete`**; build theo `06-lab-complete/railway.toml` (`builder = "DOCKERFILE"`, `healthcheckPath = "/health"`, restart `ON_FAILURE`); Dockerfile multi-stage, `CMD` chạy uvicorn theo biến `PORT` do Railway cấp |
| Redis | Railway Redis (database service trong cùng project), agent dùng qua `REDIS_URL` |
| Public domain | Settings → Networking → Generate Domain, target port 8080 |

Lần build đầu bị lỗi vì chưa đặt Root Directory: Railway build `Dockerfile` ở gốc repo (Dockerfile của ví dụ Go ở Part 2). Đặt Root Directory = `06-lab-complete` thì build đúng Dockerfile của agent.

## Test Commands

Mỗi lệnh chỉ in mã HTTP, chụp màn hình được mà không lộ key.

```bash
BASE=https://k5-l3a-day14-hoanglamminh-2a202603566-deployment-production.up.railway.app
# AGENT_API_KEY = key đã đặt trong Railway Variables (không ghi trong repo)

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

### Kết quả đã chạy (2026-11-25, UTC)

```text
$ curl -sS "$BASE/health"
{"status":"ok","version":"1.0.0","environment":"production","timestamp":"2026-11-25T10:34:05.545297+00:00"}
HTTP 200

$ curl -sS "$BASE/ready"
{"ready":true}
HTTP 200                      # agent kết nối được Redis trên Railway

$ curl -sS -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'
{"error":"Missing or invalid API key. Include header: X-API-Key: <your-key>"}
HTTP 401

$ curl -sS -X POST "$BASE/ask" -H 'Content-Type: application/json' -H 'X-API-Key: wrong-key' -d '{"question":"Hello"}'
HTTP 401
```

Kiểm tra có API key (chạy trong PowerShell, key nhập bằng `Read-Host`, không in ra màn hình), ảnh `screenshots/test.png`:

```text
health        : 200
ready         : 200
ask (no key)  : 401
ask (with key): 200
rate limit    : 200 200 200 200 200 200 200 200 200 429 429 429
```

Dòng cuối gửi 12 request liên tiếp có key: 9 request 200 rồi 429. Request "ask (with key)" ngay trước đó đã dùng 1 lượt, nên tổng đúng `RATE_LIMIT_PER_MINUTE=10` trong 60 giây; state rate limit nằm trong Redis của Railway.

### `check_production_ready.py --public-url`

```text
$ python check_production_ready.py --public-url https://k5-l3a-day14-hoanglamminh-2a202603566-deployment-production.up.railway.app
...
✅ 12. Public URL ready (Railway / Render config)
     ✅ Public URL: GET https://k5-l3a-day14-hoanglamminh-2a202603566-deployment-production.up.railway.app/health → 200
...
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

## Environment Variables Set

Đặt trong Railway → service → Variables (không ghi giá trị trong code hay repo):

- `AGENT_API_KEY`: secret, chuỗi ngẫu nhiên do tôi tự sinh
- `REDIS_URL` = `${{Redis.REDIS_URL}}`: tham chiếu tới Redis service của Railway
- `PORT`: Railway tự cấp; Dockerfile chạy `uvicorn ... --port ${PORT:-8000}`
- Dùng giá trị mặc định trong image/code: `ENVIRONMENT=production` (đặt sẵn trong Dockerfile, `/health` trả `"environment":"production"`), `RATE_LIMIT_PER_MINUTE=10`, `DAILY_BUDGET_USD=1.0`, `MONTHLY_BUDGET_USD=10.0`, `LOG_LEVEL=INFO`

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png): project Railway gồm service agent và Redis, cả hai Online
- [Service running](screenshots/running.png): deployment Active/Online
- [Test results](screenshots/test.png): 4 lệnh kiểm tra ra 200 / 200 / 401 / 200
