from dataclasses import replace

import pytest

from evidence_arena.config import Settings
from evidence_arena.services.cases import CaseService, principal_from_claims
from evidence_arena.storage import Base, make_engine


@pytest.fixture
def environment(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", storage_dir=tmp_path / "sources",
                        gemma_base_url="http://localhost:11435/v1", gemma_model_id="gemma4:e2b-it-qat", gemma_remote=False)
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    yield engine, settings
    engine.dispose()


@pytest.fixture
def service(environment):
    engine, settings = environment
    service = CaseService(engine, settings)
    people = [principal_from_claims({"iss": "https://identity.example", "sub": s, "name": s}) for s in ("owner", "reviewer", "viewer", "stranger")]
    for p in people:
        service.register(p)
    return service, people

