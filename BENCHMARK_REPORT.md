# Benchmark & Observability Report — Day 14

**Họ tên:** Hoang Lam Minh — 2A202603566
**Ngày chạy:** 2026-11-25 (giờ trong report là UTC)
**Môi trường:** Laptop Windows 10 Pro 21H2 · WSL2 Ubuntu 24.04.5 (8 vCPU, 6.1 GB RAM cấp cho WSL) · Python 3.12.3 · Docker Engine 29.10.1 + Compose v5.8.0 chạy trong WSL (không dùng Docker Desktop). Load generator và agent chạy trên cùng máy.

Số liệu gốc: `reports/*.json` (load test + eval, develop), `benchmark-results/scale_*_stats.csv` (Locust), ảnh trong `screenshots/`.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất ~120–180 ms + retrieval ~20 ms, p50 đo được ~190 ms. 500 ms cho ~2.5× headroom so với p50 mà người dùng vẫn thấy "tức thì"; trùng ngưỡng mặc định của `load_test.py` để CI dùng được exit code. Đặt theo p95 chứ không theo avg vì avg che tail latency (mục 2.2) |
| Error rate `/ask` | < 2 % | Mock có 1% lỗi upstream có chủ đích (`MOCK_ERROR_RATE=0.01`); 2% chừa biên cho dao động ngẫu nhiên, vượt 2% là có sự cố thật. (Alert `AgentHighErrorRate` dùng ngưỡng 5% / 1 phút để báo động mức nặng) |
| Accuracy trên golden dataset | ≥ 100 % (23/23), không regression | Dataset nhỏ, cố định; mỗi câu sai là một lỗi kiến thức cụ thể. Quality gate CI (`--min-accuracy 1.0 --baseline`) chặn merge nếu tụt dù 1 câu |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

Agent v2 (`AGENT_VERSION=v2 PORT=8002 python app.py`, 1 process uvicorn), mỗi lần 300 request.

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.15 | 190.6 | 225.7 | 1697.9 | 1.67% (5×503) | ✅ PASS |
| 10 | 39.43 | 193.8 | 223.4 | 1692.6 | 1.67% (5×503) | ✅ PASS |
| 50 | 118.95 | 206.5 | 270.8 | 1722.2 | 1.00% (3×503) | ✅ PASS |
| 100 | 53.39 | 416.4 | 3426.4 | 3679.6 | 0.00% | ❌ FAIL (p95) |

(`reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`)

**Nhận xét:** Từ 1 → 10 → 50, RPS tăng gần tuyến tính (4.15 → 39.43 → 118.95; ×9.5 rồi ×3.0) trong khi p50/p95 gần như không đổi. Lý do: mock LLM là `asyncio.sleep` (I/O-bound), một event loop phục vụ được nhiều request đồng thời; theo Little's Law RPS ≈ concurrency / latency ≈ 10 / 0.2 s ≈ 50 (đo được 39 vì có tail 1.7 s). Từ 50 → 100 thì **không còn tuyến tính mà còn giảm** (118.95 → 53.39 RPS) và p95 nhảy từ 271 ms lên 3426 ms → đã qua điểm bão hoà. Tôi chạy thêm một lần chẩn đoán ở concurrency 100 (10:45 UTC) và so sánh 2 phía (`reports/diag_c100_client.json`, `reports/diag_c100_server.json`): client đo p50 = 386.3 ms, **p95 = 3356.3 ms**, p99 = 3664.6 ms; nhưng **chính agent đo** (field `ms` trong log JSON của middleware, 297 request `/ask` status 200) chỉ có p50 = 193.9 ms, **p95 = 232.2 ms**, p99 = 1690.5 ms; trong lúc chạy kernel không có `TcpExtListenOverflows`, `TcpExtListenDrops` hay `TcpExtTCPSynRetrans` (đều 0, `somaxconn` 4096, backlog uvicorn 2048). Nghĩa là thời gian chờ nằm **ngoài handler của agent**: ở phía load generator (1 process Python mở 100 kết nối httpx cùng lúc, chạy chung máy với agent) và ở bước nhận/thiết lập kết nối, không phải ở LLM. Kết luận: trên máy này một instance phục vụ tốt tới ~50 request đồng thời; muốn đo cao hơn cần tách load generator sang máy khác và scale ngang agent.

