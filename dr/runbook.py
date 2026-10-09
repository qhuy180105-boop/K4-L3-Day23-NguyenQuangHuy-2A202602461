"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr import health_checker as hc  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
CHAOS_LOG = pathlib.Path("chaos/chaos-events.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
EDGE_URL = "http://127.0.0.1:8080"


def step(n: int, name: str, **kw):
    """Ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "step": n,
        "name": name,
        **kw,
    }
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"RUNBOOK [{n}/7] {name}:", json.dumps(rec))
    return rec


def confirm(auto: bool, msg: str) -> bool:
    """auto=True -> True; ngược lại hỏi y/N."""
    if auto:
        return True
    try:
        ans = input(f"{msg} [y/N]: ").strip().lower()
        return ans == "y"
    except Exception:
        return False


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """7 bước runbook ứng cứu sự cố."""
    t_start = time.time()

    # Đọc t_outage từ chaos log trước để biết mốc sự cố
    t_outage = None
    if CHAOS_LOG.exists():
        for line in reversed(CHAOS_LOG.read_text(encoding="utf-8").splitlines()):
            if line.strip():
                try:
                    c = json.loads(line)
                    if c.get("action") == "kill" and c.get("region") == primary:
                        t_outage = c.get("ts")
                        break
                except Exception:
                    pass

    # 1. Xác nhận outage: probe cả 2 region và chờ health checker phát hiện UNHEALTHY (tránh t_cutover < t_detect)
    health_log = pathlib.Path("reports/health-events.jsonl")
    detected = False
    deadline = time.time() + 45.0
    primary_ready, primary_reason = hc.probe(primary, timeout=1.0)
    target_ready, target_reason = hc.probe(target, timeout=1.0)

    while time.time() < deadline:
        primary_ready, primary_reason = hc.probe(primary, timeout=1.0)
        target_ready, target_reason = hc.probe(target, timeout=1.0)
        if health_log.exists():
            for line in reversed(health_log.read_text(encoding="utf-8").splitlines()):
                if line.strip():
                    try:
                        ev = json.loads(line)
                        if ev.get("event") == "state_change" and ev.get("to") == "UNHEALTHY" and ev.get("region") == primary:
                            if t_outage is None or ev.get("ts", 0) >= (t_outage - 1.0):
                                detected = True
                                break
                    except Exception:
                        pass
        if detected:
            break
        if not primary_ready and not health_log.exists():
            break
        time.sleep(0.5)

    step(
        1,
        "xac_nhan_outage",
        primary=primary,
        primary_ready=primary_ready,
        primary_reason=primary_reason,
        target=target,
        target_ready=target_ready,
        target_reason=target_reason,
        health_check_detected=detected,
    )

    t_notify = time.time()
    notification_lag_s = round(t_notify - t_outage, 2) if t_outage else None
    confirmed = confirm(auto, f"Xác nhận thực hiện failover từ region {primary} sang region {target}?")
    step(
        2,
        "thong_bao_incident",
        primary=primary,
        target=target,
        t_outage=t_outage,
        t_notify=t_notify,
        notification_lag_s=notification_lag_s,
        confirmed=confirmed,
    )

    if not confirmed:
        step(2, "thong_bao_incident_aborted", reason="operator_declined")
        return {"ok": False, "aborted": True, "reason": "operator_declined"}

    # 3. Scale GPU pool: gọi failover() đúng 1 lần duy nhất
    fo_result = fo.failover(target=target, backend=backend, wait=60.0)
    step(3, "scale_gpu_pool", failover_result=fo_result)
    if not fo_result.get("ok"):
        return {"ok": False, "error": "failover_failed", "details": fo_result}

    # 4. Verify state replica: đọc kết quả từ fo_result
    target_stats = fo.state_of(target)
    step(
        4,
        "verify_state_replica",
        target=target,
        rpo_seconds=fo_result.get("rpo_seconds"),
        docs_lost=fo_result.get("docs_lost"),
        embed_model_version=fo_result.get("embed_model_version"),
        target_stats=target_stats,
    )

    # 5. DNS cutover: kiểm tra edge đã trỏ về target
    active_reg = None
    edge_state = None
    active_path = pathlib.Path("edge/active_region")
    if active_path.exists():
        active_reg = active_path.read_text(encoding="utf-8").strip()
    try:
        r = httpx.get(f"{EDGE_URL}/edge/state", timeout=2.0)
        if r.status_code == 200:
            edge_state = r.json()
    except Exception:
        pass

    cutover_ok = (active_reg == target)
    step(5, "dns_cutover", target=target, active_region=active_reg, cutover_ok=cutover_ok, edge_state=edge_state)

    # Chờ edge proxy cập nhật cache sang target region
    for _ in range(12):
        try:
            r = httpx.get(f"{EDGE_URL}/edge/state", timeout=1.0)
            if r.status_code == 200 and r.json().get("active_region") == target:
                break
        except Exception:
            pass
        time.sleep(0.5)

    # 6. Verify golden signals: 10 request thật
    latencies = []
    success_count = 0
    total_reqs = 10
    infer_url = f"{EDGE_URL}/v1/infer"

    for i in range(total_reqs):
        t0 = time.time()
        try:
            resp = httpx.get(infer_url, params={"q": f"kiem tra golden signal #{i+1}"}, timeout=3.0)
            lat = round((time.time() - t0) * 1000, 1)
            latencies.append(lat)
            if resp.status_code == 200:
                success_count += 1
        except Exception:
            lat = round((time.time() - t0) * 1000, 1)
            latencies.append(lat)

    latencies_sorted = sorted(latencies)
    p95_idx = int(0.95 * len(latencies_sorted))
    p95_latency_ms = latencies_sorted[min(p95_idx, len(latencies_sorted) - 1)] if latencies_sorted else 0.0
    error_rate = round(1.0 - (success_count / total_reqs), 2)

    step(
        6,
        "verify_golden_signals",
        requests_total=total_reqs,
        requests_success=success_count,
        error_rate=error_rate,
        p95_latency_ms=p95_latency_ms,
        latencies_ms=latencies,
    )

    # 7. Post incident: tổng thời gian và lệnh đo RTO
    elapsed_s = round(time.time() - t_start, 2)
    step(
        7,
        "post_incident",
        elapsed_s=elapsed_s,
        measure_cmd="python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300",
    )

    return {
        "ok": True,
        "primary": primary,
        "target": target,
        "elapsed_s": elapsed_s,
        "rpo_seconds": fo_result.get("rpo_seconds"),
        "docs_lost": fo_result.get("docs_lost"),
        "p95_latency_ms": p95_latency_ms,
        "error_rate": error_rate,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
