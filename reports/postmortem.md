# Postmortem — DR Drill Lab 23

Theo đúng template §4 "Sau Failover: Blameless Postmortem". Blameless: câu hỏi trọng tâm là "hệ thống/process nào cho phép chuyện này xảy ra và cơ chế nào giúp hồi phục an toàn", không nhằm quy trách nhiệm cá nhân.

## 1. Timeline sự cố (Mọi dòng đều có evidence path:line)

| ISO time (UTC) | Sự kiện | Chi tiết | Evidence |
|---|---|---|---|
| `2026-10-09T04:44:41` | Outage bắt đầu | Region A bị ngắt kết nối mạng giả lập (`mode=netblock`), cổng 8001 không phản hồi request. | `chaos/chaos-events.jsonl:3` |
| `2026-10-09T04:44:43` | User đầu tiên bị ảnh hưởng | Request seq 25 gửi qua Edge Proxy nhận HTTP 503 (`ReadTimeout`), độ trễ 2169.3ms (+2.2s từ t0). | `reports/drill-2-withdr.jsonl:26` |
| `2026-10-09T04:44:56` | Health check alert | Health checker ghi nhận 3 lần probe liên tiếp thất bại trên Region A, chuyển trạng thái sang `UNHEALTHY` (+14.9s từ t0). | `reports/health-events.jsonl:4` |
| `2026-10-09T04:44:57` | Operator confirm cutover | Kỹ sư vận hành xác nhận kích hoạt quy trình failover từ Region A sang Region B qua Runbook tự động. | `reports/runbook-run.jsonl:2` |
| `2026-10-09T04:45:04` | DNS/LB cutover hoàn tất | Region B hoàn tất nạp snapshot & warmup GPU (6.26s), con trỏ `edge/active_region` chuyển sang `b`. | `reports/failover-events.jsonl:21` |
| `2026-10-09T04:45:05` | Resolved (Hồi phục hoàn toàn) | Request seq 35 gửi qua Edge được Region B xử lý thành công (HTTP 200 OK, latency 232.4ms, +24.2s từ t0). | `reports/drill-2-withdr.jsonl:36` |

---

## 2. RTO/RPO đo được vs mục tiêu — Gap ở bước nào?

- **RTO (Recovery Time Objective):**
  - Mục tiêu: $\le$ 300.0s (5 phút)
  - Đo được: **24.2s**
  - Gap: Đạt trước hạn **275.8s** (vượt chỉ tiêu SLA đề ra)
- **RPO (Recovery Point Objective):**
  - Mục tiêu: $\le$ 300.0s (5 phút)
  - Đo được: **2.0s** (**1** document bị mất do lag replication)
  - Gap: Đạt trước hạn **298.0s**
- **Bước tốn nhiều giây nhất:**
  - **Health check phát hiện outage (`14.9s` / `24.2s` tổng cộng, chiếm ~61.6% RTO)**.
  - *Vì sao?* Health checker áp dụng quy tắc anti-flapping: bắt buộc phải có `threshold = 3` lần thăm dò lỗi liên tiếp với `interval = 5.0s`. Do đó detection floor trên lý thuyết là $5.0 \times 3 = 15.0s$. Đây là thời gian chờ bắt buộc để đảm bảo sự cố là thật, tránh phản ứng quá nhạy trước các biến động mạng nhất thời.

---

## 3. Root Cause Analysis (5 Whys)

*Câu hỏi cốt lõi: Nếu đây là một sự cố hạ tầng thực tế trong production, các mắt xích kỹ thuật nào sẽ quyết định sự thành bại của runbook?*

1. **Tại sao user nhận mã lỗi HTTP 503 khi gửi inference request?**
   - Do Region A bị mất kết nối mạng đột ngột (network partition / AZ failure), khiến Edge proxy không thể nhận phản hồi từ upstream inference server trong thời gian timeout.
2. **Tại sao hệ thống không chuyển traffic sang Region B ngay lập tức?**
   - Do Region B đang được duy trì ở cấu hình Cold/Warm standby (chưa nạp đầy đủ weights và vector database) nhằm tiết kiệm chi phí tài nguyên điện toán đám mây.
3. **Tại sao cần 14.9s hệ thống mới bắt đầu thực hiện failover?**
   - Do cơ chế phát hiện lỗi cần tích lũy đủ 3 lần probe liên tiếp thất bại để phân biệt giữa mạng suy hao tạm thời (transient glitch) và sự cố sập cụm thực sự (hard outage).
4. **Tại sao lại có 1 tài liệu (doc) bị mất khi khôi phục snapshot ở Region B?**
   - Do chu kỳ replication vector database diễn ra định kỳ (mỗi 30s) theo mô hình bất đồng bộ (asynchronous replication), dẫn đến khoảng trễ 2.0 giây giữa bản ghi mới nhất tại primary và bản snapshot gần nhất trên object store.
5. **Tại sao hệ thống có thể khôi phục dịch vụ an toàn trong 24.2s mà không gặp lỗi 503 kép?**
   - Do quy trình failover tuân thủ nghiêm ngặt thứ tự 5 bước: chỉ cập nhật DNS/LB cutover sau khi Region B đã hoàn tất khôi phục state, warm-up GPU pool và endpoint `/readyz` trả về HTTP 200 OK.

