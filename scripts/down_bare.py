"""Stop 2 regions + edge natively and reliably on Windows and Unix."""
import os
import pathlib
import sys

def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    os.chdir(root)
    run_dir = pathlib.Path("run")
    if run_dir.exists():
        import psutil
        for f in run_dir.glob("*.pid"):
            try:
                pid = int(f.read_text().strip())
                if psutil.pid_exists(pid):
                    p = psutil.Process(pid)
                    try:
                        p.resume()
                    except Exception:
                        pass
                    p.kill()
            except Exception:
                pass
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass
        # Fallback: check if any leftover process is listening on 8001, 8002, 8080
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr.port in (8001, 8002, 8080) and conn.pid:
                    try:
                        p = psutil.Process(conn.pid)
                        try:
                            p.resume()
                        except Exception:
                            pass
                        p.kill()
                    except Exception:
                        pass
        except Exception:
            pass
    print("all stopped")

if __name__ == "__main__":
    main()
