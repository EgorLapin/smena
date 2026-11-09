# Локальный стенд на ВРЕМЕННОЙ базе -> съёмка недостающих экранов -> остановка.
# Прод (smena.chickenkiller.com) не затрагивается вообще.
import os
import shutil
import subprocess
import sys
import tempfile
import time

import httpx

PORT = 8012
BASE = f"http://127.0.0.1:{PORT}"
REPO = r"C:\Users\Egor\codesss\proj\Smena"
VENV_PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
NODE = (r"C:\Users\Egor\.dsh\dsh-runtimes\dsh-primary-runtime"
        r"\dependencies\node\bin\node.exe")
CAP = os.path.join(REPO, "docs", "report-work", "capture")
SHOTS = os.path.join(REPO, "docs", "report-work", "shots")

tmp = tempfile.mkdtemp(prefix="smena-shots-")
env = dict(os.environ)
env.update({
    "SECRET_KEY": "local-shoot-not-a-real-secret",
    "DATABASE_PATH": os.path.join(tmp, "shoot.db"),
    "PYTHONIOENCODING": "utf-8",
})

srv = None
try:
    r = subprocess.run([VENV_PY, "scripts/seed_demo.py"], cwd=REPO, env=env,
                       capture_output=True, text=True, encoding="utf-8")
    print("seed:", (r.stdout or r.stderr).strip().splitlines()[-1])

    srv = subprocess.Popen(
        [VENV_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(PORT)],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            httpx.get(BASE + "/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("стенд не поднялся")
    print("стенд:", BASE, "| БД:", env["DATABASE_PATH"])

    cenv = dict(env)
    cenv.update({"BASE": BASE, "OUT": SHOTS,
                 "PATH": os.path.dirname(NODE) + os.pathsep
                         + env.get("PATH", "")})
    cap = subprocess.run([NODE, os.path.join(CAP, "capture_shots.mjs")],
                         cwd=CAP, env=cenv, capture_output=True, text=True,
                         encoding="utf-8")
    print(cap.stdout)
    if cap.returncode != 0:
        print("STDERR:", cap.stderr[-1500:])
finally:
    if srv:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()
    time.sleep(1.0)
    shutil.rmtree(tmp, ignore_errors=True)
    print("стенд остановлен, временная база удалена:", tmp)
