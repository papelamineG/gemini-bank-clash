"""Local development orchestrator for Gemini Bank.

Starts all 4 microservices concurrently using Python sub-processes,
monitors their health endpoints, and seeds initial data.
"""
import sys
import os
import time
import subprocess
import requests

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(ROOT_DIR, ".venv", "bin", "python")
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = sys.executable

SERVICES = [
    {
        "name": "users",
        "cmd": [VENV_PYTHON, "users/app.py"],
        "cwd": ROOT_DIR,
        "env": {"PORT": "8081", "TOKEN_SECRET": "dev-secret-change-me", "USERS_DB": os.path.join(ROOT_DIR, "users.db")},
        "health": "http://127.0.0.1:8081/healthz",
    },
    {
        "name": "ledger",
        "cmd": [VENV_PYTHON, "ledger/dev_ledger.py"],
        "cwd": ROOT_DIR,
        "env": {"LEDGER_PORT": "8082", "TOKEN_SECRET": "dev-secret-change-me", "LEDGER_DB": os.path.join(ROOT_DIR, "ledger.db")},
        "health": "http://127.0.0.1:8082/healthz",
    },
    {
        "name": "statements",
        "cmd": [VENV_PYTHON, "statements/app.py"],
        "cwd": ROOT_DIR,
        "env": {"PORT": "8083", "LEDGER_URL": "http://127.0.0.1:8082"},
        "health": "http://127.0.0.1:8083/healthz",
    },
    {
        "name": "frontend",
        "cmd": [VENV_PYTHON, "frontend/app.py"],
        "cwd": ROOT_DIR,
        "env": {
            "PORT": "8080",
            "USERS_URL": "http://127.0.0.1:8081",
            "LEDGER_URL": "http://127.0.0.1:8082",
            "STATEMENTS_URL": "http://127.0.0.1:8083",
            "SECRET_KEY": "dev-secret-change-me",
        },
        "health": "http://127.0.0.1:8080/healthz",
    },
]


def start_all():
    processes = []
    print("Starting Gemini Bank microservices...")
    for s in SERVICES:
        env = os.environ.copy()
        env.update(s["env"])
        p = subprocess.Popen(
            s["cmd"],
            cwd=s["cwd"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        processes.append((s, p))
        print(f"  Launched {s['name']} (PID {p.pid})")

    # Wait for all health checks
    print("\nWaiting for health checks...")
    start_time = time.time()
    for s, p in processes:
        healthy = False
        while time.time() - start_time < 20:
            if p.poll() is not None:
                out = p.stdout.read().decode()
                print(f"ERROR: {s['name']} exited prematurely:\n{out}")
                sys.exit(1)
            try:
                r = requests.get(s["health"], timeout=1)
                if r.status_code == 200:
                    healthy = True
                    print(f"  [OK] {s['name']} is healthy at {s['health']}")
                    break
            except Exception:
                pass
            time.sleep(0.3)
        if not healthy:
            print(f"ERROR: {s['name']} failed health check at {s['health']}")
            sys.exit(1)

    print("\nAll 4 microservices are healthy and running!")
    return processes


if __name__ == "__main__":
    processes = start_all()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping services...")
        for _, p in processes:
            p.terminate()
