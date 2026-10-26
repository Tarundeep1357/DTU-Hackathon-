"""Import the verified downloaded GGUF without a blocked HF metadata redirect."""
import hashlib
import json
from pathlib import Path

import httpx

DIGEST = "dfd66cc509c7e3361f54f1b7f911e9c6de57e2548b52cefa773d37b394beb410"
MODEL = "gemma4:e2b-q2-local"
blob = Path("data/models/blobs") / ("sha256-" + DIGEST)
if not blob.exists():
    raise SystemExit("The Gemma 4 E2B Q2_K weights have not finished downloading")
hasher = hashlib.sha256()
with blob.open("rb") as source:
    for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
        hasher.update(block)
if hasher.hexdigest() != DIGEST:
    raise SystemExit("Downloaded GGUF did not match its expected SHA-256")
with httpx.Client(timeout=120) as client:
    with client.stream("POST", "http://127.0.0.1:11435/api/create", json={
        "model": MODEL, "files": {"gemma-4-E2B-it.Q2_K.gguf": "sha256:" + DIGEST},
        "renderer":"gemma4-small", "parser":"gemma4",
        "parameters": {"num_ctx": 8192, "temperature": 0}, "stream": True}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            event = json.loads(line)
            if event.get("error"):
                raise RuntimeError(event["error"])
            print(event.get("status", ""), flush=True)
env = Path(".env")
lines = env.read_text().splitlines() if env.exists() else []
lines = [line for line in lines if not line.startswith("GEMMA_MODEL_ID=")]
env.write_text("\n".join(lines + ["GEMMA_MODEL_ID=" + MODEL]) + "\n")
print("Local model imported and .env configured; run the Gemma smoke test next", flush=True)

