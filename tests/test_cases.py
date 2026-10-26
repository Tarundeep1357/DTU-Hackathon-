from datetime import timedelta

import pytest

from evidence_arena.core.contracts import ReviewAnnotation, Status, utcnow
from evidence_arena.sample import sample_case
from evidence_arena.services.cases import AccessDenied, ConflictError, principal_from_claims
from evidence_arena.services.jobs import purge_expired
from evidence_arena.storage import CaseRow, sessions


def create(service, people):
    a, files = sample_case()
    return service.create(people[0].user_id, "Meal review", a, files), files


def test_identity_requires_issuer_subject():
    with pytest.raises(AccessDenied):
        principal_from_claims({"email": "a@example.com"})
    a = principal_from_claims({"iss": "issuer1", "sub": "same", "email": "a@example.com"})
    b = principal_from_claims({"iss": "issuer2", "sub": "same", "email": "a@example.com"})
    assert a.user_id != b.user_id


def test_access_and_cross_user_source_isolation(service):
    cases, people = service
    snapshot, files = create(cases, people)
    assert not cases.list_cases(people[3].user_id)
    with pytest.raises(AccessDenied):
        cases.load(people[3].user_id, snapshot.case_id)
    with pytest.raises(AccessDenied):
        cases.source_bytes(people[3].user_id, snapshot.case_id, snapshot.analysis.sources[0].source_id)
    cases.set_member(people[0].user_id, snapshot.case_id, people[2].user_id, "viewer")
    assert cases.load(people[2].user_id, snapshot.case_id).version == 1
    with pytest.raises(AccessDenied):
        cases.update(people[2].user_id, snapshot.case_id, 1, snapshot.analysis)


def test_immutable_versions_and_concurrent_edit(service):
    cases, people = service
    first, files = create(cases, people)
    cases.set_member(people[0].user_id, first.case_id, people[1].user_id, "reviewer")
    note = ReviewAnnotation(claim_id="C1", kind="override", text="Reviewed with additional caveats", override_status=Status.UNVERIFIED, author_id=people[1].user_id)
    second = cases.update(people[1].user_id, first.case_id, 1, first.analysis, annotation=note)
    assert second.version == 2
    assert second.analysis.verdicts[0].status == Status.CONTESTED
    assert second.annotations[0].override_status == Status.UNVERIFIED
    assert cases.load(people[0].user_id, first.case_id, 1).annotations == []
    with pytest.raises(ConflictError):
        cases.update(people[0].user_id, first.case_id, 1, first.analysis)


def test_membership_removal_and_owner_protection(service):
    cases, people = service
    snapshot, _ = create(cases, people)
    cases.set_member(people[0].user_id, snapshot.case_id, people[1].user_id, "reviewer")
    with pytest.raises(AccessDenied):
        cases.set_member(people[1].user_id, snapshot.case_id, people[2].user_id, "viewer")
    with pytest.raises(AccessDenied):
        cases.set_member(people[0].user_id, snapshot.case_id, people[0].user_id, None)
    cases.set_member(people[0].user_id, snapshot.case_id, people[1].user_id, None)
    with pytest.raises(AccessDenied):
        cases.load(people[1].user_id, snapshot.case_id)


def test_source_integrity(service):
    cases, people = service
    snapshot, files = create(cases, people)
    source = snapshot.analysis.sources[0]
    assert cases.source_bytes(people[0].user_id, snapshot.case_id, source.source_id) == files[source.source_id]
    cases._source_path(snapshot.case_id, source.source_id).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="integrity"):
        cases.source_bytes(people[0].user_id, snapshot.case_id, source.source_id)


def test_delete_and_retention(service, environment):
    cases, people = service
    snapshot, _ = create(cases, people)
    cases.delete_case(people[0].user_id, snapshot.case_id)
    assert not (cases.settings.storage_dir / snapshot.case_id).exists()
    snapshot, _ = create(cases, people)
    engine, settings = environment
    with sessions(engine).begin() as db:
        db.get(CaseRow, snapshot.case_id).expires_at = utcnow() - timedelta(days=1)
    with pytest.raises(AccessDenied):
        cases.load(people[0].user_id, snapshot.case_id)
    assert purge_expired(engine, settings)["cases_removed"] == 1


def test_admin_settings_are_authorized(service, environment):
    from evidence_arena.storage import UserRow
    cases, people = service
    with pytest.raises(AccessDenied):
        cases.admin_settings(people[0].user_id, hosts=["example.com"])
    with sessions(environment[0]).begin() as db:
        db.get(UserRow, people[0].user_id).admin = True
    cases.admin_settings(people[0].user_id, hosts=["example.com"], retention_days=7)
    assert cases.allowed_hosts() == ("example.com",)
    assert cases.retention_days() == 7

