import base64
from datetime import timedelta
import socket

import pytest

from evidence_arena.core.contracts import utcnow
from evidence_arena.services.cases import AccessDenied
from evidence_arena.services.fetch import FetchError, fetch_document, validate_destination
from evidence_arena.services.jobs import JobService
from evidence_arena.storage import JobRow, sessions


def resolver(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


@pytest.mark.parametrize("url", ["http://example.com/a.pdf", "https://user:pass@example.com/a", "https://example.com:8080/a", "https://example.com/a#fragment", "https://other.example/a"])
def test_url_restrictions(url):
    with pytest.raises(FetchError):
        validate_destination(url, ("example.com",), resolver)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "192.168.1.1", "0.0.0.0"])
def test_private_network_is_blocked(address):
    with pytest.raises(FetchError, match="Private"):
        validate_destination("https://example.com/a", ("example.com",), lambda *a, **k: [(2, 1, 6, "", (address, 443))])


def test_download_pins_dns_and_caps_size():
    addresses = []
    class Response:
        status = 200
        content = b"A public text record"
        def getheader(self, key, default=None):
            return {"Content-Type": "text/plain", "Content-Length": str(len(self.content))}.get(key, default)
        def read(self, n):
            result, self.content = self.content[:n], self.content[n:]
            return result
    class Connection:
        def __init__(self, host, address):
            addresses.append((host, address))
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    result = fetch_document("https://example.com/a.txt", ("example.com",), resolver=resolver, connection_factory=Connection)
    assert result.content == b"A public text record"
    assert addresses == [("example.com", "93.184.216.34")]
    with pytest.raises(FetchError, match="size"):
        fetch_document("https://example.com/a.txt", ("example.com",), 3, resolver=resolver, connection_factory=Connection)


def test_redirect_not_followed():
    class Response:
        status = 302
    class Connection:
        def __init__(self, *args):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    with pytest.raises(FetchError, match="redirect"):
        fetch_document("https://example.com/a.txt", ("example.com",), resolver=resolver, connection_factory=Connection)


def test_job_lifecycle_and_content_cleanup(environment):
    engine, settings = environment
    jobs = JobService(engine, settings)
    job = jobs.submit("session:owner", "ingest", {"files": [{"filename": "test.txt", "content": base64.b64encode(b"A readable test passage").decode()}]})
    with pytest.raises(AccessDenied):
        jobs.get("session:stranger", job)
    assert jobs.run_one()
    assert jobs.get("session:owner", job)["state"] == "completed"
    assert jobs.get("session:owner", job)["result"]["passages"]
    jobs.consume("session:owner", job)
    assert jobs.get("session:owner", job)["result"] is None


def test_job_cancel_and_interrupted_recovery(environment):
    engine, settings = environment
    jobs = JobService(engine, settings)
    cancelled = jobs.submit("owner", "fetch", {"url": "https://example.com"})
    jobs.cancel("owner", cancelled)
    assert jobs.get("owner", cancelled)["state"] == "cancelled"
    job = jobs.submit("owner", "ingest", {"files": []})
    claimed = jobs.claim()
    assert claimed[0] == job
    with sessions(engine).begin() as db:
        db.get(JobRow, job).lease_until = utcnow() - timedelta(seconds=1)
    jobs.recover()
    assert jobs.get("owner", job)["state"] == "queued"
    jobs.claim()
    with sessions(engine).begin() as db:
        db.get(JobRow, job).lease_until = utcnow() - timedelta(seconds=1)
    jobs.recover()
    assert jobs.get("owner", job)["state"] == "failed"

