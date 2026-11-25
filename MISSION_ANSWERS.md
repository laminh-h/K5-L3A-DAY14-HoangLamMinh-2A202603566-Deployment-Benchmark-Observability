# Day 14 Lab - Mission Answers

> Môi trường chạy: Windows 10 21H2 + WSL2 (Ubuntu 24.04.5, Python 3.12.3), Docker Engine 29.10.1 + Docker Compose v5.8.0 chạy trong WSL2.
> Mọi output dưới đây là output thật từ các lệnh đã chạy.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

File: `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng code | Hậu quả |
|---|---|---|---|
| 1 | Hardcode secret (API key + mật khẩu DB) | `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` (dòng 17), `DATABASE_URL = "postgresql://admin:password123@..."` (dòng 18) | Push lên GitHub là lộ key; muốn đổi key phải sửa code và deploy lại |
| 2 | Log ra secret | `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` (dòng 34) | Secret nằm trong log, ai đọc được log là có key |
| 3 | Dùng `print()` thay vì logging có cấu trúc | dòng 33, 34, 38 | Không có level, timestamp, không parse được trong log aggregator |
| 4 | Config cứng trong code, không đọc env | `DEBUG = True`, `MAX_TOKENS = 500` (dòng 21–22) | Không đổi được cấu hình giữa dev/staging/prod mà không sửa code |
| 5 | Không có health check | không có `/health`, `/ready` (gọi `GET /health` → **404**) | Platform không biết agent chết để restart, load balancer không biết khi nào route traffic |
| 6 | Bind `host="localhost"` | dòng 51 | Trong container/cloud, chỉ nhận kết nối từ chính nó, bên ngoài không gọi được |
| 7 | Port cố định `port=8000` | dòng 52 | Railway/Render inject `PORT` qua env, app không nghe đúng cổng sẽ fail health check |
| 8 | `reload=True` (debug reload) | dòng 53 | Tốn tài nguyên, chạy thêm process theo dõi file, không dùng cho production |
| 9 | Không xử lý shutdown / lifecycle | không có lifespan, không có SIGTERM handler | Khi platform tắt container, request đang chạy có thể bị cắt ngang |
| 10 | Input nhận qua query string, không validate | `def ask_agent(question: str)` | Không giới hạn độ dài, câu hỏi nằm trong URL (lộ trong access log, proxy log) |

### Exercise 1.2: Chạy basic version

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP 422          # bản develop chỉ nhận question qua query string

$ curl http://localhost:8000/health
HTTP 404          # không có health check
```

Log server (thấy rõ anti-pattern: secret bị in ra log, chỉ chạy ở localhost):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [7688] using WatchFiles
Starting agent on localhost:8000...
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.
INFO:     127.0.0.1:44492 - "POST /ask?question=Hello HTTP/1.1" 200 OK
```

**Quan sát:** chạy được, nhưng chưa production-ready: lộ secret trong log, không có health check, chỉ nghe ở `localhost:8000`, đang chạy reloader.

### Exercise 1.3: Comparison table

Chạy bản production (`01-localhost-vs-production/production/`, biến môi trường lấy từ `.env.example`):

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":4.5,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:17:41.505521+00:00"}
$ curl http://localhost:8000/ready
{"ready":true}
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","model":"gpt-4o-mini"}
$ kill -TERM 7739
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [7739]
```

| Feature | Develop | Production | Why Important? |
|---------|---------|------------|----------------|
| Config | Hardcode trong code (`DEBUG = True`, `MAX_TOKENS = 500`) | `config.py` đọc env (`os.getenv("PORT")`, `DEBUG`, `LLM_MODEL`…), có `validate()` fail-fast | Cùng một image chạy được ở dev/staging/prod chỉ bằng đổi env; không phải sửa code để đổi cấu hình |
| Secrets | `OPENAI_API_KEY = "sk-..."` trong code, còn bị `print` ra log | `os.getenv("OPENAI_API_KEY")`, `.env` không commit, chỉ commit `.env.example`; log không chứa secret | Secret đã vào git history thì coi như lộ; đổi key chỉ cần đổi env, không cần deploy code mới |
| Host / Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env | Container và cloud cần nghe trên mọi interface và đúng cổng platform cấp, không thì health check fail |
| Health check | Không có (`/health` → 404) | `GET /health` (liveness) + `GET /ready` (readiness, 503 khi chưa sẵn sàng) | Platform dùng `/health` để restart khi agent treo; load balancer dùng `/ready` để không gửi traffic vào instance chưa sẵn sàng |
| Logging | `print()` | `logging` với format JSON, không log secret | Log JSON parse được bằng Loki/Datadog, lọc theo field (event, level); `print` không có level/timestamp |
| Shutdown | Tắt đột ngột, không có lifecycle | `lifespan` (startup/shutdown) + handler SIGTERM, uvicorn chờ request đang chạy xong | Khi deploy/scale down, request đang xử lý không bị cắt, đóng kết nối sạch |
| Reload | `reload=True` luôn bật | `reload=settings.debug` (chỉ khi `DEBUG=true`) | Reloader tốn CPU và không cần thiết trong production |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ cho frontend được phép gọi API |
| Input | Query string, không validate | JSON body, trả 422 khi thiếu `question` | Lỗi rõ ràng, câu hỏi không lộ trong URL/access log |

**Điều tôi thấy khi chạy thật bản production:** log không ra JSON như mong đợi. Nguyên nhân: `config.py` gọi `logging.warning("OPENAI_API_KEY not set …")` lúc import, **trước** `logging.basicConfig(...)` trong `app.py`. Lời gọi `logging.warning` đầu tiên tự cấu hình root logger với format mặc định và level WARNING, nên `basicConfig` sau đó không còn tác dụng: dòng log là `WARNING:root:OPENAI_API_KEY not set — using mock LLM` và các log INFO (startup/shutdown) bị nuốt mất. Cách sửa: gọi `logging.basicConfig(..., force=True)` hoặc cấu hình logging trước khi import `config`. Đây là ví dụ cho thấy cần kiểm tra log thật, không chỉ đọc code.

