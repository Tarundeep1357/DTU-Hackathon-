from dataclasses import dataclass
from datetime import timedelta
import hashlib
import re
import shutil

from sqlalchemy import delete, select, update

from evidence_arena.config import Settings
from evidence_arena.core.contracts import Analysis, CaseSnapshot, ReviewAnnotation, uid, utcnow
from evidence_arena.storage import AuditRow, CaseRow, MemberRow, SystemSettingRow, UserRow, VersionRow, sessions


class AccessDenied(PermissionError):
    pass


class ConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class Principal:
    user_id: str
    issuer: str
    subject: str
    name: str


def principal_from_claims(claims: dict) -> Principal:
    if not claims.get("iss") or not claims.get("sub"):
        raise AccessDenied("OIDC identity is missing issuer or subject")
    issuer, subject = str(claims["iss"]), str(claims["sub"])
    user_id = "U_" + hashlib.sha256(f"{issuer}\x00{subject}".encode()).hexdigest()
    return Principal(user_id, issuer, subject, str(claims.get("name") or claims.get("preferred_username") or "Reviewer")[:200])


class CaseService:
    def __init__(self, engine, settings: Settings):
        self.session = sessions(engine)
        self.settings = settings
        settings.storage_dir.mkdir(parents=True, exist_ok=True)

    def register(self, principal: Principal):
        with self.session.begin() as db:
            user = db.get(UserRow, principal.user_id)
            if user is None:
                user = UserRow(id=principal.user_id, issuer=principal.issuer, subject=principal.subject,
                               name=principal.name, admin=f"{principal.issuer}|{principal.subject}" in self.settings.admin_identities)
                db.add(user)
            elif (user.issuer, user.subject) != (principal.issuer, principal.subject):
                raise AccessDenied("Identity mismatch")
            return {"id": user.id, "name": user.name, "admin": user.admin}

    def _permission(self, db, actor, case_id, roles=("owner", "reviewer", "viewer")):
        member = db.get(MemberRow, (case_id, actor))
        case = db.get(CaseRow, case_id)
        if not member or member.role not in roles or not case or case.expires_at.replace(tzinfo=utcnow().tzinfo) <= utcnow():
            raise AccessDenied("Case unavailable or permission denied")
        return case, member

    @staticmethod
    def _audit(db, actor, action, case_id=None, **details):
        db.add(AuditRow(id=uid("A"), actor_id=actor, action=action, case_id=case_id, details=details))

    def list_cases(self, actor):
        with self.session() as db:
            return [{"case_id": c.id, "title": c.title, "version": c.current_version, "role": m.role,
                     "expires_at": c.expires_at.isoformat()} for c, m in db.execute(
                select(CaseRow, MemberRow).join(MemberRow, CaseRow.id == MemberRow.case_id)
                .where(MemberRow.user_id == actor, CaseRow.expires_at > utcnow()).order_by(CaseRow.created_at.desc()))]

    def load(self, actor, case_id, version=None):
        with self.session() as db:
            case, _ = self._permission(db, actor, case_id)
            row = db.get(VersionRow, (case_id, version or case.current_version))
            if row is None:
                raise ValueError("Case version does not exist")
            return CaseSnapshot.model_validate(row.payload)

    def role(self, actor, case_id):
        with self.session() as db:
            return self._permission(db, actor, case_id)[1].role

    def _source_path(self, case_id, source_id):
        if not re.fullmatch(r"K_[a-f0-9]{32}", case_id) or not re.fullmatch(r"S_[a-f0-9]{20}(?:_duplicate\d+)?", source_id):
            raise ValueError("Invalid storage identifier")
        root = self.settings.storage_dir.resolve()
        target = (root / case_id / f"{source_id}.bin").resolve()
        if root not in target.parents:
            raise AccessDenied("Invalid source path")
        return target

    def _store_sources(self, case_id, analysis, source_bytes):
        for source in analysis.sources:
            target = self._source_path(case_id, source.source_id)
            content = source_bytes.get(source.source_id)
            if content is None and target.exists():
                content = target.read_bytes()
            if content is None or hashlib.sha256(content).hexdigest() != source.sha256:
                raise ValueError("Source bytes missing or integrity check failed")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != source.sha256:
                raise ValueError("Stored source failed integrity validation")
            if not target.exists():
                # Immutable files: never overwrite bytes belonging to an older case version.
                with target.open("xb") as output:
                    output.write(content)

    def create(self, actor, title, analysis: Analysis, source_bytes):
        from .exports import verified_analysis
        analysis = verified_analysis(analysis)
        title = title.strip()[:200]
        if not title:
            raise ValueError("A case title is required")
        case_id = uid("K")
        snapshot = CaseSnapshot(case_id=case_id, title=title, version=1, analysis=analysis)
        try:
            with self.session.begin() as db:
                if not db.get(UserRow, actor):
                    raise AccessDenied("Sign in before saving cases")
                self._store_sources(case_id, analysis, source_bytes)
                db.add(CaseRow(id=case_id, title=title, current_version=1,
                               expires_at=utcnow() + timedelta(days=self.retention_days())))
                db.flush()
                db.add(MemberRow(case_id=case_id, user_id=actor, role="owner"))
                db.add(VersionRow(case_id=case_id, version=1, payload=snapshot.model_dump(mode="json")))
                self._audit(db, actor, "case_created", case_id, version=1)
        except Exception:
            directory = self.settings.storage_dir / case_id
            if directory.exists():
                shutil.rmtree(directory)
            raise
        return snapshot

    def update(self, actor, case_id, expected_version, analysis, source_bytes=None, annotation=None):
        from .exports import verified_analysis
        analysis = verified_analysis(analysis)
        with self.session.begin() as db:
            case, _ = self._permission(db, actor, case_id, ("owner", "reviewer"))
            old = CaseSnapshot.model_validate(db.get(VersionRow, (case_id, case.current_version)).payload)
            result = db.execute(update(CaseRow).where(CaseRow.id == case_id, CaseRow.current_version == expected_version)
                                .values(current_version=expected_version + 1))
            if result.rowcount != 1:
                raise ConflictError("A newer case version exists. Reload before saving your changes.")
            annotations = old.annotations
            if annotation:
                annotation = ReviewAnnotation.model_validate(annotation)
                if annotation.author_id != actor:
                    raise AccessDenied("Annotation author must be the signed-in reviewer")
                if annotation.claim_id and annotation.claim_id not in {c.claim_id for c in analysis.claims}:
                    raise ValueError("Annotation refers to an unknown claim")
                if annotation.source_id and annotation.source_id not in {s.source_id for s in analysis.sources}:
                    raise ValueError("Annotation refers to an unknown source")
                if annotation.kind == "source_note" and not annotation.source_id:
                    raise ValueError("Source quality notes need a source identifier")
                annotations = annotations + [annotation]
            self._store_sources(case_id, analysis, source_bytes or {})
            snapshot = CaseSnapshot(case_id=case_id, title=case.title, version=expected_version + 1,
                                    analysis=analysis, annotations=annotations)
            db.add(VersionRow(case_id=case_id, version=snapshot.version, payload=snapshot.model_dump(mode="json")))
            self._audit(db, actor, annotation.kind if annotation else "analysis_updated", case_id, version=snapshot.version)
            return snapshot

    def source_bytes(self, actor, case_id, source_id, version=None):
        # Keep authorization and filesystem read within the same service entry point.
        snapshot = self.load(actor, case_id, version)
        source = next((s for s in snapshot.analysis.sources if s.source_id == source_id), None)
        if source is None:
            raise AccessDenied("Source is not part of the accessible case version")
        content = self._source_path(case_id, source_id).read_bytes()
        if hashlib.sha256(content).hexdigest() != source.sha256:
            raise ValueError("Stored source failed SHA-256 integrity validation")
        return content

    def members(self, actor, case_id):
        with self.session() as db:
            self._permission(db, actor, case_id)
            return [{"user_id": m.user_id, "name": u.name, "role": m.role} for m, u in db.execute(
                select(MemberRow, UserRow).join(UserRow, MemberRow.user_id == UserRow.id).where(MemberRow.case_id == case_id))]

    def set_member(self, actor, case_id, target, role):
        if role not in {"reviewer", "viewer", None}:
            raise ValueError("Only reviewer/viewer memberships may be assigned")
        with self.session.begin() as db:
            self._permission(db, actor, case_id, ("owner",))
            member = db.get(MemberRow, (case_id, target))
            if member and member.role == "owner":
                raise AccessDenied("Case owner cannot be removed or demoted")
            if not db.get(UserRow, target):
                raise ValueError("Recipient must sign in once and share their user ID")
            if role is None:
                if member:
                    db.delete(member)
            elif member:
                member.role = role
            else:
                db.add(MemberRow(case_id=case_id, user_id=target, role=role))
            self._audit(db, actor, "membership_changed", case_id, target=target, role=role)

    def audit(self, actor, case_id):
        with self.session() as db:
            self._permission(db, actor, case_id)
            return [{"time": a.created_at.isoformat(), "actor": a.actor_id, "action": a.action, "details": a.details}
                    for a in db.scalars(select(AuditRow).where(AuditRow.case_id == case_id).order_by(AuditRow.created_at))]

    def delete_case(self, actor, case_id):
        with self.session.begin() as db:
            case, _ = self._permission(db, actor, case_id, ("owner",))
            db.delete(case)
            self._audit(db, actor, "case_deleted", case_id)
        directory = self.settings.storage_dir / case_id
        if directory.exists():
            shutil.rmtree(directory)

    def retention_days(self):
        with self.session() as db:
            setting = db.get(SystemSettingRow, "retention_days")
            return int(setting.value["days"]) if setting else self.settings.retention_days

    def allowed_hosts(self):
        with self.session() as db:
            setting = db.get(SystemSettingRow, "allowed_hosts")
            return tuple(setting.value["hosts"]) if setting else self.settings.allowed_hosts

    def admin_settings(self, actor, *, hosts=None, retention_days=None):
        with self.session.begin() as db:
            user = db.get(UserRow, actor)
            if not user or not user.admin:
                raise AccessDenied("Administrator permission required")
            changes = {}
            if hosts is not None:
                if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", h) or "." not in h for h in hosts):
                    raise ValueError("Use exact public DNS hostnames, without paths or wildcards")
                changes["allowed_hosts"] = {"hosts": sorted(set(hosts))}
            if retention_days is not None:
                if not 1 <= retention_days <= 3650:
                    raise ValueError("Retention must be 1–3650 days")
                changes["retention_days"] = {"days": retention_days}
            for key, value in changes.items():
                db.merge(SystemSettingRow(key=key, value=value))
            self._audit(db, actor, "system_settings_changed", keys=list(changes))

