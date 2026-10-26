import pytest
from fastapi.testclient import TestClient

from evidence_arena.sample import sample_case
from evidence_arena.services.cases import CaseService
from evidence_arena.services.jobs import JobService
from evidence_arena.storage import JobRow,sessions


@pytest.fixture
def api_client(environment,monkeypatch):
    from evidence_arena import api
    engine,settings=environment
    monkeypatch.setattr(api,"engine",engine)
    monkeypatch.setattr(api,"settings",settings)
    monkeypatch.setattr(api,"cases",CaseService(engine,settings))
    monkeypatch.setattr(api,"jobs",JobService(engine,settings))
    with TestClient(api.app) as client:
        bootstrap=client.get("/api/bootstrap").json()
        yield client,bootstrap,api


def test_csrf_required(api_client):
    client,boot,_=api_client
    assert client.post("/api/model/smoke").status_code==403
    assert boot["csrf"]


def test_guest_case_save_requires_oidc(api_client):
    client,boot,_=api_client
    sample,_=sample_case()
    result=client.post("/api/cases",headers={"X-CSRF-Token":boot["csrf"]},json={"title":"test","analysis":sample.model_dump(mode="json"),"sample":True})
    assert result.status_code==401


def test_browser_cannot_supply_fake_provenance(api_client,environment):
    client,boot,api=api_client
    result=client.post("/api/analyze",headers={"X-CSRF-Token":boot["csrf"]},json={"question":"Investigate this sample evidence","sample":True,"sources":[{"fake":"source"}],"passages":[{"text":"FABRICATED"}]})
    assert result.status_code==200
    with sessions(environment[0])() as db:
        job=db.get(JobRow,result.json()["job_id"])
        assert all(p["text"]!="FABRICATED" for p in job.payload["passages"])
        assert len(job.payload["sources"])==3
    other=TestClient(api.app)
    other.get("/api/bootstrap")
    assert other.get("/api/jobs/"+result.json()["job_id"]).status_code==403


def test_missing_server_evidence_reference_rejected(api_client):
    client,boot,_=api_client
    result=client.post("/api/analyze",headers={"X-CSRF-Token":boot["csrf"]},json={"question":"A potentially forged source question","sources":[],"passages":[]})
    assert result.status_code==422


def test_export_uses_server_record(api_client):
    client,boot,_=api_client
    sample,_=sample_case()
    sample.edges[0].edge.quote="BROWSER FABRICATED QUOTATION"
    response=client.post("/api/export/json",headers={"X-CSRF-Token":boot["csrf"]},json={"sample":True,"analysis":sample.model_dump(mode="json")})
    assert response.status_code==200
    assert "BROWSER FABRICATED" not in response.text
    assert response.json()["analysis"]["verdicts"][0]["status"]=="Contested"


def test_upload_creates_bounded_job(api_client):
    client,boot,_=api_client
    response=client.post("/api/ingest",headers={"X-CSRF-Token":boot["csrf"]},files=[("files",("source.txt",b"A readable evidence record","text/plain"))])
    assert response.status_code==200
    assert response.json()["job_id"].startswith("J_")