**Checkpoint 1:**
- Hardcode secret nguy hiểm vì secret đi theo mọi bản copy của code (git history, fork, image Docker, log), không thu hồi được bằng cách xoá file; phải revoke key.
- Biến môi trường: platform/`.env` inject giá trị lúc chạy, code chỉ `os.getenv(...)`.
- Health check giúp platform tự restart agent chết và load balancer biết instance nào nhận traffic.
- Graceful shutdown: khi nhận SIGTERM, ngừng nhận request mới, xử lý xong request đang chạy, đóng kết nối rồi mới thoát.

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions

File: `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` — bản Python đầy đủ (Debian + build tools + Python 3.11), rất nặng (image cuối 1.67 GB).
2. **Working directory:** `/app` (`WORKDIR /app`) — mọi lệnh `COPY`, `RUN`, `CMD` sau đó chạy trong thư mục này.
3. **Vì sao `COPY requirements.txt` trước:** Docker cache theo layer. Layer `RUN pip install` chỉ build lại khi các layer trước nó thay đổi. Copy `requirements.txt` trước rồi `pip install`, sau đó mới `COPY app.py` → sửa code chỉ làm mất cache của layer copy code, layer cài dependencies (53.3 MB, lâu nhất) được dùng lại. Nếu `COPY . .` trước thì mỗi lần sửa 1 dòng code là phải cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số mặc định, bị ghi đè khi chạy `docker run <image> <lệnh khác>`. `ENTRYPOINT` là chương trình cố định luôn được chạy; tham số của `docker run` (hoặc `CMD`) được nối vào sau nó, muốn đổi phải dùng `--entrypoint`. Cả hai nên viết dạng exec (`["python", "app.py"]`) để process là PID 1 và nhận được SIGTERM.

### Exercise 2.2: Build và run

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d --name p2dev -p 8000:8000 my-agent:develop
$ curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "What is Docker?"}'
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}
HTTP 422            # app develop nhận question qua query string, không nhận JSON

$ docker logs p2dev
INFO:     Started server process [1]
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     172.17.0.1:37790 - "POST /ask?question=What%20is%20Docker%3F HTTP/1.1" 200 OK
$ docker exec p2dev sh -c 'whoami; ls /app'
root                 # chạy bằng root
__pycache__  app.py  requirements.txt  utils
```

### Exercise 2.3: Multi-stage build — Image size comparison

File: `02-docker/production/Dockerfile`

- **Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc`, `libpq-dev` (build tools) và `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ để build, không được deploy.
- **Stage 2 (`runtime`, `python:3.11-slim`):** tạo user `appuser` (non-root), chỉ `COPY --from=builder /root/.local` (site-packages đã cài, 40.8 MB) + `main.py` + `utils/mock_llm.py`, `USER appuser`, có `HEALTHCHECK`, `CMD uvicorn ... --workers 2`.
- **Vì sao nhỏ hơn:** image cuối chỉ chứa stage runtime. Compiler `gcc`, header `libpq-dev`, apt cache, pip cache nằm ở stage builder và bị bỏ lại. Base `slim` cũng nhỏ hơn rất nhiều so với `python:3.11` đầy đủ (bản đầy đủ có sẵn gcc, git, nhiều thư viện dev…).

Số đo thật (`docker images` + `docker image inspect --format '{{.Size}}'`):

```text
$ docker images | grep my-agent
IMAGE                ID             DISK USAGE   CONTENT SIZE
my-agent:advanced    75eda80ed74d        240MB         57.4MB
my-agent:develop     6cafa5936cc0       1.67GB          426MB

$ docker image inspect my-agent:develop my-agent:advanced --format '{{index .RepoTags 0}} {{.Size}}'
my-agent:develop 1667783910
my-agent:advanced 239815489
```

- Develop: **1667.8 MB** (1,667,783,910 bytes)
- Production: **239.8 MB** (239,815,489 bytes)
- Difference: production nhỏ hơn **85.6%** (`1 - 239.8 / 1667.8`), tức nhỏ hơn ~7 lần

Chạy thử image production:

```text
$ docker run -d --name p2adv -p 8000:8000 -e ENVIRONMENT=production my-agent:advanced
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":6.1,"version":"2.0.0","timestamp":"2026-11-25T08:34:59.993300"}
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "What is Docker?"}'
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ docker exec p2adv whoami
appuser
$ docker inspect p2adv --format 'health={{.State.Health.Status}}'
health=healthy
```

### Exercise 2.4: Docker Compose stack

`02-docker/production/docker-compose.yml` (chạy bằng `docker compose -p k5-day14-docker up -d --build`, đã `touch .env.local`):

```text
                      network "internal" (bridge) — các service gọi nhau bằng tên service
 ┌──────────┐ :80/:443 ┌───────────────────────┐  agent:8000  ┌───────────────────────────┐
 │  Client  │─────────▶│ nginx (nginx:alpine)  │─────────────▶│ agent (FastAPI, 2 workers)│
 │  (curl)  │          │ reverse proxy / LB    │              │ build từ Dockerfile       │
 └──────────┘          │ rate limit 10 r/s/IP  │              └─────┬───────────────┬─────┘
                       │ security headers      │       redis:6379   │               │  qdrant:6333
                       └───────────────────────┘                    ▼               ▼
                                                      ┌──────────────────┐  ┌──────────────────┐
                                                      │ redis:7-alpine   │  │ qdrant v1.9.0    │
                                                      │ cache/rate limit │  │ vector DB (RAG)  │
                                                      │ vol: redis_data  │  │ vol: qdrant_data │
                                                      └──────────────────┘  └──────────────────┘
