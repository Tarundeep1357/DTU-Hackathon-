"""DNS-pinned HTTPS downloads with no redirects, cookies, credentials or proxies."""
from dataclasses import dataclass
import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import unquote, urlsplit


class FetchError(ValueError):
    pass


@dataclass
class Download:
    filename: str
    content: bytes
    url: str
    sha256: str
    fetched_at: float


def validate_destination(url, allowed_hosts, resolver=socket.getaddrinfo):
    if len(url) > 3000 or any(ord(c) < 32 for c in url):
        raise FetchError("Invalid URL")
    p = urlsplit(url)
    try:
        port = p.port
    except ValueError:
        raise FetchError("Invalid URL port") from None
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment or port not in {None, 443}:
        raise FetchError("Only credential-free HTTPS on port 443 is allowed")
    host = p.hostname.lower().encode("idna").decode("ascii")
    if host not in allowed_hosts:
        raise FetchError("Host is not on the administrator's exact-host allowlist")
    try:
        addresses = sorted({item[4][0] for item in resolver(host, 443, type=socket.SOCK_STREAM)})
    except OSError:
        raise FetchError("Host could not be resolved") from None
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise FetchError("Private, loopback, reserved and mixed public/private destinations are blocked")
    return p, host, addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=12, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def fetch_document(url, allowed_hosts, max_bytes=10 * 1024 * 1024, *, resolver=socket.getaddrinfo, connection_factory=PinnedHTTPS):
    parsed, host, addresses = validate_destination(url, allowed_hosts, resolver)
    connection = connection_factory(host, addresses[0])
    started = time.monotonic()
    try:
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request("GET", path, headers={"Host": host, "User-Agent": "EvidenceArena/2.0", "Accept": "application/pdf,text/plain", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        if response.status != 200:
            raise FetchError("Download failed; redirects are deliberately not followed")
        content_type = response.getheader("Content-Type", "").split(";")[0].lower()
        extension = {"application/pdf": ".pdf", "text/plain": ".txt"}.get(content_type)
        if not extension or response.getheader("Content-Encoding", "identity") != "identity":
            raise FetchError("Only uncompressed PDF/TXT responses are accepted")
        length = response.getheader("Content-Length")
        if length and (not length.isdigit() or int(length) > max_bytes):
            raise FetchError("Download exceeds the allowed size")
        parts, total = [], 0
        while True:
            if time.monotonic() - started > 35:
                raise FetchError("Download exceeded its time budget")
            chunk = response.read(min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise FetchError("Download exceeds the allowed size")
            parts.append(chunk)
        content = b"".join(parts)
        filename = unquote(parsed.path.rsplit("/", 1)[-1])
        if not filename.lower().endswith(extension):
            filename = "retrieved-document" + extension
        return Download(filename[:200], content, url, hashlib.sha256(content).hexdigest(), time.time())
    except FetchError:
        raise
    except Exception:
        raise FetchError("HTTPS download failed; check the allowed host and source availability") from None
    finally:
        connection.close()

