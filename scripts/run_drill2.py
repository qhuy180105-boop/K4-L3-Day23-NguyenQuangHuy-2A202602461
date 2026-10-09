"""Script chạy Drill 2 (có DR) tự động trên Windows/bare mode."""
import json
import os
import pathlib
import subprocess
import sys
import time

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    root = pathlib.Path(__file__).resolve().parent.parent
    os.chdir(root)
    print("=== CHUAN BI DRILL 2 ===")
    
    # Đặt active_region là 'a'
    pathlib.Path("edge/active_region").write_text("a", encoding="utf-8")
    
    # 1. Bật ingest và replicate chạy ngầm
    print("1. Khoi dong ingest (region a) va replicate (every 30s)...")
    p_ingest = subprocess.Popen([sys.executable, "state/ingest.py", "--region", "a", "--rate", "0.5", "--duration", "150"])
    p_replicate = subprocess.Popen([sys.executable, "state/replicate.py", "--every", "30", "--duration", "150", "--backend", "fs"])
    
    # 2. Chờ 5s cho vòng replication đầu tiên hoàn thành
    print("2. Cho 5s cho replication cycle dau tien...")
    time.sleep(5)
    
    # 3. Bật traffic generator và health checker
    print("3. Khoi dong loadgen (duration 80s) va health_checker (interval 5s, threshold 3)...")
    p_traffic = subprocess.Popen([
        sys.executable, "loadgen/traffic.py",
        "--duration", "80", "--rps", "2",
        "--out", "reports/drill-2-withdr.jsonl"
    ])
    p_health = subprocess.Popen([
        sys.executable, "dr/health_checker.py",
        "--interval", "5", "--threshold", "3", "--duration", "80",
        "--out", "reports/health-events.jsonl"
    ])
    
    # 4. Chờ 12s để có baseline traffic và health check ổn định
    print("4. Cho 12s truoc khi kich hoat su co...")
    time.sleep(12)
    
    # 5. Tấn công: kill region a bằng netblock mock
    print("5. TAN CONG: chaos kill region a (mode=netblock)...")
    subprocess.run([
        sys.executable, "chaos/kill_region.py",
        "--region", "a", "--mode", "netblock", "--mock"
    ], check=True)
    
    # 6. Kích hoạt runbook ứng cứu sự cố
    print("6. UNG CUU: chay runbook tu dong...")
    subprocess.run([
        sys.executable, "dr/runbook.py",
        "--primary", "a", "--target", "b", "--backend", "fs", "--auto"
    ], check=True)
    
    # 7. Chờ loadgen và health checker hoàn thành
    print("7. Cho loadgen hoan tat viec ghi log...")
    p_traffic.wait()
    p_health.wait()
    
    # 8. Dừng ingest và replicate
    print("8. Dung ingest va replicate processes...")
    p_ingest.terminate()
    p_replicate.terminate()
    try:
        p_ingest.wait(timeout=2)
        p_replicate.wait(timeout=2)
    except Exception:
        pass
        
    # 9. Đo kết quả RTO
    print("9. DO RTO & XUAT BAO CAO...")
    res = subprocess.run([
        sys.executable, "tools/measure_rto.py",
        "--loadgen", "reports/drill-2-withdr.jsonl",
        "--target-rto", "300"
    ], capture_output=True, text=True, encoding="utf-8")
    
    print(res.stdout)
    with open("reports/measure-drill-2.json", "w", encoding="utf-8") as f:
        f.write(res.stdout)
        
    print("=== HOAN TAT DRILL 2 ===")

if __name__ == "__main__":
    main()