### 2.2 Tail latency

v2 khởi động lại với từng giá trị `MOCK_SLOW_RATE`, `--requests 300 --concurrency 20` (`reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`).

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 191.6 | 222.0 | 240.8 |
| 0.03 (mặc định) | 227.9 | 232.9 | 1689.3 |
| 0.1 | 310.2 | 1683.0 | 1710.7 |

**Nhận xét:** Request "slow" bị cộng thêm 1500 ms. Với 3% request chậm, **p99 nhảy từ 241 → 1689 ms (×7)** trong khi avg chỉ tăng 36 ms (+19%) và p95 gần như không đổi (vì 3% < 5%, nhóm chậm nằm ngoài top 5%). Với 10% request chậm (> 5%), **p95 cũng nhảy lên 1683 ms** (×7.6) còn avg mới tăng 119 ms (+62%) — SLO p95 < 500 ms bị vi phạm (`exit code 1`). Percentile cao là chỉ số nhạy nhất với request chậm. SLO không nên đặt theo average vì average "pha loãng" nhóm chậm: avg 310 ms trông vẫn ổn trong khi cứ 10 người dùng thì 1 người chờ gần 2 giây; p95/p99 nói thẳng "x% người dùng chờ lâu hơn N ms".

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 57.2 | 109.0 | 116.7 |
| Tổng cost (USD) | 0.000406 | 0.000760 | 0.000817 |
| Cost / câu đúng (USD) | 0.00040575 | 0.00003619 | 0.00003553 |
| Latency p50 / p95 (ms) | 169.4 / 191.2 | 188.3 / 220.1 | 197.4 / 215.4 |
| Quality gate | FAIL (`min 80%`) | PASS (`min 80%`, baseline v1, 0 regression) | PASS (`min 100%`, baseline v2, 0 regression) |

(`reports/eval_v1.json`, `eval_v2.json`, `eval_v2_fixed.json`)

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `scaling-03` "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` (score 0.00 — retrieval không tìm thấy tài liệu nào nên agent trả câu chung chung) | Thêm entry `(["hpa", "kubernetes", "autoscal"], "Kubernetes HPA (Horizontal Pod Autoscaler) tự tăng giảm số pod dựa trên mức dùng CPU, memory hoặc custom metrics ...")` vào cuối `KNOWLEDGE_BASE` |
| `security-02` "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` (score 0.00, cùng nguyên nhân) | Thêm entry `(["jwt", "api key"], "API key là một chuỗi bí mật cố định, không tự hết hạn ... JWT là token có chữ ký số ... thời điểm hết hạn (exp) ...")` |

Thêm vào **cuối** danh sách để khi điểm khớp bằng nhau, tài liệu cũ vẫn thắng (sort ổn định). Trước khi chạy lại agent tôi kiểm tra offline bằng `retrieve_context()` trên cả 23 câu: chỉ đúng 2 câu trên đổi tài liệu được retrieve. Sau khi restart v2: `python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json` → 23/23, `Câu được cải thiện: 2`, `Câu bị regression: 0`, `Quality gate ... ✅ PASS`, exit code 0. Cùng phần sửa được chép sang `production/utils/mock_llm.py`.

