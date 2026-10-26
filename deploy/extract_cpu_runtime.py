"""Read the official release ZIP by byte range; extract only CPU runtime files.

HTTPS authenticates the official asset; ZipFile validates each extracted entry CRC.
This selective path cannot verify the checksum of the entire release archive.
"""
import io
from pathlib import Path
import zipfile
import time
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor

import httpx

URL = "https://github.com/ollama/ollama/releases/download/v0.40.11/ollama-windows-amd64.zip"


class RemoteFile(io.RawIOBase):
    def __init__(self):
        self.client = httpx.Client(follow_redirects=True, timeout=25)
        release = self.client.get("https://api.github.com/repos/ollama/ollama/releases/tags/v0.40.11")
        release.raise_for_status()
        asset = next(a for a in release.json()["assets"] if a["name"] == "ollama-windows-amd64.zip")
        size = asset["size"]
        with self.client.stream("GET", URL + "?ea_probe=" + str(time.time_ns()), headers={"Range": f"bytes={size-65557}-{size-1}", "Accept-Encoding": "identity"}) as response:
            if response.status_code != 206:
                raise RuntimeError("Release host did not support byte-range downloads")
            self.url = str(response.url)
            self.size = int(response.headers["content-range"].split("/")[-1])
            self.tail = response.read()
        self.position = 0

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        return self.position

    def tell(self):
        return self.position

    def read(self, n=-1):
        n = self.size - self.position if n < 0 else n
        end = min(self.position + n, self.size)
        if self.position >= self.size - len(self.tail):
            content = self.tail[self.position - (self.size - len(self.tail)):end - (self.size - len(self.tail))]
        else:
            response = self.client.get(self.url + "&ea_range=" + str(self.position), headers={"Range": f"bytes={self.position}-{end - 1}", "Accept-Encoding": "identity"})
            if response.status_code != 206:
                raise RuntimeError("Range request failed")
            content = response.content
        self.position += len(content)
        return content


root = Path("data/runtime-cpu")
root.mkdir(parents=True, exist_ok=True)
remote = RemoteFile()
with zipfile.ZipFile(remote) as package:
    entries = [e for e in package.infolist() if not e.is_dir() and
               (e.filename.lower() == "ollama.exe" or
                (e.filename.lower().startswith("lib/ollama/") and not any(x in e.filename.lower() for x in ("cuda", "vulkan", "rocm", "mlx"))))]
    print("CPU release entries:", [(e.filename, e.file_size) for e in entries], flush=True)
    entries = list(entries)

def range_bytes(start, end):
    cache = root / ".chunks" / f"{start}-{end}.bin"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and cache.stat().st_size == end - start:
        return cache.read_bytes()
    for attempt in range(3):
        try:
            response = remote.client.get(remote.url + "&ea_chunk=" + str(start) + "_" + str(attempt),
                                         headers={"Range": f"bytes={start}-{end-1}", "Accept-Encoding": "identity"}, timeout=15)
            if response.status_code != 206 or len(response.content) != end - start:
                raise RuntimeError("Incorrect release byte range")
            cache.write_bytes(response.content)
            return response.content
        except (httpx.HTTPError, RuntimeError):
            if attempt == 2:
                raise


for entry in sorted(entries, key=lambda e: (e.filename != "ollama.exe", e.filename)):
        target = (root / entry.filename).resolve()
        if root.resolve() not in target.parents:
            raise RuntimeError("Unsafe archive entry")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.stat().st_size != entry.file_size:
            header = range_bytes(entry.header_offset, entry.header_offset + 30)
            fields = struct.unpack("<IHHHHHIIIHH", header)
            data_offset = entry.header_offset + 30 + fields[-2] + fields[-1]
            segments = [(start, min(data_offset + entry.compress_size, start + 512 * 1024))
                        for start in range(data_offset, data_offset + entry.compress_size, 512 * 1024)]
            with ThreadPoolExecutor(max_workers=6) as pool:
                blocks = list(pool.map(lambda pair: range_bytes(*pair), segments))
            compressed = b"".join(blocks)
            content = zlib.decompress(compressed, -15) if entry.compress_type == 8 else compressed
            if len(content) != entry.file_size or zlib.crc32(content) & 0xffffffff != entry.CRC:
                raise RuntimeError("Release component CRC validation failed")
            target.write_bytes(content)
        print("Extracted", entry.filename, flush=True)
remote.client.close()

