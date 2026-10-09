"""Start 2 regions + edge natively and reliably on Windows and Unix."""
import os
import pathlib
import subprocess
import sys
import time
import httpx

def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    os.chdir(root)
    run_dir = pathlib.Path("run")
    run_dir.mkdir(exist_ok=True)
    pathlib.Path("reports").mkdir(exist_ok=True)

    DETACHED = (0x00000008 | 0x00000200) if sys.platform == "win32" else 0

    def start_region(region, port):
        env = dict(os.environ)
        env["REGION"] = region
        env["STATE_DIR"] = f"state/region-{region}"
        env["WARMUP_SECONDS"] = "6"
        log_f = open(run_dir / f"region-{region}.log", "w", encoding="utf-8")
        p = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "serving.app:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            env=env, stdout=log_f, stderr=log_f, creationflags=DETACHED
        )
        (run_dir / f"region-{region}.pid").write_text(str(p.pid))
        print(f"region-{region} pid={p.pid} port={port}")

    def start_edge():
        env = dict(os.environ)
        env["EDGE_TTL_SECONDS"] = "5"
        log_f = open(run_dir / "edge.log", "w", encoding="utf-8")
        p = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "edge.proxy:app", "--host", "127.0.0.1", "--port", "8080", "--log-level", "warning"],
            env=env, stdout=log_f, stderr=log_f, creationflags=DETACHED
        )
        (run_dir / "edge.pid").write_text(str(p.pid))
        print(f"edge pid={p.pid} port=8080")

    start_region("a", 8001)
    start_region("b", 8002)
    start_edge()

    print("Cho service len (toi da 10s)...")
    services = [
        ("region-a", "http://127.0.0.1:8001/healthz"),
        ("region-b", "http://127.0.0.1:8002/healthz"),
        ("edge", "http://127.0.0.1:8080/edge/state")
    ]
    for name, url in services:
        up = False
        for _ in range(20):
            try:
                r = httpx.get(url, timeout=1.0)
                if r.status_code == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        print(f"  {name}: {'UP' if up else 'KHONG PHAN HOI'}")

if __name__ == "__main__":
    main()
