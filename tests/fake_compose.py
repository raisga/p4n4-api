"""Stands in for `docker compose` in tests. Records each call as a JSON line in
$FAKE_COMPOSE_LOG; behaviour comes from environment variables:

- FAKE_SERVICES: services `config --services` lists (default "web db")
- FAKE_FAIL: stack directory name whose commands exit 1
- FAKE_SLEEP: seconds every up/down/restart takes
- FAKE_FOLLOW: "forever" keeps `logs --follow` running until killed; otherwise it ends
- FAKE_PID_FILE: where `logs` writes its PID
"""

import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
cwd = Path.cwd().name
with open(os.environ["FAKE_COMPOSE_LOG"], "a") as f:
    f.write(json.dumps({"args": args, "cwd": cwd}) + "\n")

if os.environ.get("FAKE_FAIL") == cwd:
    print(f"failing in {cwd}", flush=True)
    sys.exit(1)

if args[:2] == ["config", "--services"]:
    print("\n".join(os.environ.get("FAKE_SERVICES", "web db").split()))
elif args[0] == "logs":
    if os.environ.get("FAKE_PID_FILE"):
        Path(os.environ["FAKE_PID_FILE"]).write_text(str(os.getpid()))
    tail = int(args[args.index("--tail") + 1])
    for i in range(min(tail, 3)):
        print(f"web  | line {i}", flush=True)
    if "--follow" in args and os.environ.get("FAKE_FOLLOW") == "forever":
        while True:
            time.sleep(0.05)
else:
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
    print(f"{' '.join(args)} done in {cwd}", flush=True)