```

- **Services được start:** `redis`, `qdrant` (start trước, có healthcheck) → `agent` (`depends_on: condition: service_healthy`) → `nginx`.
- **Giao tiếp:** tất cả nằm trong network `k5-day14-docker_internal`, gọi nhau bằng tên service (DNS nội bộ của Docker): nginx → `agent:8000`, agent → `redis:6379`, `qdrant:6333`. Chỉ nginx publish port ra host (80, 443); agent, redis, qdrant không mở port ra ngoài.

```text
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 22 seconds (healthy)   8000/tcp
nginx     nginx:alpine            Up 21 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 35 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 34 seconds (healthy)   6379/tcp

$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":20.1,"version":"2.0.0","timestamp":"2026-11-25T08:36:21.759663"}

$ curl -i http://localhost/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY

{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ docker compose -p k5-day14-docker logs agent nginx
agent-1  | INFO:     172.18.0.5:54470 - "POST /ask HTTP/1.1" 200 OK      # 172.18.0.5 = IP của nginx
nginx-1  | 172.18.0.1 - - [25/Nov/2026:08:36:21 +0000] "POST /ask HTTP/1.1" 200 95 "-" "curl/8.5.0"
```

Response đi qua nginx (header `Server: nginx` và các security header do nginx thêm), log agent cho thấy request đến từ IP của container nginx.

**Nhận xét thêm:** log của agent dạng `{"time":...,"msg":"{"event": "request", "q_len": 21}"}` **không phải JSON hợp lệ** vì format string nhúng nguyên `json.dumps(...)` vào trong `"msg":"..."` mà không escape dấu `"`. Ở Part 6 tôi viết `logging.Formatter` riêng dùng `json.dumps` cho cả dòng log để tránh lỗi này.

**Debug container:** `docker logs <container>` xem stdout/stderr, `docker exec -it <container> sh` vào trong container, `docker inspect` xem trạng thái health/exit code.

---

## Part 3: Cloud Deployment

> Lớp K5 làm Part 3 sau Part 6 (CP6) và deploy luôn agent Part 6 (`06-lab-complete/`), không deploy app mẫu `03-cloud-deployment/`.

### Exercise 3.1 / 3.2: Deploy agent Part 6

<!-- DEPLOY_RESULT -->
Chi tiết URL, lệnh test và biến môi trường: [DEPLOYMENT.md](DEPLOYMENT.md).

### So sánh `railway.toml` và `render.yaml`

So sánh cả bản mẫu trong `03-cloud-deployment/` lẫn bản tôi viết cho `06-lab-complete/`:

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Định dạng / phạm vi | TOML, cấu hình **1 service** (build + deploy) | YAML **Blueprint**, khai báo **nhiều service** cùng lúc (web + Redis/Key Value, DB, cron…) | Railway tạo project/service và add-on (Redis) qua dashboard/CLI; Render coi cả hạ tầng là code trong 1 file |
| Build | `builder = "NIXPACKS"` (mẫu, tự đoán Python) hoặc `"DOCKERFILE"` (bản của tôi) | `runtime: python` + `buildCommand` (mẫu) hoặc `runtime: docker` + `dockerfilePath`/`rootDir` (bản của tôi) | Cả hai đều hỗ trợ buildpack lẫn Dockerfile; dùng Dockerfile thì môi trường build giống hệt local |
| Lệnh chạy & cổng | `startCommand = "uvicorn ... --port $PORT"` (mẫu); bản của tôi để `CMD` của Dockerfile đọc `${PORT:-8000}` | `startCommand` (mẫu) hoặc `CMD` của Docker | Cả hai platform inject biến `PORT`, app phải nghe theo nó |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` | Railway chỉ dùng health check khi deploy (chờ bản mới healthy rồi mới chuyển traffic); Render kiểm tra liên tục và restart khi fail |
| Restart policy | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries` | Không khai báo (Render tự restart) | |
| Biến môi trường / secret | **Không** khai báo trong file: đặt bằng `railway variables set` hoặc dashboard | Khai báo trong `envVars`: giá trị thường ghi thẳng, secret dùng `sync: false` (nhập tay trên dashboard) hoặc `generateValue: true` (Render tự sinh) | Render muốn file mô tả đủ hạ tầng nhưng không chứa secret |
| Redis | Thêm Redis plugin trên Railway, tham chiếu `${{Redis.REDIS_URL}}` trong Variables | Khai báo service `type: keyvalue` (mẫu ghi `type: redis`, tên cũ) và `fromService ... property: connectionString` | |
| Repo nhiều thư mục | Đặt **Root Directory** = `06-lab-complete` trong service settings (hoặc `railway up` từ thư mục đó) | `rootDir: 06-lab-complete` ngay trong file | |
| Region / plan | Chọn trên dashboard | `region: singapore`, `plan: free` trong file | |
| Auto deploy | Deploy khi push (nếu nối GitHub) hoặc `railway up` | `autoDeploy: true` | |

### Exercise 3.3: Cloud Run CI/CD (`03-cloud-deployment/production-cloud-run/`)

Pipeline trong `cloudbuild.yaml` chạy tự động khi push lên `main` (Cloud Build trigger), gồm 4 bước nối nhau bằng `waitFor`:

1. **test** — container `python:3.11-slim` cài `requirements.txt` + `pytest`, chạy `pytest tests/`; test fail thì dừng pipeline, không build.
2. **build** — `docker build` gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tận dụng layer cache.
3. **push** — đẩy cả 2 tag lên Container Registry.
4. **deploy** — `gcloud run deploy ai-agent` với image theo `$COMMIT_SHA` (truy vết được bản đang chạy, rollback được), region `asia-southeast1`, `min-instances=1` (tránh cold start), `max-instances=10`, 512Mi / 1 CPU, timeout 60s, `ENVIRONMENT=production`, `OPENAI_API_KEY` lấy từ **Secret Manager** (`--set-secrets`), không nằm trong code.

`service.yaml` là bản khai báo (IaC, Knative) của cùng service để `gcloud run services replace`: autoscaling 1–10 instance, `containerConcurrency: 80` request/instance, `livenessProbe` gọi `/health` mỗi 30s, `startupProbe` gọi `/ready` (tối đa 10 lần × 3s) trước khi nhận traffic, secret `OPENAI_API_KEY` và `AGENT_API_KEY` qua `secretKeyRef`.

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop`)

- **API key được check ở đâu?** Trong dependency `verify_api_key()` (dòng 39–54). `APIKeyHeader(name="X-API-Key", auto_error=False)` đọc header, endpoint `/ask` gắn `_key: str = Depends(verify_api_key)` nên FastAPI chạy hàm này trước khi vào handler. `/` và `/health` không gắn dependency nên public.
- **Sai key thì sao?** Thiếu header → **401** `Missing API key`; có header nhưng sai → **403** `Invalid API key.` Handler `/ask` không được gọi, LLM không bị gọi.
- **Rotate key thế nào?** Key đọc từ biến môi trường `AGENT_API_KEY` (dòng 35), nên rotate = đặt giá trị mới trên platform/secret manager rồi restart (hoặc redeploy) service, không sửa code. Muốn rotate không downtime thì cho phép tạm thời 2 key (cũ + mới, ví dụ `AGENT_API_KEYS=old,new`), báo client đổi sang key mới, rồi xoá key cũ.

Test (chạy `AGENT_API_KEY=secret-key-123 python app.py`):

```text
$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
content-type: application/json

{"detail":"Invalid API key."}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
content-type: application/json

{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

Lưu ý: bản develop trả 403 khi sai key; hợp đồng Part 6 yêu cầu **401** cho cả thiếu lẫn sai key, nên `app/auth.py` ở Part 6 trả 401 trong cả hai trường hợp.

### Exercise 4.2: JWT authentication (`04-api-gateway/production`)

**JWT flow (`auth.py`):**
1. `POST /auth/token` với username/password → `authenticate_user()` so với `DEMO_USERS` → `create_token()` ký JWT HS256 bằng `JWT_SECRET` (đọc từ env), payload gồm `sub` (username), `role`, `iat`, `exp` (60 phút).
2. Client gửi `Authorization: Bearer <token>` ở mọi request.
3. `verify_token()` (dependency của `/ask`) `jwt.decode()` kiểm tra chữ ký và hạn → trả `{username, role}`. Server không cần tra DB mỗi request (stateless). Thiếu token → 401, token hết hạn → 401, token sai chữ ký → 403.

```text
$ curl -i http://localhost:8000/auth/token -X POST -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY

{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***MASKED***"}

$ curl -i http://localhost:8000/auth/token -X POST -H "Content-Type: application/json" -d '{"username": "student", "password": "wrong"}'
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i http://localhost:8000/ask -X POST -H "Authorization: Bearer abc.def.ghi" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ curl -i http://localhost:8000/ask -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","usage":{"requests_remaining":9,"budget_remaining_usd":1.6e-05}}
```

### Exercise 4.3: Rate limiting

- **Algorithm:** Sliding Window (log) — mỗi user một `deque` timestamp; mỗi request xoá các timestamp cũ hơn 60s, nếu còn ≥ `max_requests` thì từ chối.
- **Limit:** user thường (`student`) **10 request / 60 giây**; admin **100 request / 60 giây**.
- **Bypass cho admin:** `app.py` chọn limiter theo role trong JWT: `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user` → admin (`teacher`) dùng limiter 100 req/phút. Test: 15 request liên tiếp bằng token `teacher` đều 200.
- **Hạn chế:** state nằm trong RAM của process (`defaultdict(deque)`), nên chạy nhiều worker/instance thì mỗi process đếm riêng, restart là mất. Part 6 chuyển sang Redis.

Vòng lặp 20 request bằng token `student`:

```text
Request  1: {"question":"Test 1",...,"usage":{"requests_remaining":8,...}}  -> HTTP 200
Request  2: ...  -> HTTP 200
...
Request  9: {"question":"Test 9",...,"usage":{"requests_remaining":0,...}}  -> HTTP 200
Request 10: {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}  -> HTTP 429
Request 11: ... -> HTTP 429
...
Request 20: {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}  -> HTTP 429

$ curl -i http://localhost:8000/ask -X POST -H "Authorization: Bearer $TOKEN" ... -d '{"question": "one more"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795595953
retry-after: 59

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}

$ curl http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":296,"cost_usd":0.000184,"budget_usd":1.0,"budget_remaining_usd":0.999816,"budget_used_pct":0.0}
```

User `student` bị chặn ở request thứ 10 của vòng lặp vì request "Explain JWT" ở bước 4.2 đã dùng 1 lượt (tổng 10 request trong 60s). Response 429 có `Retry-After` để client biết chờ bao lâu.

Hai lỗi nhỏ tôi thấy trong code mẫu khi chạy: field `budget_remaining_usd` trong response `/ask` thực ra trả **chi phí đã dùng** (`usage.total_cost_usd`), không phải số còn lại; và `budget_used_pct` làm tròn 1 chữ số nên chi phí nhỏ hiện `0.0`.

### Exercise 4.4: Cost guard implementation

**Cách làm:** mỗi user một key Redis theo tháng `budget:<user_id>:<YYYY-MM>` lưu tổng USD đã tiêu. Trước khi gọi LLM, kiểm tra `đã tiêu + chi phí ước tính > $10` thì từ chối; còn budget thì cộng chi phí vào key. Vì tên key chứa tháng nên sang tháng mới tự dùng key mới (reset đầu tháng); key có TTL 32 ngày để tự xoá. Đọc–kiểm tra–cộng nằm trong một Lua script để atomic: nhiều worker/instance gọi cùng lúc không thể cùng vượt budget (bản solution trong `CODE_LAB.md` dùng `GET` rồi `INCRBYFLOAT` riêng nên có race condition).

```python
from datetime import datetime, timezone
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)
MONTHLY_BUDGET_USD = 10.0

_RESERVE = r.register_script("""
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local cost = tonumber(ARGV[1])
if current + cost > tonumber(ARGV[2]) then
  return 0
end
redis.call('INCRBYFLOAT', KEYS[1], cost)
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
""")

def check_budget(user_id: str, estimated_cost: float) -> bool:
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"budget:{user_id}:{month_key}"
    return bool(_RESERVE(keys=[key], args=[estimated_cost, MONTHLY_BUDGET_USD, 32 * 24 * 3600]))
```

Chạy thử với Redis thật (`docker run -d -p 6379:6379 redis:7-alpine`):

```text
alice 4 x $3.0 -> [True, True, True, False]      # $9 ok, lần thứ 4 sẽ thành $12 > $10 → chặn
alice spent: 9 TTL(s): 2764800                    # 32 ngày
alice thêm $1.0 -> True                           # đúng $10, vẫn cho
alice thêm $0.5 -> False
bob $3.0 -> True (user khác có budget riêng)
```

Trong API: `False` → trả **HTTP 402 Payment Required**. Ở Part 6 (`06-lab-complete/app/cost_guard.py`), theo hợp đồng của script chấm, tôi tách làm `check_budget()` (trước khi gọi LLM, 402 nếu tổng đã ≥ `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD`) và `record_cost()` (sau khi gọi LLM, cộng chi phí thật tính theo token vào key ngày và key tháng trong Redis).

**Vì sao cần cả rate limit và cost guard:** rate limit bảo vệ **tốc độ** (chống spam/DoS, chia đều tài nguyên: tối đa N request/phút), cost guard bảo vệ **tiền** (tổng chi phí theo ngày/tháng). Một user gọi đúng 10 req/phút nhưng mỗi request prompt rất dài vẫn có thể đốt hết ngân sách trong vài giờ — rate limit không chặn được, chỉ cost guard chặn được.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop`)

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":3.5,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:39:54.119276+00:00","checks":{"memory":{"status":"ok","used_percent":9.7}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

- **`/health` (liveness):** "process còn sống không?" — chỉ kiểm tra những gì thuộc về chính process (ở đây thêm memory qua `psutil`). Không phụ thuộc DB/Redis: nếu Redis chết mà `/health` trả lỗi thì platform sẽ restart agent liên tục, không giải quyết được gì.
- **`/ready` (readiness):** "có nên gửi traffic vào instance này không?" — trả 503 khi đang khởi động, đang shutdown (`_is_ready = False`) hoặc dependency (Redis/DB) chưa kết nối. Load balancer bỏ instance khỏi pool chứ không restart nó. (`in_flight_requests: 1` là chính request `/ready` đang được đếm.)

Implementation đúng cho `/ready` khi có Redis (bản `production/app.py` dùng đúng cách này):

```python
@app.get("/ready")
def ready():
    try:
        _redis.ping()
    except Exception:
        raise HTTPException(503, "Redis not available")
    return {"ready": True, "instance": INSTANCE_ID}
```

### Exercise 5.2: Graceful shutdown

Test: bật app, bắn 3 request `/ask` song song rồi `kill -TERM` ngay lập tức:

```text
$ PID=11302
$ kill -TERM $PID   # gửi ngay khi 3 request đang chạy
process exit code: 0
request 1: {"answer":"Đây là câu trả lời từ AI agent (mock)..."} -> HTTP 200 (3.030809s)
request 2:  -> HTTP 000 (3.026331s)
request 3:  -> HTTP 000 (3.019488s)

--- server log ---
INFO:     Shutting down
INFO:     127.0.0.1:44880 - "POST /ask?question=Long%20task%201 HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 15:39:57,268 INFO 🔄 Graceful shutdown initiated...
2026-11-25 15:39:57,269 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [11302]
2026-11-25 15:39:57,270 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Quan sát và giải thích:**
1. Request đang được xử lý (request 1) **vẫn hoàn thành với 200** sau khi uvicorn đã in `Shutting down`, rồi mới chạy lifespan shutdown và process thoát với **exit code 0** → đây là graceful shutdown của uvicorn: ngừng nhận kết nối mới, chờ request đang chạy xong (tối đa `timeout_graceful_shutdown=30`s), rồi chạy phần sau `yield` của lifespan.
2. Request 2 và 3 **bị mất** (HTTP 000 = server đóng kết nối). Nguyên nhân: `async def ask_agent` gọi `ask()` có `time.sleep()` (code đồng bộ) → chặn event loop, nên lúc SIGTERM tới thì request 2, 3 mới nằm trong socket, uvicorn chưa đọc tới. Khi shutdown, uvicorn chỉ chờ request **đã bắt đầu xử lý**; kết nối chưa có request đang chạy bị đóng luôn. Cách sửa: khai báo `def ask_agent(...)` (FastAPI chạy trong threadpool, event loop không bị chặn) hoặc dùng client LLM async (`await`). Part 6 tôi khai báo `/ask` là `def`.
3. Dòng `Received signal 15 — uvicorn will handle graceful shutdown` in ra **sau** `Finished server process`: `uvicorn.run()` cài signal handler của nó đè lên `signal.signal(SIGTERM, handle_sigterm)` của app; khi thoát, uvicorn trả lại handler cũ và phát lại signal nên handler của app mới chạy. Nếu tự đăng ký handler mà không gọi lại handler của uvicorn thì uvicorn sẽ không bao giờ biết để tắt.

Signal handler hoàn chỉnh cho đề bài (dùng khi tự quản lý server, không để uvicorn lo):

```python
def shutdown_handler(signum, frame):
    global _is_ready
    _is_ready = False                 # 1. /ready → 503, load balancer ngừng gửi request mới
    logger.info("SIGTERM received — draining in-flight requests")
    deadline = time.time() + 30
    while _in_flight_requests > 0 and time.time() < deadline:
        time.sleep(0.5)               # 2. chờ request đang chạy xong
    redis_client.close()              # 3. đóng kết nối
    sys.exit(0)                       # 4. thoát exit code 0

signal.signal(signal.SIGTERM, shutdown_handler)
```

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

Bản production không giữ conversation trong RAM: `save_session()` / `load_session()` lưu `session:<id>` vào Redis (`SETEX`, TTL 3600s), `append_to_history()` đọc–sửa–ghi lại history (giữ 20 message gần nhất). Mọi instance cùng đọc một Redis, nên request tiếp theo của cùng session đến instance nào cũng thấy history.

```python
# ❌ State trong memory — mỗi instance một dict riêng, restart là mất
conversation_history = {}

# ✅ State trong Redis — instance nào cũng đọc được
def append_to_history(session_id, role, content):
    session = load_session(session_id)            # GET session:<id>
    session.setdefault("history", []).append({"role": role, "content": content})
    save_session(session_id, session)             # SETEX session:<id> 3600 <json>
```

Vì sao state trong RAM hỏng khi scale: Nginx round-robin request của cùng một user qua các instance khác nhau; instance 2 không có dict của instance 1 → mất history, rate limit/cost đếm sai (mỗi instance đếm riêng, giới hạn thực tế nhân lên N lần); instance restart hoặc bị scale down → mất hết state.

Lưu ý: code mẫu vẫn **fallback sang dict trong RAM** nếu không ping được Redis lúc khởi động. Trong production nên fail (để `/ready` trả 503) thay vì âm thầm chạy chế độ không scale được. Ngoài ra `append_to_history` đọc–sửa–ghi không atomic; 2 request cùng session đến 2 instance cùng lúc có thể ghi đè nhau (nên dùng `RPUSH`/`LTRIM` trên Redis list).

### Exercise 5.4: Load balancing

```text
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
$ docker compose -p k5-day14-scaling ps
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 29 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 28 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 27 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 26 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 41 seconds (healthy)   6379/tcp

# 10 request POST /chat qua nginx (cổng 8080)
Request 1: served_by=instance-c01b98 storage=redis   X-Served-By: 172.18.0.4:8000
Request 2: served_by=instance-b08bf7 storage=redis   X-Served-By: 172.18.0.3:8000
Request 3: served_by=instance-2981bc storage=redis   X-Served-By: 172.18.0.5:8000
Request 4: served_by=instance-c01b98 storage=redis   X-Served-By: 172.18.0.4:8000
Request 5: served_by=instance-b08bf7 storage=redis   X-Served-By: 172.18.0.3:8000
Request 6: served_by=instance-2981bc storage=redis   X-Served-By: 172.18.0.5:8000
Request 7: served_by=instance-c01b98 storage=redis   X-Served-By: 172.18.0.4:8000
Request 8: served_by=instance-b08bf7 storage=redis   X-Served-By: 172.18.0.3:8000
Request 9: served_by=instance-2981bc storage=redis   X-Served-By: 172.18.0.5:8000
Request 10: served_by=instance-c01b98 storage=redis  X-Served-By: 172.18.0.4:8000

# docker compose logs agent | grep 'POST /chat' | đếm theo instance
      3 agent-1
      4 agent-2
      3 agent-3
```

Nginx resolve tên `agent` qua DNS của Docker (`resolver 127.0.0.11`) ra 3 IP và **round-robin** đều: 3 / 4 / 3 request. Header `X-Served-By` (nginx thêm `$upstream_addr`) khớp với `served_by` agent trả về. `proxy_next_upstream error timeout http_503` cho phép nginx thử instance khác nếu một instance lỗi.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 6e69ac3c-4a56-467f-b633-286138f19d58

Request 1: [instance-b08bf7]   Q: What is Docker?
Request 2: [instance-2981bc]   Q: Why do we need containers?
Request 3: [instance-c01b98]   Q: What is Kubernetes?
Request 4: [instance-b08bf7]   Q: How does load balancing work?
Request 5: [instance-2981bc]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-c01b98', 'instance-2981bc', 'instance-b08bf7'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ... (đủ 5 cặp user/assistant)
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu của cùng một session đi qua cả 3 instance khác nhau, nhưng history đọc lại đủ 10 message (5 user + 5 assistant) đúng thứ tự, vì không instance nào giữ history trong RAM — tất cả đọc/ghi `session:<id>` trong Redis.

Script không tự tắt instance nào, nên tôi làm thêm phần "kill 1 instance" bằng tay:

```text
session_id=e6b9da4e-1dff-433c-a17a-a36bb612849e
(turn 1–3 trước khi tắt, phục vụ bởi cả 3 instance)
$ docker stop /k5-day14-scaling-agent-1
turn 5 served_by instance-2981bc
turn 6 served_by instance-2981bc
turn 7 served_by instance-c01b98
turn 8 served_by instance-2981bc
turn 9 served_by instance-c01b98
history count: 16
   user : Turn 1 before failure
   user : Turn 2 before failure
   user : Turn 3 before failure
   user : Turn 4 after failure
   ... 
   user : Turn 8 after failure
```

Sau khi tắt 1 instance, mọi request vẫn 200, được 2 instance còn lại phục vụ, và history vẫn đủ 8 câu hỏi (16 message) → không mất state khi một instance chết. (Field `turn` trong response bị lệch +1 so với số câu thật vì code mẫu đếm lại câu hỏi vừa append rồi còn `+ 1`; history trong Redis thì đúng.)

---

## Part 6: Final Project (`06-lab-complete/`)

### Kết quả `check_production_ready.py` (chạy đầy đủ, có Docker, trong WSL2: `os.name == "posix"`)

```text
$ cd 06-lab-complete && python check_production_ready.py
...
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết trong output: `Docker: image < 500 MB — 273 MB`, `429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)`, `state nằm trong Redis — restart agent vẫn bị 429`, `mọi dòng log của app là JSON hợp lệ — 17 dòng JSON`, `SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0`, `docker stop tắt êm — exit code 0, 1.5s`.

### Thiết kế

| File | Làm gì |
|---|---|
| `app/config.py` | `Settings` dataclass, mọi field đọc `os.getenv` (`PORT`, `ENVIRONMENT`, `LOG_LEVEL`, `AGENT_API_KEY`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `MONTHLY_BUDGET_USD`). `AGENT_API_KEY` không có default → chưa đặt thì mọi `/ask` bị 401 (fail closed) |
| `app/auth.py` | `APIKeyHeader("X-API-Key", auto_error=False)`, so sánh bằng `hmac.compare_digest` (chống timing attack); thiếu **hoặc** sai → 401 |
| `app/redis_client.py` | `redis.from_url(...)` chỉ tạo pool, không kết nối lúc import → app khởi động được khi chưa có Redis; timeout 2s; `redis_healthy()` = `PING` |
| `app/rate_limiter.py` | Sliding window trên sorted set `ratelimit:<sha256(key)>`: `ZREMRANGEBYSCORE → ZCARD → ZADD → EXPIRE` gói trong **Lua script** (atomic giữa 2 worker / nhiều instance). Vượt → 429 + `Retry-After`. Redis chỉ lưu hash của API key |
| `app/cost_guard.py` | `check_budget()` trước khi gọi LLM: đọc `cost:daily:<YYYY-MM-DD>` và `cost:monthly:<YYYY-MM>`, ≥ budget → 402. `record_cost()` sau khi gọi: `INCRBYFLOAT` cả 2 key (TTL 2 ngày / 32 ngày → tự reset) |
| `app/main.py` | Log JSON (`JsonFormatter` dùng `json.dumps` cho cả dòng), middleware log mỗi request (`request_id`, path, status, ms); `/health` không đụng Redis; `/ready` 503 khi Redis chết hoặc đang shutdown; `/ask` là `def` (chạy trong threadpool, không chặn event loop); exception handler trả lỗi JSON gọn (422 có field + message, Redis lỗi → 503, lỗi khác → 500 `Internal server error`, không lộ stack trace) |
| `Dockerfile` | Multi-stage: builder tạo venv `/opt/venv` + `pip install`; runtime `python:3.11-slim`, copy venv + `app/` + `utils/`, user `app` (non-root), `HEALTHCHECK` gọi `/health`, `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout-graceful-shutdown 8"]` (`exec` → uvicorn là PID 1) |
| `docker-compose.yml` | `agent` (build `.`, `REDIS_URL=redis://redis:6379/0`, `depends_on: redis: condition: service_healthy`, `env_file: .env` với `required: false`) + `redis:7-alpine` (healthcheck `redis-cli ping`, volume AOF) |
| `.dockerignore` | `.env`, `.env.*` (giữ `.env.example`), `__pycache__/`, `*.pyc`, `venv/`, `.venv/`, `.git/`, `benchmark-observability/`, docs |
| `railway.toml` / `render.yaml` | Railway: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`. Render: `type: web`, `runtime: docker`, `rootDir: 06-lab-complete`, `healthCheckPath: /health`, `AGENT_API_KEY` dùng `generateValue: true`, `REDIS_URL` lấy từ Key Value service `agent-redis` |

### Test thủ công (`docker compose -p k5-day14-final up -d --build`, `AGENT_API_KEY` sinh ngẫu nhiên trong shell)

```text
$ docker compose -p k5-day14-final ps
SERVICE   IMAGE                  STATUS                    PORTS
agent     k5-day14-final-agent   Up 7 seconds (healthy)    0.0.0.0:8000->8000/tcp
redis     redis:7-alpine         Up 13 seconds (healthy)   6379/tcp

GET /health                              → 200 {"status":"ok","version":"1.0.0","environment":"development",...}
GET /ready                               → 200 {"ready":true}
POST /ask (không key)                    → 401 {"error":"Missing or invalid API key. Include header: X-API-Key: <your-key>"}
POST /ask (sai key)                      → 401 (cùng thông báo)
POST /ask (đúng key) {"user_id":"test","question":"What is deployment?"}
                                         → 200 {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","user_id":"test","usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05,"rate_limit_remaining":9}}
