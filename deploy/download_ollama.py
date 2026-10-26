"""Download a pinned official standalone runtime; verify the release checksum."""
import hashlib
from pathlib import Path
import time
import zipfile

import httpx

VERSION = "v0.40.11"
ASSET = "ollama-windows-amd64-rocm.zip"
BASE = f"https://github.com/ollama/ollama/releases/download/{VERSION}"
root = Path("data/runtime")
root.mkdir(parents=True, exist_ok=True)
archive = root / ASSET
with httpx.Client(follow_redirects=True, timeout=25) as client:
    checksum = client.get(f"{BASE}/sha256sum.txt")
    checksum.raise_for_status()
    expected = next(line.split()[0] for line in checksum.text.splitlines() if line.rstrip().endswith(ASSET))
    if not archive.exists():
        with client.stream("GET", f"{BASE}/{ASSET}") as response:
            response.raise_for_status()
            total, last = 0, time.monotonic()
            with archive.open("wb") as target:
                for chunk in response.iter_bytes(1024 * 1024):
                    target.write(chunk)
                    total += len(chunk)
                    if time.monotonic() - last > 10:
                        print(f"Downloaded {total // 2**20} MB", flush=True)
                        last = time.monotonic()
digest = hashlib.sha256()
with archive.open("rb") as source:
    for chunk in iter(lambda: source.read(2**20), b""):
        digest.update(chunk)
if digest.hexdigest() != expected:
    archive.unlink()
    raise RuntimeError("Official runtime checksum did not match; archive removed")
with zipfile.ZipFile(archive) as package:
    for entry in package.infolist():
        destination = (root / entry.filename).resolve()
        if root.resolve() not in destination.parents:
            raise RuntimeError("Unsafe archive path")
    package.extractall(root)
print(f"Verified runtime extracted into {root.resolve()}", flush=True)

