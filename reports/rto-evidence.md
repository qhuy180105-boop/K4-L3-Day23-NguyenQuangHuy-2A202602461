# RTO/RPO Evidence — Lab 23

Quy tắc duy nhất: mỗi con số ở đây phải trỏ được về **một dòng log thật**
(`đường/dẫn.jsonl:số_dòng`). `pytest tests/test_rto_evidence.py` sẽ mở từng file ra kiểm tra.
Con số không có evidence = trượt, bất kể các phần khác.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---|---|---|
| t_outage | 2026-10-09T04:11:10 | chaos kill | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | +2.1s | dòng `ok:false` đầu tiên sau t_outage | `reports/drill-1-nodr.jsonl:13` |
| Request thành công sau đó | không có | không có dòng `ok:true` nào sau t_outage | `reports/measure-drill-1.json` |
| RTO | NO_RECOVERY | `tools/measure_rto.py` | `reports/measure-drill-1.json` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---|---|---|
| t_outage (mốc 0) | 0s | `action:kill` | `chaos/chaos-events.jsonl:3` |
| User thấy lỗi đầu tiên | +2.2s | dòng `ok:false` đầu | `reports/drill-2-withdr.jsonl:26` |
| Health check phát hiện | +14.9s | `to:UNHEALTHY, region:a` | `reports/health-events.jsonl:4` |
| Snapshot restore xong | +16.3s | `step:2_restore_snapshot` | `reports/failover-events.jsonl:18` |
| Region phụ ready | +22.6s | `step:4_wait_ready` | `reports/failover-events.jsonl:20` |
| DNS cutover | +22.6s | `step:5_dns_cutover` | `reports/failover-events.jsonl:21` |
| **RTO đo được** | +24.2s | dòng `ok:true` đầu sau lỗi | `reports/drill-2-withdr.jsonl:36` |

| Chỉ số | Đo được | Mục tiêu (slide §1) | Verdict |
|---|---|---|---|
| RTO — Inference API | 24.2s | 300s (5 phút) | PASS |
| RPO — Vector DB | 2.0s / 1 doc | 300s (5 phút) | PASS |

## 3. RTO của tôi gồm những gì (bắt buộc — đây là phần chấm điểm hiểu bài)

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào |
|---|---|---|---|
| Health-check detect floor | 15.0s | `interval_s × threshold` trong `reports/health-events.jsonl:4` (interval 5.0s × threshold 3) | Giảm interval xuống 2s hoặc threshold xuống 2 lần probe (chấp nhận đánh đổi tăng nguy cơ false alarm nếu mạng chập chờn) |
| Snapshot restore | 0.0s | `2_restore_snapshot` → `3_scale_pool` trong `reports/failover-events.jsonl:18` | Tối ưu tốc độ transfer artifact từ object storage, sử dụng incremental backup thay vì copy full database |
| GPU pool warm-up | 6.3s | `waited_s` ở `4_wait_ready` trong `reports/failover-events.jsonl:20` | Pre-warm GPU pool ở trạng thái warm cao (pre-load weights và keep GPU context), chỉ load vector index khi failover |
| DNS/LB TTL cache | 1.6s | t_recovered − t_cutover (`reports/drill-2-withdr.jsonl:36` vs `reports/failover-events.jsonl:21`: 24.2s − 22.6s) | Giảm DNS TTL từ 5s xuống 1s-2s, hoặc sử dụng Global Anycast Load Balancer (Cloudflare/Route53 ARC) thay vì purely DNS TTL caching |
