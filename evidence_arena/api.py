"""Same-origin HTTP adapter for the React client; all rules remain in services."""
import base64
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings
from .core.contracts import Analysis, Claim, ReviewAnnotation, uid
from .core.ingest import ingest
from .core.templates import TEMPLATES
from .sample import sample_case
from .services.cases import AccessDenied, CaseService, ConflictError, principal_from_claims
from .services.exports import evidence_packet, json_export, markdown_export, verified_analysis
from .services.gemma_client import GemmaClient, ModelError
from .services.jobs import JobService
from .storage import SystemSettingRow, make_engine, sessions

settings = Settings.from_env()
engine = make_engine(settings.database_url)
cases = CaseService(engine, settings)
jobs = JobService(engine, settings)
Path("data").mkdir(exist_ok=True)
secret_path = Path("data/session-secret.txt")
if not secret_path.exists():
    secret_path.write_text(secrets.token_urlsafe(48))
secret = os.getenv("EA_SESSION_SECRET") or secret_path.read_text().strip()
public_url = os.getenv("EA_PUBLIC_URL", "http://127.0.0.1:8501").rstrip("/")
oauth = OAuth()
oidc_configured = bool(os.getenv("EA_OIDC_METADATA_URL"))
if oidc_configured:
    oauth.register("identity", client_id=os.environ["EA_OIDC_CLIENT_ID"], client_secret=os.environ["EA_OIDC_CLIENT_SECRET"],
                   server_metadata_url=os.environ["EA_OIDC_METADATA_URL"], client_kwargs={"scope":"openid profile email", "code_challenge_method":"S256"})

app = FastAPI(title="Evidence Arena API", version="2.0.0")
app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="arena_session", same_site="lax",
                   https_only=public_url.startswith("https://"), max_age=8*3600)


@app.exception_handler(AccessDenied)
async def permission_error(request, error):
    return Response(json.dumps({"detail":str(error)}), status_code=403, media_type="application/json")


@app.exception_handler(ConflictError)
async def version_error(request, error):
    return Response(json.dumps({"detail":str(error)}), status_code=409, media_type="application/json")


@app.exception_handler(ValueError)
async def value_error(request, error):
    return Response(json.dumps({"detail":str(error)[:500]}), status_code=422, media_type="application/json")


def actor(request: Request, required=False):
    if request.session.get("identity"):
        principal = principal_from_claims(request.session["identity"])
        return principal.user_id
    if required:
        raise HTTPException(401, "Sign in through OIDC to save and collaborate")
    if not request.session.get("guest"):
        request.session["guest"] = "guest:" + secrets.token_hex(32)
    return request.session["guest"]