**Quality gate trong CI:** `run_eval.py` trả **exit code 1** khi accuracy < `--min-accuracy` hoặc có regression so với `--baseline`. Thử: eval v1 với `--baseline reports/eval_v2.json` → accuracy 4.3%, 20 câu regression, **exit code 1**. Job `.github/workflows/quality-gate.yml` chạy trên mỗi pull request: cài requirements, bật agent v2 (`MOCK_ERROR_RATE=0` để không lỗi ngẫu nhiên), chạy `python eval/run_eval.py --url http://localhost:8000 --min-accuracy 1.0 --baseline reports/eval_v2_fixed.json`; exit code ≠ 0 → step fail → job fail → với branch protection "Require status checks to pass", PR không merge được.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:** v2 thêm bước retrieval nên prompt dài hơn: **tokens/câu ×1.9** (57.2 → 109.0), **tổng cost ×1.9**, latency p50 +19 ms, p95 +29 ms. Đổi lại accuracy từ 4.3% lên 91.3% (100% sau khi sửa KB). Tính theo **cost / câu trả lời đúng** thì v2 **rẻ hơn ~11 lần** (0.00040575 → 0.00003619 USD) vì v1 tốn tiền cho câu trả lời sai. So 2 phiên bản phải dùng cost/câu đúng chứ không phải cost/câu. Bản sửa KB tốn thêm 7% token (tài liệu mới dài hơn câu chung chung) nhưng cost/câu đúng vẫn giảm nhẹ.

---

## 4. Observability stack (production)

Stack: `docker compose -p k5-day14-observability up -d --build` (agent ×2 + Nginx + Prometheus + Grafana + Jaeger + Locust). Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals) — `screenshots/grafana-dashboard.png` (Locust 30 user, chụp 09:30 UTC)
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian) — `screenshots/jaeger-slow-trace.png`
- [x] Một trace lỗi trong Jaeger (có event `exception`) — `screenshots/jaeger-error-trace.png`
- [x] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing** — `screenshots/prometheus-alert-firing.png`

**Exercise 7.4 — đọc dashboard (Locust 30 user, 2 instance, số lấy bằng PromQL lúc 09:28 UTC):**

| Chỉ số | Giá trị |
|---|---|
| Traffic `/ask` | 18.58 req/s (instance `172.18.0.7`: 9.45, `172.18.0.8`: 9.25) |
| p50 / p95 / p99 / avg | 176 ms / 295 ms / 1854 ms / 241 ms |
| Error rate | 1.17% |
| Tokens/s | input 1461, output 778 |
| Cost burn | $3.63/giờ (5m) |
| Retrieval hit ratio | 100% |

1. **p95 ≈ 295 ms, avg ≈ 241 ms** (panel lúc chụp ảnh: p95 298 ms). avg và p95 gần nhau nhưng **p99 ≈ 1.85 s**: ~3% request bị mock làm chậm thêm 1.5 s → phân bố có đuôi dài; avg kéo lên một chút, p95 chưa thấy, chỉ p99 lộ ra.
2. **Nginx chia đều:** 9.45 vs 9.25 req/s (round-robin trên DNS `agent`).
3. **Input tokens/s > output tokens/s (1461 vs 778):** prompt gửi LLM = system prompt + tài liệu retrieve được + câu hỏi, dài hơn câu trả lời (câu trả lời chỉ là 1 đoạn tài liệu). Agent RAG luôn có input > output.
4. **`curl http://localhost:8080/metrics` → `HTTP/1.1 404 Not Found`** (trang lỗi HTML của nginx). Nginx chặn `location = /metrics { return 404; }` để không lộ thông tin nội bộ (route, lưu lượng, version, chi phí) ra internet; Prometheus vẫn scrape trực tiếp `http://agent:8000/metrics` trong mạng `obs_net` (tôi kiểm tra bằng `wget` từ container prometheus: trả metric bình thường).

### 4.2 Thí nghiệm "phá hệ thống"

Locust chạy nền 30 user suốt các thí nghiệm. Thời gian đo bằng script hỏi `GET /api/v1/alerts` mỗi 5 s, tính từ lúc `docker compose up -d agent` (hoặc `docker pause`) trả về.