---

## 4. Action Items (Kế hoạch hành động cải tiến)

| # | Action Item | Owner | Deadline | Giảm RTO/RPO bao nhiêu giây |
|---|---|---|---|---|
| 1 | **Tối ưu tần suất health check**: Giảm `interval` từ 5s xuống 3s, giữ `threshold = 3` (detection floor giảm từ 15s xuống 9s). Thêm adaptive exponential backoff khi phát hiện chập chờn. | SRE Team | 2026-10-20 | Giảm RTO khoảng **6.0s** |
| 2 | **Chuyển sang cơ chế Replication CDC (Change Data Capture)**: Thay thế batch snapshot 30s bằng streaming replication WAL/event-driven qua Kafka/Debezium sang Region B. | Data Platform | 2026-11-01 | Giảm RPO về $\le$ **0.5s** (docs lost = 0) |
| 3 | **Pre-warm model weights trên GPU standby**: Duy trì weights model trong VRAM của Region B ở chế độ sleep context, chỉ nạp vector index SQLite khi failover. | AI Infra Team | 2026-10-25 | Giảm RTO khoảng **4.0s** ở bước warm-up |
| 4 | **Tích hợp Anycast Route53 ARC / Cloudflare failover**: Loại bỏ hoàn toàn độ trễ DNS TTL cache bằng cách định tuyến L4/L7 Anycast IP. | Network Team | 2026-11-10 | Giảm RTO khoảng **1.6s** |

---

## 5. Ba câu hỏi bắt buộc trả lời (§5 Analysis)

### 1. `interval × threshold` của bạn là bao nhiêu giây? Nó chiếm bao nhiêu % RTO?
- Với cấu hình `interval = 5.0s` và `threshold = 3`, sàn phát hiện sự cố trên lý thuyết là:
  $$\text{Detection Floor} = 5.0\text{s} \times 3 = 15.0\text{s}$$
- Trong thực tế đo đạc từ log:
  $$t_{\text{detect}} - t_0 = 14.9\text{s}$$
- Tỷ lệ chiếm dụng trong tổng RTO đo được (24.2s):
  $$\frac{14.9\text{s}}{24.2\text{s}} \times 100\% \approx 61.6\%$$
- **Nhận xét:** Thời gian phát hiện sự cố chiếm hơn 60% tổng thời gian phục hồi của toàn bộ hệ thống.

### 2. Nếu hạ interval xuống 1s, RTO giảm mấy giây — và bạn trả giá gì (§4 flapping)?
- **Mức giảm RTO:** Nếu giảm interval xuống 1.0s (với threshold = 3), detection floor giảm từ 15s xuống $1.0\text{s} \times 3 = 3.0\text{s}$. Do đó, RTO sẽ giảm khoảng **12.0 giây** (tổng RTO giảm xuống còn khoảng ~12.2s).
- **Cái giá phải trả (Trade-off & Flapping risk):**
  - **Hiện tượng Flapping:** Nếu mạng internet công cộng hoặc liên vùng bị nghẽn nhẹ (jitter / packet loss kéo dài 3-4 giây), health checker sẽ kết luận sai rằng Region A đã chết và lập tức kích hoạt failover sang Region B. Khi mạng bình phục 5 giây sau đó, hệ thống lại đòi chuyển ngược về.
  - **Hậu quả:** Flapping làm gián đoạn liên tục kết nối của người dùng (TCP resets, 503 bursts), gây lãng phí tài nguyên spin-up GPU pool, và đặc biệt nguy hiểm là nguy cơ **Split-Brain** (hai region cùng ghi dữ liệu độc lập, dẫn đến xung đột dữ liệu không thể cứu vãn).

### 3. Nếu outage kéo dài 6 giờ và region chính mất dữ liệu vĩnh viễn, `docs_lost` của bạn có nghĩa gì với khách hàng?
- Trong bài drill này, `docs_lost = 1` đại diện cho 1 bản ghi/ticket khách hàng bị mất hoàn toàn do chưa kịp đồng bộ sang bản snapshot trước khi Region A bị cô lập.
- **Ý nghĩa đối với khách hàng trong kịch bản Region chính mất dữ liệu vĩnh viễn:**
  - Đối với khách hàng cuối, `docs_lost` đồng nghĩa với việc thông tin giao dịch, câu hỏi hỗ trợ hoặc dữ liệu vừa cập nhật trong 2 giây trước khi sập sàn **đã biến mất vĩnh viễn** khỏi cơ sở tri thức của hệ thống. Khách hàng sẽ phải nhập lại dữ liệu hoặc nhận câu trả lời không chính xác từ trợ lý AI.
  - Nếu hệ thống chạy trong môi trường tài chính / ngân hàng hoặc y tế (clinical trials), việc mất tài liệu dù chỉ 1 bản ghi cũng có thể vi phạm các quy định tuân thủ pháp lý nghiêm ngặt (Audit compliance, SLA vi phạm bồi thường).
  - Vì vậy, đối với dữ liệu quan trọng, kiến trúc Disaster Recovery bắt buộc phải nâng cấp lên cơ chế đồng bộ đồng thời (Synchronous replication) hoặc ghi nhận Multi-Region Raft/Paxos consensus để đảm bảo Zero Data Loss ($RPO = 0$).
