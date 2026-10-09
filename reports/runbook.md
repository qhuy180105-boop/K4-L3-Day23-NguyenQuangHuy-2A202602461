# Runbook 1 trang — Region chính down (On-Call Runbook)

Runbook này được thiết kế để kỹ sư trực vận hành (On-Call SRE) có thể thực thi chính xác vào lúc 3h sáng trong tình huống Region chính (Region A) gặp sự cố. Mọi bước đều có lệnh copy-paste trực tiếp và dấu hiệu nhận biết hoàn thành rõ ràng.

## Quy trình 7 bước ứng cứu sự cố (Incident Response)

| # | Bước | Lệnh thực thi (Copy-Pasteable) | Biết là xong khi (Tín hiệu hoàn thành) | Ai làm |
|---|---|---|---|---|
| 1 | **Xác nhận outage** | `python -c "import httpx; [print(f'Probe {i+1}:', httpx.get('http://127.0.0.1:8001/readyz', timeout=2.0).status_code) for i in range(3)]"` | Thấy 3 lần liên tiếp timeout hoặc lỗi kết nối (HTTP 503 / ConnectError / ReadTimeout). Health checker ghi nhận `UNHEALTHY` trên Region A. | On-call SRE |
| 2 | **Mở incident + Bấm giờ RTO** | `python -c "import json, time; open('reports/runbook-run.jsonl', 'a').write(json.dumps({'ts': time.time(), 'step': 2, 'event': 'incident_declared', 'status': 'ACTIVE'}) + '\n'); print('INCIDENT DECLARED')"` | Tạo incident ticket trên PagerDuty/Slack (`#incident-ai-serving`), mốc thời gian `ts` được ghi lại làm điểm bắt đầu đồng hồ ứng cứu. | Incident Commander (IC) |
| 3 | **Restore state ở region phụ** | `python state/snapshot.py get --region b --backend fs` | Output JSON trả về `"restored_at": <ts>`, file `state/region-b/vectors.sqlite` và model weights `model.bin` được khôi phục thành công. | On-call SRE |
| 4 | **Scale pool warm → full** | `python -c "open('state/region-b/pool_state', 'w').write('full'); import httpx, time; [exit(0) if httpx.get('http://127.0.0.1:8002/readyz', timeout=1.5).status_code==200 else time.sleep(0.5) for _ in range(30)]; exit(1)"` | Lệnh thoát với mã code 0. Endpoint `/readyz` của Region B trả về HTTP 200 OK (`ready: true`). | Platform Engineer |
| 5 | **DNS/LB cutover** | `python -c "open('edge/active_region', 'w').write('b'); import httpx; print(httpx.get('http://127.0.0.1:8080/edge/state').json())"` | Output trả về `active_region: "b"`. Edge proxy hoàn tất cập nhật định tuyến sang Region B sau chu kỳ DNS TTL. | Network / Traffic Engineer |
| 6 | **Verify golden signals** | `python -c "import httpx, time; resps = [httpx.get('http://127.0.0.1:8080/v1/infer?q=check').status_code for _ in range(10)]; print('Success rate:', resps.count(200)/10); assert resps.count(200) == 10"` | 10/10 requests trả về HTTP 200 OK. Error rate = 0%, latency p95 < 500ms, upstream trả về `[b] ...`. | On-call SRE |
| 7 | **Đo RTO + Postmortem** | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | Output trả về `valid: true` và `rto_verdict: PASS` (RTO $\le$ 300s). Tạo draft postmortem trong `reports/postmortem.md`. | Incident Commander |

---

## Quy trình Rollback (Failback về Region A)

> **CẢNH BÁO CHỐNG FLAPPING (§4 Anti-Patterns):**
> Tuyệt đối không tự động trả traffic về Region A ngay khi Region A vừa có tín hiệu sống lại. Chuyển đổi hai chiều liên tục khi mạng chập chờn sẽ gây gián đoạn kép (double outage) và phân mảnh dữ liệu (split-brain).

### Điều kiện tiên quyết để Failback (All must be TRUE):
1. **Region A hoạt động ổn định $\ge$ 30 phút liên tục**: Endpoint `http://127.0.0.1:8001/readyz` trả về HTTP 200 liên tục không ngắt quãng trong tối thiểu 30 phút.
2. **Đồng bộ dữ liệu ngược (Reverse Replication B $\rightarrow$ A) hoàn tất**: Toàn bộ dữ liệu mới được ghi nhận tại Region B trong thời gian outage đã được replicate và apply thành công về Region A (`docs_lost = 0`).
3. **Model weights và index hash đồng nhất**: Kiểm tra checksum của model weights và vector count giữa Region A và Region B khớp 100%.
4. **Quyền quyết định (Authority)**: Bắt buộc phải có sự phê duyệt đồng thuận (dual-signoff) từ **Incident Commander (IC)** và **Platform Lead**. On-call SRE không được tự ý thực hiện rollback đơn phương.

### Lệnh thực hiện Rollback:
```bash
# 1. Đồng bộ snapshot từ Region B sang Region A
python state/snapshot.py put --region b --backend fs
python state/snapshot.py get --region a --backend fs

# 2. Đảm bảo GPU pool Region A ở trạng thái full và ready
python -c "open('state/region-a/pool_state', 'w').write('full')"
curl -s http://127.0.0.1:8001/readyz

# 3. Chuyển traffic edge về Region A
python -c "open('edge/active_region', 'w').write('a')"
curl -s http://127.0.0.1:8080/edge/state

# 4. Kiểm tra golden signals sau rollback
curl -s "http://127.0.0.1:8080/v1/infer?q=verify_rollback"
```
