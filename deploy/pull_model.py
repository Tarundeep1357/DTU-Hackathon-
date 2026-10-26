"""Download the smallest text-capable Gemma 4 instruction quant for this 8 GB host."""
import json
from pathlib import Path
import time
import argparse

import httpx

parser=argparse.ArgumentParser()
parser.add_argument("--model",default="gemma4:e2b-it-qat")
MODEL = parser.parse_args().model
last, last_percent = "", -1
with httpx.Client(timeout=httpx.Timeout(30, read=45)) as client:
    with client.stream("POST", "http://127.0.0.1:11435/api/pull", json={"model": MODEL, "stream": True}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            event = json.loads(line)
            if "error" in event:
                raise RuntimeError(event["error"])
            status = event.get("status", "")
            percent = int(event.get("completed", 0) * 100 / max(event.get("total", 1), 1))
            if status != last or percent >= last_percent + 2:
                print(status, f"{percent}%" if event.get("total") else "", flush=True)
                Path("data/model-download-status.json").write_text(json.dumps({"model": MODEL, "status": status,
                    "completed": event.get("completed"), "total": event.get("total"), "updated": time.time()}))
                last, last_percent = status, percent
print("Model download completed", flush=True)