POST /ask {"invalid":"data"}             → 422 {"error":"Invalid request body","details":[{"field":"question","message":"Field required"}]}
POST /ask {"question":""}                → 422 {"error":"Invalid request body","details":[{"field":"question","message":"String should have at least 1 character"}]}
POST /ask '{bad json'                    → 422 {"error":"Invalid request body","details":[{"field":"body","message":"Request body is not valid JSON"}]}

15 request liên tiếp: 200 200 200 200 200 200 200 200 200 429 429 429 429 429 429
HTTP/1.1 429 Too Many Requests
retry-after: 59
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
{"error":"Rate limit exceeded","limit_per_minute":10,"retry_after_seconds":59}

Redis keys: cost:monthly:2026-11, ratelimit:264d8cc2e36df2c1d0ac1ee0, cost:daily:2026-11-25 (= 0.00011715 USD)
docker compose restart agent → POST /ask: 429        # state rate limit còn trong Redis
docker compose stop redis   → /health 200, /ready 503 {"ready":false,"reason":"redis unavailable"}, /ask 503 {"error":"Service temporarily unavailable"}
docker compose start redis  → /ready 200
docker compose stop agent   → 1.8s, exit code=0
  {"ts": "...", "lvl": "INFO", "logger": "agent", "event": "shutdown", "detail": "graceful shutdown complete — closing Redis connections"}