def protect(request: Request):
    expected = request.session.get("csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not secrets.compare_digest(expected, supplied):
        raise HTTPException(403, "Refresh the page before submitting this request")
    return actor(request)


@app.get("/api/bootstrap")
def bootstrap(request: Request):
    identity = request.session.get("identity")
    user = cases.register(principal_from_claims(identity)) if identity else None
    actor(request)
    if not request.session.get("csrf"):
        request.session["csrf"] = secrets.token_urlsafe(32)
    return {"csrf":request.session["csrf"], "user":user, "oidc_configured":oidc_configured,
            "templates":TEMPLATES, "model":settings.gemma_model_id, "model_config_error":GemmaClient(settings).configuration_error(),
            "remote":settings.gemma_remote, "privacy_url":settings.gemma_privacy_url,
            "limits":{"files":settings.max_files,"file_mb":settings.max_file_mb,"pages":settings.max_pdf_pages,"claims":settings.max_claims},
            "allowed_hosts":cases.allowed_hosts(),"retention_days":cases.retention_days()}


@app.get("/api/health")
def health():
    from datetime import datetime
    from .core.contracts import utcnow
    with sessions(engine)() as db:
        beat = db.get(SystemSettingRow,"worker_heartbeat")
        age = (utcnow()-datetime.fromisoformat(beat.value["timestamp"])).total_seconds() if beat else None
    return {"api":"ready","worker_alive":age is not None and age<35,"model":settings.gemma_model_id}


@app.get("/auth/login")
async def login(request: Request):
    if not oidc_configured:
        raise HTTPException(503,"Configure the OIDC provider first")
    return await oauth.identity.authorize_redirect(request,public_url+"/auth/callback")


@app.get("/auth/callback")
async def callback(request: Request):
    if not oidc_configured:
        raise HTTPException(503,"OIDC unavailable")
    try:
        token = await oauth.identity.authorize_access_token(request)
        info = token.get("userinfo")
        if not info:
            raise ValueError("Missing verified OIDC identity")
        principal = principal_from_claims(dict(info))
        cases.register(principal)
        request.session.clear()
        request.session["identity"] = {"iss":principal.issuer,"sub":principal.subject,"name":principal.name}
    except Exception:
        raise HTTPException(401,"OIDC sign-in could not be verified") from None
    return RedirectResponse("/")


@app.post("/api/logout")
def logout(request: Request):
    protect(request)
    request.session.clear()
    return {"ok":True}


@app.get("/api/sample")
def sample():
    analysis, files = sample_case(settings)
    return {"analysis":analysis.model_dump(mode="json"),"source_bytes":{k:base64.b64encode(v).decode() for k,v in files.items()}}


@app.post("/api/ingest")
async def ingest_files(request: Request, files: list[UploadFile] = File(...)):
    owner = protect(request)
    if not 1<=len(files)<=settings.max_files:
        raise HTTPException(422,"Document count exceeds the configured limit")
    encoded=[]
    for upload in files:
        content=await upload.read(settings.max_file_mb*1024*1024+1)
        await upload.close()
        if len(content)>settings.max_file_mb*1024*1024:
            raise HTTPException(413,"File exceeds the configured size limit")
        encoded.append({"filename":upload.filename or "input.txt","content":base64.b64encode(content).decode()})
    return {"job_id":jobs.submit(owner,"ingest",{"files":encoded,"ocr":request.query_params.get("ocr","true")=="true"})}


class AnalyzeBody(BaseModel):
    question: str = Field(min_length=1,max_length=4000)
    sources: list[dict]
    passages: list[dict]
    claims: list[dict] | None = None
    domain: str = "programme-audit"
    remote_consent: bool = False
    sample: bool = False
    ingest_job_id: str | None = None
    analysis_job_id: str | None = None
    case_id: str | None = None
    version: int | None = None


def resolve_evidence(request, reference):
    """Never accept browser-authored passages as source provenance."""
    if reference.sample:
        analysis,files=sample_case(settings)
        return analysis,{"sources":[s.model_dump(mode="json") for s in analysis.sources],"passages":[p.model_dump(mode="json") for p in analysis.passages]},files
    if reference.case_id:
        snapshot=cases.load(actor(request,True),reference.case_id,reference.version)
        analysis=snapshot.analysis
        files={s.source_id:cases.source_bytes(actor(request,True),reference.case_id,s.source_id,snapshot.version) for s in analysis.sources}
        return analysis,{"sources":[s.model_dump(mode="json") for s in analysis.sources],"passages":[p.model_dump(mode="json") for p in analysis.passages]},files
    job_id=reference.analysis_job_id or reference.ingest_job_id
    if not job_id:
        raise HTTPException(422,"Choose server-extracted sources, a saved case, or the prepared sample")
    completed=jobs.get(actor(request),job_id)
    if completed["state"]!="completed" or completed["kind"] not in {"ingest","analysis"}:
        raise HTTPException(422,"Evidence job is not ready")
    data=completed["result"]
    files={k:base64.b64decode(v,validate=True) for k,v in data.get("source_bytes",{}).items()}
    if completed["kind"]=="analysis":
        analysis=verified_analysis(Analysis.model_validate(data["analysis"]))
        return analysis,{"sources":[s.model_dump(mode="json") for s in analysis.sources],"passages":[p.model_dump(mode="json") for p in analysis.passages]},files
    return None,{"sources":data["sources"],"passages":data["passages"]},files


@app.post("/api/analyze")
def analyze(request: Request, body: AnalyzeBody):
    owner=protect(request)
    if settings.gemma_remote and not body.remote_consent:
        raise HTTPException(422,"Remote model processing consent is required")
    if len(json.dumps(body.model_dump()))>3_000_000:
        raise HTTPException(413,"Analysis input exceeds the allowed budget")
    problem=GemmaClient(settings).configuration_error()
    if problem:
        raise HTTPException(503,problem)
    _,corpus,files=resolve_evidence(request,body)
    return {"job_id":jobs.submit(owner,"analysis",{"question":body.question,"claims":body.claims,"domain":body.domain,
        **corpus,"with_sources":True,"source_bytes":{k:base64.b64encode(v).decode() for k,v in files.items()}})}


@app.get("/api/jobs/{job_id}")
def job(request: Request,job_id:str):
    return jobs.get(actor(request),job_id)


@app.post("/api/jobs/{job_id}/cancel")
def cancel(request: Request,job_id:str):
    jobs.cancel(protect(request),job_id)
    return {"ok":True}


@app.post("/api/jobs/{job_id}/consume")
def consume(request: Request,job_id:str):
    jobs.consume(protect(request),job_id)
    return {"ok":True}


class FetchBody(BaseModel):
    url: str = Field(max_length=3000)


@app.post("/api/fetch")
def fetch(request: Request,body:FetchBody):
    return {"job_id":jobs.submit(protect(request),"fetch",body.model_dump())}


class ApproveBody(BaseModel):
    job_id: str
    sample:bool=False
    ingest_job_id:str|None=None
    analysis_job_id:str|None=None
    case_id:str|None=None
    version:int|None=None


@app.post("/api/fetch/approve")
def approve(request: Request,body:ApproveBody):
    owner=protect(request)
    completed=jobs.get(owner,body.job_id)
    if completed["kind"]!="fetch" or completed["state"]!="completed":
        raise HTTPException(422,"Retrieved source is not ready for review")
    result=completed["result"]
    data=base64.b64decode(result["content"])
    item=ingest(result["filename"],data,settings,acquisition_url=result["url"],acquisition_label="User-approved HTTPS retrieval")
    from datetime import datetime,timezone
    item.source.acquired_at=datetime.fromtimestamp(result["fetched_at"],timezone.utc)
    try:
        _,corpus,originals=resolve_evidence(request,body)
    except HTTPException as error:
        if error.status_code!=422 or any([body.sample,body.ingest_job_id,body.analysis_job_id,body.case_id]):raise
        corpus,originals={"sources":[],"passages":[]},{}
    if len(corpus["sources"])>=settings.max_files or any(s["sha256"]==item.source.sha256 for s in corpus["sources"]):
        raise HTTPException(422,"Source limit reached or identical bytes already present")
    originals[item.source.source_id]=data
    merged={"sources":corpus["sources"]+[item.source.model_dump(mode="json")],"passages":corpus["passages"]+[p.model_dump(mode="json") for p in item.passages],"source_bytes":{k:base64.b64encode(v).decode() for k,v in originals.items()}}
    draft=jobs.store_completed(owner,"ingest",merged)
    return {"source":item.source.model_dump(mode="json"),"passages":[p.model_dump(mode="json") for p in item.passages],
            "content":result["content"],"ingest_job_id":draft}


@app.post("/api/model/smoke")
def model_smoke(request: Request):
    protect(request)
    try:
        return GemmaClient(settings).smoke()
    except ModelError as error:
        raise HTTPException(503,str(error)) from None


@app.get("/api/cases")
def list_cases(request: Request):
    return cases.list_cases(actor(request,True))


class SaveBody(BaseModel):
    title: str = Field(min_length=1,max_length=200)
    analysis: Analysis
    source_bytes: dict[str,str] = Field(default_factory=dict)
    expected_version: int | None = None
    sample:bool=False
    ingest_job_id:str|None=None
    analysis_job_id:str|None=None
    case_id:str|None=None
    version:int|None=None


@app.post("/api/cases")
def save(request: Request,body:SaveBody):
    protect(request)
    analysis,_,files=resolve_evidence(request,body)
    if analysis is None:raise HTTPException(422,"Run an evidence review before saving")
    return cases.create(actor(request,True),body.title,analysis,files)


@app.get("/api/cases/{case_id}")
def load(request: Request,case_id:str,version:int|None=None):
    owner=actor(request,True)
    snapshot=cases.load(owner,case_id,version)
    return {"snapshot":snapshot.model_dump(mode="json"),"role":cases.role(owner,case_id),
            "source_bytes":{s.source_id:base64.b64encode(cases.source_bytes(owner,case_id,s.source_id,snapshot.version)).decode() for s in snapshot.analysis.sources},
            "members":cases.members(owner,case_id),"audit":cases.audit(owner,case_id)}


@app.put("/api/cases/{case_id}")
def update_case(request:Request,case_id:str,body:SaveBody):
    protect(request)
    if body.expected_version is None:
        raise HTTPException(422,"Expected version is required")
    analysis,_,files=resolve_evidence(request,body)
    if analysis is None:raise HTTPException(422,"Run an evidence review before saving")
    return cases.update(actor(request,True),case_id,body.expected_version,analysis,files)


@app.delete("/api/cases/{case_id}")
def delete_case(request:Request,case_id:str):
    protect(request)
    cases.delete_case(actor(request,True),case_id)
    return {"ok":True}


class AnnotationBody(BaseModel):
    expected_version:int
    annotation:dict


@app.post("/api/cases/{case_id}/annotations")
def annotate(request:Request,case_id:str,body:AnnotationBody):
    protect(request)
    owner=actor(request,True)
    snapshot=cases.load(owner,case_id)
    annotation=ReviewAnnotation.model_validate({**body.annotation,"author_id":owner})
    return cases.update(owner,case_id,body.expected_version,snapshot.analysis,annotation=annotation)


class MemberBody(BaseModel):
    user_id:str
    role:str|None


@app.put("/api/cases/{case_id}/members")
def member(request:Request,case_id:str,body:MemberBody):
    protect(request)
    cases.set_member(actor(request,True),case_id,body.user_id,body.role)
    return {"ok":True}


class ExportBody(BaseModel):
    analysis:Analysis
    source_bytes:dict[str,str] = Field(default_factory=dict)
    sample:bool=False
    ingest_job_id:str|None=None
    analysis_job_id:str|None=None
    case_id:str|None=None
    version:int|None=None


@app.post("/api/export/{kind}")
def export(request:Request,kind:str,body:ExportBody):
    protect(request)
    analysis,_,files=resolve_evidence(request,body)
    if analysis is None:raise HTTPException(422,"No completed analysis to export")
    annotations=[];audit=[]
    if body.case_id:
        annotations=cases.load(actor(request,True),body.case_id,body.version).annotations
        audit=cases.audit(actor(request,True),body.case_id)
    if kind=="json":
        return Response(json_export(analysis,annotations,audit),media_type="application/json",headers={"Content-Disposition":"attachment; filename=evidence-review.json"})
    if kind=="markdown":
        return Response(markdown_export(analysis,annotations,audit),media_type="text/markdown",headers={"Content-Disposition":"attachment; filename=evidence-review.md"})
    if kind=="packet":
        content=evidence_packet(analysis,files,annotations,audit)
        return Response(content,media_type="application/zip",headers={"Content-Disposition":"attachment; filename=evidence-packet.zip"})
    raise HTTPException(404,"Unknown export format")


class AdminBody(BaseModel):
    hosts:list[str]
    retention_days:int


@app.put("/api/settings")
def admin(request:Request,body:AdminBody):
    protect(request)
    cases.admin_settings(actor(request,True),hosts=body.hosts,retention_days=body.retention_days)
    return {"ok":True}


dist=Path(__file__).resolve().parents[1]/"frontend"/"dist"
if (dist/"assets").exists():
    app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")


@app.get("/{path:path}")
def frontend(path:str):
    if path.startswith("api/") or path.startswith("auth/"):
        raise HTTPException(404,"Not found")
    if not (dist/"index.html").exists():
        return Response("Build the React UI: cd frontend && npm install && npm run build",media_type="text/plain",status_code=503)
    return FileResponse(dist/"index.html")

