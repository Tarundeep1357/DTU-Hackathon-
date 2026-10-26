import base64
from datetime import timedelta
from threading import Event, Thread
import time

from sqlalchemy import delete, or_, select, update

from evidence_arena.config import Settings
from evidence_arena.core.contracts import Analysis, Claim, uid, utcnow
from evidence_arena.core.ingest import IngestionError, ingest_many
from evidence_arena.storage import CaseRow, JobRow, sessions
from .cases import AccessDenied, CaseService
from .fetch import FetchError, fetch_document
from .gemma_client import ModelError
from .pipeline import run_analysis


class Cancelled(RuntimeError):
    pass


class JobService:
    def __init__(self, engine, settings: Settings):
        self.engine = engine
        self.session = sessions(engine)
        self.settings = settings

    def submit(self, owner, kind, payload):
        if kind not in {"ingest", "analysis", "fetch"}:
            raise ValueError("Unsupported job type")
        with self.session.begin() as db:
            pending = list(db.scalars(select(JobRow.id).where(JobRow.owner_key == owner, JobRow.state.in_(["queued", "running"]))))
            if len(pending) >= 3:
                raise ValueError("Finish or cancel existing jobs before submitting more")
            job = JobRow(id=uid("J"), owner_key=owner, kind=kind, payload=payload,
                         expires_at=utcnow() + timedelta(hours=24))
            db.add(job)
            return job.id

    def get(self, owner, job_id):
        with self.session() as db:
            job = db.get(JobRow, job_id)
            if not job or job.owner_key != owner:
                raise AccessDenied("Job unavailable")
            return {"id": job.id, "kind": job.kind, "state": job.state, "progress": job.progress,
                    "message": job.message, "error": job.error, "result": job.result, "attempts": job.attempts,
                    "cancel_requested": job.cancel_requested}

    def cancel(self, owner, job_id):
        with self.session.begin() as db:
            job = db.get(JobRow, job_id)
            if not job or job.owner_key != owner:
                raise AccessDenied("Job unavailable")
            job.cancel_requested = True
            if job.state in {"queued", "failed"}:
                job.state, job.payload, job.result = "cancelled", {}, None

    def consume(self, owner, job_id):
        with self.session.begin() as db:
            job = db.get(JobRow, job_id)
            if not job or job.owner_key != owner:
                raise AccessDenied("Job unavailable")
            if job.state not in {"completed", "cancelled", "failed"}:
                raise ValueError("Job is still active")
            job.payload, job.result = {}, None
            job.state = "consumed"

    def recover(self):
        with self.session.begin() as db:
            stale = db.scalars(select(JobRow).where(JobRow.state == "running", JobRow.lease_until < utcnow()))
            for job in stale:
                job.lease_token, job.lease_until = None, None
                if job.cancel_requested:
                    job.state, job.payload = "cancelled", {}
                elif job.attempts >= 2:
                    job.state, job.error, job.payload = "failed", "Worker interrupted twice; please resubmit", {}
                else:
                    job.state, job.message = "queued", "Recovered interrupted work; waiting to retry"

    def claim(self):
        self.recover()
        with self.session.begin() as db:
            candidate = db.scalar(select(JobRow.id).where(JobRow.state == "queued", JobRow.cancel_requested.is_(False))
                                  .order_by(JobRow.created_at).limit(1))
            if not candidate:
                return None
            token = uid("L")
            changed = db.execute(update(JobRow).where(JobRow.id == candidate, JobRow.state == "queued", JobRow.cancel_requested.is_(False))
                                 .values(state="running", lease_token=token, lease_until=utcnow() + timedelta(seconds=90),
                                         attempts=JobRow.attempts + 1, message="Starting"))
            return (candidate, token) if changed.rowcount else None

    def run_one(self):
        claim = self.claim()
        if not claim:
            return False
        job_id, token = claim
        stop, cancellation = Event(), Event()
        def pulse():
            while not stop.wait(15):
                try:
                    with self.session.begin() as db:
                        job = db.get(JobRow, job_id)
                        if not job or job.lease_token != token or job.cancel_requested:
                            cancellation.set()
                            return
                        job.lease_until = utcnow() + timedelta(seconds=90)
                except Exception:
                    cancellation.set()
                    return
        heartbeat = Thread(target=pulse, daemon=True)
        heartbeat.start()
        def progress(percent, message):
            if cancellation.is_set():
                raise Cancelled()
            with self.session.begin() as db:
                job = db.get(JobRow, job_id)
                if not job or job.lease_token != token or job.cancel_requested:
                    raise Cancelled()
                job.progress, job.message = percent, message[:300]
                job.lease_until = utcnow() + timedelta(seconds=90)
        try:
            with self.session() as db:
                job = db.get(JobRow, job_id)
                kind, payload = job.kind, job.payload
            progress(5, "Processing")
            if kind == "ingest":
                files = [(f["filename"], base64.b64decode(f["content"], validate=True)) for f in payload["files"]]
                items = ingest_many(files, self.settings, ocr=payload.get("ocr", False))
                result = {"sources": [i.source.model_dump(mode="json") for i in items],
                          "passages": [p.model_dump(mode="json") for i in items for p in i.passages],
                          "images": {f"{i.source.source_id}:{page}": base64.b64encode(image).decode() for i in items for page, image in i.images.items()},
                          "source_bytes": {i.source.source_id: base64.b64encode(data).decode() for i, (_, data) in zip(items, files)}}
            elif kind == "analysis":
                from evidence_arena.core.contracts import Passage, Source
                result = run_analysis(payload["question"], [Source.model_validate(s) for s in payload["sources"]],
                                      [Passage.model_validate(p) for p in payload["passages"]], self.settings,
                                      claims=[Claim.model_validate(c) for c in payload["claims"]] if payload.get("claims") else None,
                                      domain=payload.get("domain", "programme-audit"), progress=progress).model_dump(mode="json")
            else:
                hosts = CaseService(self.engine, self.settings).allowed_hosts()
                doc = fetch_document(payload["url"], hosts, self.settings.max_file_mb * 1024 * 1024)
                result = {"filename": doc.filename, "content": base64.b64encode(doc.content).decode(), "url": doc.url,
                          "sha256": doc.sha256, "fetched_at": doc.fetched_at}
            progress(100, "Completed")
            with self.session.begin() as db:
                job = db.get(JobRow, job_id)
                if job and job.lease_token == token:
                    if job.cancel_requested:
                        job.state, job.result = "cancelled", None
                    else:
                        job.state, job.result = "completed", result
                    job.payload, job.lease_token, job.lease_until = {}, None, None
        except Exception as exc:
            with self.session.begin() as db:
                job = db.get(JobRow, job_id)
                if job and job.lease_token == token:
                    job.state = "cancelled" if isinstance(exc, Cancelled) or job.cancel_requested else "failed"
                    job.error = str(exc)[:500] if isinstance(exc, (ModelError, FetchError, IngestionError)) else "Processing failed; check inputs and configuration. Source contents were not logged."
                    job.payload, job.result, job.lease_token, job.lease_until = {}, None, None, None
        finally:
            stop.set()
            heartbeat.join(timeout=1)
        return True

    def purge(self):
        with self.session.begin() as db:
            count = db.execute(delete(JobRow).where(JobRow.expires_at < utcnow())).rowcount
        return count


def purge_expired(engine, settings):
    import shutil
    from evidence_arena.storage import AuditRow
    factory = sessions(engine)
    with factory.begin() as db:
        ids = list(db.scalars(select(CaseRow.id).where(CaseRow.expires_at < utcnow())))
        for case_id in ids:
            db.execute(delete(CaseRow).where(CaseRow.id == case_id))
            db.add(AuditRow(id=uid("A"), case_id=case_id, actor_id="system", action="retention_expired", details={}))
    for case_id in ids:
        folder = settings.storage_dir / case_id
        if folder.exists():
            shutil.rmtree(folder)
    # Clean orphan source directories, including a file write interrupted before DB commit.
    with factory() as db:
        live = set(db.scalars(select(CaseRow.id)))
        db.execute(delete(AuditRow).where(AuditRow.created_at < utcnow() - timedelta(days=90)))
        db.commit()
    for folder in settings.storage_dir.glob("K_*"):
        if folder.is_dir() and folder.name not in live and time.time() - folder.stat().st_mtime > 3600:
            shutil.rmtree(folder)
    return {"cases_removed": len(ids), "jobs_removed": JobService(engine, settings).purge()}

