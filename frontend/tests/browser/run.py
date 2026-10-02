import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

scripts = Path(__file__).resolve().parent
repo = scripts.parents[2]
temporary = tempfile.TemporaryDirectory(prefix="layla-chat-regression-")
qa = Path(temporary.name)
env = {
    **{k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")},
    "DATABASE_URL": f"sqlite+aiosqlite:///{qa}/isolation-ui.db",
    "LAYLA_SECRET_KEY": "ui-local-encryption-key",
    "LAYLA_JWT_SECRET": "ui-test-only-long-token-secret-not-production",
    "CORE_ORIGIN": "http://127.0.0.1:8219",
    "NO_PROXY": "127.0.0.1,localhost",
    "no_proxy": "127.0.0.1,localhost",
    "NEXT_TELEMETRY_DISABLED": "1",
    "LAYLA_ALLOW_OPEN_REGISTRATION": "1",
    "LAYLA_PROJECTS_DIR": str(qa / "projects"),
}
processes = []
try:
    with (qa / "api.log").open("w") as api_log, (qa / "next.log").open("w") as next_log:
        processes.append(
            subprocess.Popen(
                [str(repo / "core/.venv/bin/python"), str(scripts / "fixture_server.py")],
                cwd=repo / "core",
                env=env,
                stdout=api_log,
                stderr=subprocess.STDOUT,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    "node",
                    str(repo / "frontend/node_modules/next/dist/bin/next"),
                    "dev",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    "3310",
                ],
                cwd=repo / "frontend",
                env=env,
                stdout=next_log,
                stderr=subprocess.STDOUT,
            )
        )
        for port in [8219, 3310]:
            deadline = time.monotonic() + 40
            while True:
                with socket.socket() as s:
                    if s.connect_ex(("127.0.0.1", port)) == 0:
                        break
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Port {port} unavailable")
                time.sleep(0.2)
        result = subprocess.run(
            ["node", str(scripts / "engagement-chat.cjs")],
            cwd=qa,
            env=env,
            check=False,
            timeout=240,
        )
        raise SystemExit(result.returncode)
finally:
    for process in processes:
        process.terminate()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    temporary.cleanup()