Trạng thái trước thí nghiệm (baseline, 09:55 UTC): p50 177 ms, p95 296 ms, p99 1849 ms, avg 241 ms, error rate 1.5%, cost $2.02/giờ, tokens/s input 1405 / output 746, retrieval hit 100%. Lưu ý: ở tải 30 user, **`LLMCostBurnRateHigh` đã firing ngay cả khi hệ thống bình thường** (09:16–09:55, giá trị $2.0–3.6/giờ dao động quanh ngưỡng $2) → ngưỡng alert này quá thấp so với tải thật.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` (09:38:55) | `AgentHighErrorRate` (value 0.188) | pending sau 21 s, **firing sau 81 s** (`for: 1m`) | **Error rate** (stat chuyển đỏ, 19.40%) và **LLM calls theo kết quả** (đường `error` tăng lên ~4 req/s); error rate đo bằng PromQL 19.9% |
| B | `MOCK_SLOW_RATE=0.3` (09:56:54) | `AgentHighLatencyP95` (value 1.915 s) | pending sau 11 s, **firing sau 131 s** (`for: 2m`) | **Latency /ask — p50/p95/p99**: p95 296 ms → 1916 ms (×6.5), p99 1849 → 1983 ms, avg 241 → 637 ms, p50 177 → 220 ms; **LLM latency p95** 198 ms → 1917 ms (chỗ chậm là LLM) |
| C | `LLM_MODEL=mock-llm-large` (10:00:02) | `LLMCostBurnRateHigh` (value $16 → $32.7/giờ) | pending ngay (0 s, vì cost đã sát ngưỡng từ trước), **firing sau 131 s** (`for: 2m`) | **Cost burn rate (USD/giờ)**: $2.02 → $32.8/giờ (**×16.2**); **LLM latency p95** 198 → 497 ms (×2.5), p50 `/ask` 177 → 413 ms. Tokens/s gần như không đổi (1267/675) → tăng chi phí hoàn toàn do giá model |
| D | `docker pause k5-day14-observability-agent-1` (09:40:16, khi A đang bật) | `AgentInstanceDown` (instance `172.18.0.7:8000`) | pending sau 11 s, **firing sau 41 s** (`for: 30s`) | **Instances up** 2 → 1, **Requests/s theo instance** (một đường về 0, instance còn lại gánh ~18 req/s), bảng **Phiên bản đang chạy** còn 1 instance |
| E | `AGENT_VERSION=v1` (10:05:16, theo dõi 3 phút) | **Không có alert mới.** Lúc 10:08:16 chỉ còn `LLMCostBurnRateHigh` sót lại từ C (cửa sổ `rate(...[5m])` vẫn chứa dữ liệu model large: $14.3/giờ theo 5m nhưng chỉ $1.18/giờ theo 1m) | — | **Retrieval hit ratio** mất hẳn (v1 không gọi retrieval, panel không còn dữ liệu); **Tokens/s** giảm một nửa (input 1405 → 692, output 746 → 375); bảng **Phiên bản đang chạy** đổi sang `v1`. Latency còn **tốt hơn**: p50 177 → 152 ms, p95 296 → 199 ms; error rate 1.1% |

**Ảnh `prometheus-alert-firing.png`:** chụp lúc 10:12:08 UTC khi bật cùng lúc A + B (`MOCK_ERROR_RATE=0.2`, `MOCK_SLOW_RATE=0.3`, đổi lúc 10:08:56) rồi D (`docker pause` lúc 10:11:22) → nhóm `agent-slo` **FIRING (3)**: `AgentHighErrorRate` (firing sau 90 s, value 0.189), `AgentHighLatencyP95` (firing sau 146 s, value 1.906 s), `AgentInstanceDown` (firing 41 s sau khi pause, instance `172.18.0.7:8000`). Thời gian lần này khớp với khi làm riêng từng thí nghiệm (81 s / 131 s / 41 s) — chủ yếu do `for:` (1m / 2m / 30s) cộng 1–3 chu kỳ đánh giá 5 s và thời gian để `rate(...[1m])` vượt ngưỡng.

**Phân tích thêm thí nghiệm D (lỗi ở Nginx không có trong metric của agent):** trong 14 phút pause (09:40:16–09:54:37), log Nginx có **64 response 504** và 69 dòng `upstream timed out` — request bị Nginx gửi vào instance đang pause, chờ hết `proxy_read_timeout 10s` rồi trả 504. Cùng khoảng thời gian đó, `increase(http_requests_total{route="/ask"})` của agent chỉ có status `200` (12139) và `503` (3057, do thí nghiệm A), **không có 504 nào**: instance bị pause không trả lời được nên không bao giờ ghi metric cho các request đó, còn instance kia không hề thấy chúng. Metric chỉ phản ánh những gì chính service đó nhìn thấy → cần giám sát thêm ở tầng load balancer (5xx/504 của Nginx) hoặc synthetic check từ bên ngoài. Dùng `docker pause` (không phải `stop`) vì container bị pause vẫn còn trong DNS nên Prometheus vẫn scrape và thấy `up == 0`; container bị stop biến mất khỏi DNS, target biến mất chứ không "down", nên không có alert.

Ảnh dashboard trong lúc thí nghiệm: `screenshots/exp-AD-grafana.png` (A + D), `screenshots/exp-B-grafana.png`, `screenshots/exp-C-grafana.png`, `screenshots/exp-E-grafana.png`.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Với v1, mọi Golden Signal đều "xanh", thậm chí latency và cost còn đẹp hơn v2, nhưng câu trả lời sai: gọi thử `"Kubernetes HPA scale dựa trên những chỉ số nào?"` → `"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."` (`agent_version: v1`); eval ở mục 3 cho thấy v1 chỉ đúng 4.3% so với 100% của v2. Metric hạ tầng đo "nhanh/chậm, lỗi/không lỗi", không đo "đúng/sai". Cách phát hiện:

1. **Chặn trước khi deploy:** quality gate trong CI (`run_eval.py --min-accuracy 1.0 --baseline ...`, exit code 1 → chặn merge/deploy) — mục 3. Bản v1 sẽ fail với 20 câu regression.
2. **Giám sát chất lượng khi đang chạy:** chạy eval định kỳ (vd mỗi 15 phút) với golden dataset trên production/canary và đẩy accuracy thành metric `agent_eval_accuracy`, alert khi < 95%; hoặc LLM-as-judge chấm một mẫu nhỏ traffic thật.
3. **Metric gián tiếp (proxy) có alert:** retrieval hit ratio < 80%, tokens/câu giảm đột ngột so với baseline, `agent_info{version}` đổi ngoài lịch release.
4. **Tín hiệu từ người dùng:** tỉ lệ 👍/👎, tỉ lệ hỏi lại cùng câu, ticket hỗ trợ.

### 4.3 Debug bằng trace

Chọn trace chậm trong lúc tải ổn định (30 user, 2 instance; Jaeger API v3, `POST /ask`, sắp xếp theo thời gian giảm dần):

- `trace_id`: `4d1c72ff1d72585059d111fe7deaceb7`
- Tổng thời gian: 1779.8 ms (`POST /ask`, 09:31:35 UTC)
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (1692.6 ms, 95.1%) — `agent.run` 1716.5 ms, `agent.retrieve_context` chỉ 23.5 ms (1.3%). Attributes: `gen_ai.request.model=mock-llm-small`, `gen_ai.usage.input_tokens=74`, `gen_ai.usage.output_tokens=37`. Chậm do LLM (request rơi vào nhóm "slow" +1500 ms của mock), không phải do code agent.
- Dòng log tương ứng (grep theo `trace_id` trong `docker compose -p k5-day14-observability logs agent`):

```json
{"ts": "2026-11-25T09:31:37.657263+00:00", "level": "INFO", "service": "observable-agent", "instance": "11ed2126b2ff", "event": "llm_call", "trace_id": "4d1c72ff1d72585059d111fe7deaceb7", "span_id": "a44ff9a9dfa22749", "request_id": "b759fdc9866e", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 74, "output_tokens": 37, "cost_usd": 3.3e-05, "llm_ms": 1691.9}
{"ts": "2026-11-25T09:31:37.658124+00:00", "level": "INFO", "service": "observable-agent", "instance": "11ed2126b2ff", "event": "http_request", "trace_id": "4d1c72ff1d72585059d111fe7deaceb7", "span_id": "a44ff9a9dfa22749", "request_id": "b759fdc9866e", "method": "POST", "route": "/ask", "status": 200, "ms": 1779.0}
```

`llm_ms: 1691.9` trong log khớp span `chat mock-llm-small` 1692.6 ms trong Jaeger.

**Trace lỗi** (`screenshots/jaeger-error-trace.png`): `trace_id` `dcbcd2d64a9b6dd9a75e1f5dbf074f1d`, `POST /ask` → 503, 104 ms. Span `chat mock-llm-small` (75.7 ms) có status ERROR, attribute `error.type=upstream_unavailable`, event `exception`: `utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)` kèm stacktrace. Log cùng `trace_id`:

```json
{"ts": "2026-11-25T09:32:08.807171+00:00", "level": "INFO", "service": "observable-agent", "instance": "fdf946f4e530", "event": "llm_error", "trace_id": "dcbcd2d64a9b6dd9a75e1f5dbf074f1d", "span_id": "f12f348b84c22feb", "request_id": "13aa1717005f", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

**Hai trace đáng chú ý khác:**
- Trace dài nhất trong 5 phút đó (`46c58c6666b1fe5bd84c037d43415e4b`, 1914.4 ms): span dài nhất vẫn là `chat mock-llm-small` nhưng chỉ 753.7 ms (39.4%), trong khi log ghi `llm_ms: 157.4` (latency mock tự tính). Phần chênh nằm ngoài lời gọi LLM: request phải chờ event loop/CPU (container agent bị giới hạn 0.5 CPU). Trace không chỉ nói "chậm ở đâu" mà còn cho thấy khi tổng các span con không cộng lại bằng span cha thì thời gian bị mất ở hàng đợi.
- Trace dài nhất trong 10 phút (`07456ffc45c913fb6e61324f828cf706`, 4692.8 ms, 09:22:46) rơi vào lúc vừa scale từ 1 lên 3 instance: `agent.run` chỉ 802 ms (17%), ~3.9 s còn lại là thời gian request nằm chờ trong instance vừa khởi động.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

`docker compose -p k5-day14-observability up -d --scale agent=N` rồi `docker compose -p k5-day14-observability run --rm --no-deps locust ... --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_N`. Số liệu: dòng **Aggregated** trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv` (dòng `POST /ask` ghi trong ngoặc).

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 63.33 (`/ask` 57.75) | 200 | 820 (`/ask` 900) | 1700 | 70 / 7520 (0.93%) |
| 3 | 63.97 (`/ask` 58.50) | 200 | 480 (`/ask` 560) | 1700 | 77 / 7565 (1.02%) |

Failures đều là 503 từ mock LLM (`MOCK_ERROR_RATE=0.01`), không có lỗi do quá tải. Prometheus xác nhận suốt mỗi lần chạy có đúng N target `up` (`count(up{job="agent"} == 1)`).

**Giải thích kết quả:**
- **RPS không đổi** (63.3 → 64.0): Locust là closed-loop — 100 user, mỗi user nghỉ 0.5–2 s (trung bình 1.25 s) giữa 2 request, nên tải tối đa ≈ 100 / (1.25 + ~0.27) ≈ 66 req/s bất kể có bao nhiêu instance. Thêm instance không tạo thêm tải.
- **p95 cải thiện rõ** (820 → 480 ms; `/ask` 900 → 560 ms): với 1 instance (giới hạn 0.5 CPU, 1 process uvicorn), Prometheus thấy 7–16 request đang xử lý cùng lúc, CPU ~0.27–0.30 core và p95 server-side ~0.68–0.70 s → request phải xếp hàng trong event loop. 3 instance chia tải (mỗi instance ~20 req/s, đều nhau); 1 phút đầu p95 server-side còn 0.9–1.9 s do 2 instance mới vừa khởi động, sau đó về ~0.30 s.
- **p50 và p99 không đổi** (200 ms và 1700 ms): p50 chính là latency của LLM (LLM p95 ≈ 198 ms ở cả 2 lần), p99 là tail +1500 ms do mock LLM tạo ra. Scale ngang không làm LLM nhanh hơn → **nút thắt còn lại là LLM upstream**; muốn giảm p99 phải xử lý ở tầng LLM (timeout + retry/hedging, model nhanh hơn, cache câu trả lời), không phải thêm instance.
- **Bài học khi đo:** lần đầu tôi chạy `docker compose run --rm locust ...` không có `--no-deps`, compose tự bật lại dependency `agent` theo `replicas: 2` ngay giữa lần đo "1 instance": instance mới khởi động chưa xong đã nhận traffic → 504 rồi Nginx đánh dấu upstream hỏng → 937 lỗi "no live upstreams" (502). Kết quả đó bị loại, đo lại với `--no-deps`.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Percentile mới phản ánh trải nghiệm người dùng.** 3% request chậm làm p99 tăng ×7 nhưng avg chỉ +19%; SLO và alert phải đặt theo p95/p99. Và phải so client-side với server-side: ở concurrency 100 client thấy p95 3356 ms còn agent tự đo chỉ 232 ms (`reports/diag_c100_*.json`) — chỗ chậm không nằm ở agent.
  2. **Metric hạ tầng không bắt được lỗi chất lượng.** Đổi sang v1 (accuracy 91% → 4%) mà không alert nào kêu; chỉ eval trên golden dataset (và metric gián tiếp như retrieval hit ratio) mới thấy. Eval phải là quality gate trong CI, so sánh bằng cost/câu đúng.
  3. **Metric, log, trace bổ sung cho nhau và phải liên kết được.** Alert cho biết "có chuyện", dashboard cho biết "chuyện gì, từ lúc nào", trace cho biết "chậm/lỗi ở bước nào", và `trace_id` trong log JSON nối 3 thứ lại. Đồng thời mỗi tín hiệu chỉ thấy phần của chính nó: 504 ở Nginx không có trong metric của agent.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - Metric ở tầng Nginx/LB (tỉ lệ 5xx/504, upstream response time) + synthetic check từ bên ngoài gọi `/ask` mỗi phút — bắt được loại lỗi như thí nghiệm D mà metric agent không thấy.
  - Alert **SLO burn-rate** nhiều cửa sổ (vd 5m và 1h) thay cho ngưỡng cứng, để báo theo tốc độ tiêu error budget.
  - Alert `retrieval hit ratio < 80%` và thay đổi `agent_info{version}` bất thường; chạy eval định kỳ (canary trên golden dataset) trên production và đẩy accuracy thành metric `agent_eval_accuracy` để alert khi tụt.
  - Ngưỡng `LLMCostBurnRateHigh` cần chỉnh theo tải thật: ở 30 user model small đã $2.0–3.6/giờ nên alert kêu cả khi hệ thống bình thường (mục 4.2) → dùng ngưỡng theo ngân sách ngày (vd dự báo vượt `DAILY_BUDGET_USD`) hoặc cost/request.
  - Saturation: CPU throttling của container (`container_cpu_cfs_throttled_seconds_total`) và độ dài hàng đợi, vì trace cho thấy thời gian chờ event loop/CPU khi container bị giới hạn 0.5 CPU.