```

(9 request 200 trước khi 429 vì request "What is deployment?" phía trên đã dùng 1 lượt trong cùng 60s → tổng đúng 10.)

Dòng log mẫu (mỗi dòng 1 JSON object):

```json
{"ts": "2026-11-25T08:47:33.079+00:00", "lvl": "INFO", "logger": "agent", "event": "request", "request_id": "a0ac9cfc258148c3", "method": "POST", "path": "/ask", "status": 503, "ms": 4138.7}
```

### Những điều tôi gặp khi làm

- **Graceful shutdown và signal handler:** uvicorn 0.30.0 (bản trong `requirements.txt`) cài handler SIGTERM trong `Server.serve()` **trước** khi import app. Vì vậy app không được `signal.signal(SIGTERM, ...)` lúc import (sẽ đè mất handler của uvicorn → uvicorn không bao giờ tắt; tôi đã thử và process bị treo). Cách đúng: trong lifespan startup lấy `signal.getsignal(SIGTERM)` (lúc này là handler của uvicorn), cài handler mới chỉ đánh dấu `shutting_down` (để `/ready` trả 503) rồi **gọi lại handler cũ**.
- **Exit code khi chạy 1 worker:** uvicorn 0.30 chạy 1 worker nhận SIGTERM thì tắt êm nhưng thoát với **143** (sau khi tắt nó phát lại SIGTERM với handler mặc định). Tôi kiểm tra bằng một app FastAPI rỗng: `plain uvicorn 0.30.0 workers=1 exit=143`. Chạy `--workers 2` thì process cha (supervisor) thoát **0**. Vì vậy `CMD` mặc định `--workers 2` (`WEB_CONCURRENCY`), khớp với cách script chấm chạy.
- **`/ready` khi Redis chết** phải trả nhanh: `socket_connect_timeout=2` để không treo request quá timeout 15s của script chấm.

---

## Part 7: Benchmark & Observability

Xem [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md), số liệu gốc trong [reports/](reports/) và [benchmark-results/](benchmark-results/), ảnh trong [screenshots/](screenshots/).

---

## AI log

Tôi dùng **Claude Code** (Anthropic, model Claude Opus) chạy trong VS Code, có quyền chạy lệnh trên máy tôi (WSL2 + Docker). Toàn bộ lệnh đều chạy thật trên máy; mọi con số trong bài và trong `BENCHMARK_REPORT.md` lấy từ output/JSON/CSV của các lệnh đó, không có số nào do AI tự điền.

| # | Tôi yêu cầu | AI làm / trả lời | Tôi kiểm tra thế nào |
|---|---|---|---|
| 1 | Đọc repo, liệt kê yêu cầu và việc cần làm | Tóm tắt README, RUBRIC, SUBMISSION, CHECKPOINTS thành danh sách việc theo checkpoint | Đối chiếu lại với RUBRIC.md và CHECKPOINTS.md |
| 2 | Cài môi trường (CP0) | Bật WSL2; Docker Desktop không cài được vì Windows 10 21H2 (build 19044) dưới mức yêu cầu 22H2 → tải Ubuntu 24.04 (`wsl --import`) và cài Docker Engine trong WSL; chuyển `iptables` sang legacy vì kernel WSL 5.10 cũ; cấu hình registry mirror `mirror.gcr.io` khi Docker Hub trả 429 | `docker run --rm hello-world` chạy được; `python3 -c "import os; print(os.name)"` in `posix` |
| 3 | CP1–CP4: chạy các ví dụ Part 1, 2, 4, 5 và trả lời câu hỏi | Viết script chạy app develop/production, curl các trường hợp 200/401/403/422/429, build image, đo size, chạy compose 3 instance, kill instance; soạn câu trả lời từ output | Đọc lại output gốc của từng lệnh trước khi chép vào bài; số MB lấy từ `docker image inspect` |
| 4 | CP5: hoàn thành TODO Part 6 | Viết `app/*.py`, Dockerfile, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml`. Phát hiện và sửa: handler SIGTERM cài lúc import làm uvicorn treo (đã thử và thấy treo, rồi bỏ); body lỗi 429 bị lồng `{"error":{"error":...}}`; lỗi JSON hỏng báo `field: "1"` | `python check_production_ready.py` (đầy đủ, có Docker) → **PASS 12/12, 43/43 checks**; test tay bằng curl với compose (401/422/429/402, restart vẫn 429, Redis chết → `/ready` 503, `docker stop` exit 0) |
| 5 | CP7: load test + eval + sửa knowledge base | Chạy `load_test.py` 4 mức concurrency và 3 mức `MOCK_SLOW_RATE`, eval v1/v2; thêm 2 entry `KNOWLEDGE_BASE` (HPA, JWT vs API key) với nội dung tự viết; viết job CI `.github/workflows/quality-gate.yml` | Trước khi chạy eval, kiểm tra offline: retrieval chỉ đổi đúng 2 câu `scaling-03`, `security-02`; eval cuối 23/23, không regression; chạy lại đúng lệnh của Lab Coach (`MOCK_ERROR_RATE=0`, cổng 8000) → PASS |
| 6 | CP8: observability stack, Locust, Jaeger, alerts | Sửa lỗi Dockerfile Part 7 (`PYTHONUSERBASE`, nếu không container báo `No module named uvicorn`); phát hiện `docker compose run locust` tự bật thêm agent theo `replicas: 2` làm hỏng lần đo 1 instance → đo lại với `--no-deps`; chụp màn hình Grafana/Jaeger/Prometheus bằng Chromium headless (Playwright) trên đúng stack đang chạy | Số instance trong lúc đo kiểm tra bằng `count(up{job="agent"} == 1)`; thời gian tới lúc firing đo bằng script hỏi `/api/v1/alerts` mỗi 5s; `trace_id` trong ảnh khớp dòng log JSON |

Không chép nguyên lời giải mẫu: `check_budget` trong CODE_LAB đọc rồi cộng tách rời (race condition khi nhiều worker), bài này dùng Lua script atomic; các entry `KNOWLEDGE_BASE` viết lại nội dung riêng thay vì dùng nguyên văn solution.
