"""Exercise live Gemma analysis plus persistence, permissions and grounded export."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time

from evidence_arena.config import Settings
from evidence_arena.sample import sample_case
from evidence_arena.services.cases import AccessDenied, CaseService, principal_from_claims
from evidence_arena.services.exports import evidence_packet, json_export
from evidence_arena.services.gemma_client import GemmaClient
from evidence_arena.services.pipeline import run_analysis
from evidence_arena.storage import Base, make_engine


def main():
    settings = Settings.from_env()
    started = time.monotonic()
    report = {"model_smoke": GemmaClient(settings).smoke()}
    print("Live Gemma 4 structured smoke passed", flush=True)
    fixture, files = sample_case(settings)
    analysis = run_analysis(fixture.question, fixture.sources, fixture.passages, settings,
                            progress=lambda p, message: print(p, message, flush=True))
    report["live_verdicts"] = {v.claim_id: v.status.value for v in analysis.verdicts}
    report["valid_citations"] = sum(e.citation_valid for e in analysis.edges)
    report["rejected_citations"] = sum(not e.citation_valid for e in analysis.edges)
    report["gaps"] = len(analysis.gaps)
    with tempfile.TemporaryDirectory(dir="data") as directory:
        local = replace(settings, database_url=f"sqlite:///{Path(directory).resolve() / 'smoke.db'}", storage_dir=Path(directory) / "sources")
        engine = make_engine(local.database_url)
        Base.metadata.create_all(engine)
        cases = CaseService(engine, local)
        owner = principal_from_claims({"iss": "https://synthetic-test.example", "sub": "owner"})
        stranger = principal_from_claims({"iss": "https://synthetic-test.example", "sub": "stranger"})
        cases.register(owner)
        cases.register(stranger)
        snapshot = cases.create(owner.user_id, "Live backend verification", analysis, files)
        restored = cases.load(owner.user_id, snapshot.case_id)
        assert restored.analysis == analysis
        try:
            cases.load(stranger.user_id, snapshot.case_id)
            raise AssertionError("Cross-user case access was allowed")
        except AccessDenied:
            report["cross_user_access_blocked"] = True
        originals = {s.source_id: cases.source_bytes(owner.user_id, snapshot.case_id, s.source_id) for s in analysis.sources}
        report["packet_bytes"] = len(evidence_packet(restored.analysis, originals))
        report["case_roundtrip"] = True
        report["export_json_valid"] = bool(json.loads(json_export(restored.analysis)))
        engine.dispose()
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    Path("docs/live-backend-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    Path("data/live-analysis.json").write_bytes(json_export(analysis))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()

