"""Run with: streamlit run app.py --server.address 127.0.0.1"""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets

import streamlit as st
from sqlalchemy import inspect

from evidence_arena.config import Settings
from evidence_arena.core.contracts import Analysis, Claim, Passage, ReviewAnnotation, Source, Status
from evidence_arena.core.ingest import ingest
from evidence_arena.core.templates import TEMPLATES
from evidence_arena.sample import sample_case
from evidence_arena.services.cases import AccessDenied, CaseService, ConflictError, principal_from_claims
from evidence_arena.services.exports import verified_analysis
from evidence_arena.services.gemma_client import GemmaClient, ModelError
from evidence_arena.services.jobs import JobService
from evidence_arena.storage import make_engine
from evidence_arena import ui

st.set_page_config(page_title="Evidence Arena", page_icon="◈", layout="wide")
st.markdown(ui.STYLE, unsafe_allow_html=True)
settings = Settings.from_env()


@st.cache_resource
def database(url):
    return make_engine(url)


engine = database(settings.database_url)
if "jobs" not in inspect(engine).get_table_names():
    st.title("Evidence Arena")
    st.info("Initialize the database before starting the application.")
    st.code("python -m evidence_arena.cli migrate")
    st.stop()
cases = CaseService(engine, settings)
jobs = JobService(engine, settings)
client = GemmaClient(settings)

if "session_key" not in st.session_state:
    st.session_state.session_key = "session:" + secrets.token_hex(32)
principal = None
user = None
try:
    auth_configured = bool(st.secrets.get("auth", {}).get("server_metadata_url"))
except (FileNotFoundError, KeyError):
    auth_configured = False
if getattr(st.user, "is_logged_in", False):
    try:
        principal = principal_from_claims(dict(st.user))
        user = cases.register(principal)
    except AccessDenied as exc:
        st.error(str(exc))
owner_key = principal.user_id if principal else st.session_state.session_key


def queue(kind, payload, context="main"):
    job_id = jobs.submit(owner_key, kind, payload)
    st.session_state.active_job = {"id": job_id, "owner": owner_key, "context": context}


def clear_draft():
    for key in ("analysis", "corpus", "source_bytes", "source_images", "snapshot", "retrieved", "followup_analysis", "simulation", "source_selection", "claims_editor"):
        st.session_state.pop(key, None)


with st.sidebar:
    st.title("Evidence Arena")
    st.caption("Review claims. Inspect sources. Resolve gaps.")
    if principal:
        st.text(principal.name)
        with st.expander("Your identity"):
            st.code(principal.user_id)
            st.caption("Share this user ID with a case owner for access.")
            st.text(f"Issuer: {principal.issuer}\nSubject: {principal.subject}")
        if st.button("Sign out"):
            clear_draft()
            st.logout()
    elif auth_configured:
        if st.button("Sign in to save and collaborate", use_container_width=True):
            st.login()
    else:
        st.caption("Guest session · OIDC is not configured. Saved cases require sign-in.")
    st.divider()
    if st.button("Load fictional meal walkthrough", use_container_width=True):
        clear_draft()
        analysis, source_bytes = sample_case(settings)
        st.session_state.analysis, st.session_state.source_bytes = analysis, source_bytes
        st.session_state.corpus = {"sources": analysis.sources, "passages": analysis.passages}
        st.session_state.workspace_nav = "Findings"
        st.rerun()
    if st.button("New / reset session", use_container_width=True):
        active = st.session_state.get("active_job")
        if active:
            jobs.cancel(active["owner"], active["id"])
            st.session_state.pop("active_job", None)
        clear_draft()
        st.session_state.workspace_nav = "Investigation"
        st.session_state.session_key = "session:" + secrets.token_hex(32)
        st.rerun()
    if principal:
        saved = cases.list_cases(principal.user_id)
        if saved:
            selected_case = st.selectbox("Saved cases", saved, format_func=lambda c: f"{c['title']} · {c['role']}")
            if st.button("Open case", use_container_width=True):
                clear_draft()
                snapshot = cases.load(principal.user_id, selected_case["case_id"])
                st.session_state.snapshot = snapshot
                st.session_state.analysis = snapshot.analysis
                st.session_state.source_bytes = {s.source_id: cases.source_bytes(principal.user_id, snapshot.case_id, s.source_id, snapshot.version) for s in snapshot.analysis.sources}
                st.session_state.corpus = {"sources": snapshot.analysis.sources, "passages": snapshot.analysis.passages}
                st.session_state.workspace_nav = "Findings"
                st.rerun()
    st.divider()
    navigation = st.radio("WORKSPACE", ["Investigation", "Findings", "Sources", "Next evidence", "Challenge trail", "Flip Lab", "Review & cases", "Export", "Settings"], key="workspace_nav", label_visibility="visible")
    st.divider()
    problem = client.configuration_error()
    st.caption("Gemma 4 · " + ("configuration required" if problem else settings.gemma_model_id))
    if st.button("Test live model", disabled=bool(problem)):
        try:
            with st.spinner("Testing structured Gemma 4 inference…"):
                st.session_state.model_smoke = client.smoke()
            st.success("Gemma 4 responded with valid JSON")
        except ModelError as exc:
            st.error(str(exc))
    if st.session_state.get("model_smoke"):
        st.caption("Last smoke test passed in this browser session")


@st.fragment(run_every="2s")
def job_status():
    active = st.session_state.get("active_job")
    if not active:
        return
    job = jobs.get(active["owner"], active["id"])
    st.progress(job["progress"] / 100, text=f"{job['state'].capitalize()} · {job['message']}")
    if job["state"] in {"queued", "running"}:
        if st.button("Cancel job", key="cancel_active"):
            jobs.cancel(active["owner"], active["id"])
        if job["state"] == "queued":
            st.caption("A worker must be running: python -m evidence_arena.worker")
    elif job["state"] == "completed":
        result = job["result"]
        if job["kind"] == "ingest":
            st.session_state.corpus = {"sources": [Source.model_validate(s) for s in result["sources"]],
                                      "passages": [Passage.model_validate(p) for p in result["passages"]]}
            st.session_state.source_bytes = {k: base64.b64decode(v) for k, v in result["source_bytes"].items()}
            st.session_state.source_images = result["images"]
            st.session_state.pop("analysis", None)
            st.session_state.pop("snapshot", None)
        elif job["kind"] == "analysis":
            key = "followup_analysis" if active["context"] == "followup" else "analysis"
            st.session_state[key] = verified_analysis(Analysis.model_validate(result))
        else:
            st.session_state.retrieved = result
        jobs.consume(active["owner"], active["id"])
        st.session_state.pop("active_job", None)
        st.rerun()
    else:
        st.error(job["error"] or "Job cancelled")
        if st.button("Dismiss", key="dismiss_job"):
            jobs.consume(active["owner"], active["id"])
            st.session_state.pop("active_job", None)
            st.rerun()


st.markdown('<div class="ea-eyebrow">Evidence Arena / Review workspace</div>', unsafe_allow_html=True)
page_titles = {"Investigation": "Start with a question", "Findings": "The evidence, examined", "Sources": "Inspect the original evidence", "Next evidence": "What would change the finding?", "Challenge trail": "Follow every challenge", "Flip Lab": "Explore a different outcome", "Review & cases": "A review you can return to", "Export": "Take the evidence with you", "Settings": "Workspace settings"}
st.title(page_titles[navigation])
st.caption("Gemma 4 proposes interpretations. Verified quotations and explicit comparison rules determine the findings.")
if navigation == "Investigation":
    st.markdown('<div class="ea-stage"><span><b>01</b> Add sources</span><span><b>02</b> Inspect claims</span><span><b>03</b> Challenge evidence</span></div>', unsafe_allow_html=True)
job_status()
busy = bool(st.session_state.get("active_job"))

analysis = st.session_state.get("analysis")
snapshot = st.session_state.get("snapshot")
if snapshot and principal:
    try:
        cases.role(principal.user_id, snapshot.case_id)
    except AccessDenied:
        clear_draft()
        st.error("Case access was removed or retention expired. The cached view has been cleared.")
        st.stop()

page_keys = ["intake", "findings", "sources", "gaps", "trail", "lab", "review", "export", "admin"]
page_names = ["Investigation", "Findings", "Sources", "Next evidence", "Challenge trail", "Flip Lab", "Review & cases", "Export", "Settings"]
hidden = ",".join(f".st-key-page_{key}" for key, name in zip(page_keys, page_names) if name != navigation)
st.markdown(f"<style>{hidden}{{display:none !important;}}</style>", unsafe_allow_html=True)
intake, findings, sources_tab, gaps_tab, trail_tab, lab_tab, review_tab, export_tab, admin_tab = [st.container(key="page_"+key) for key in page_keys]

with intake:
    domain = st.selectbox("Review workflow", list(TEMPLATES), format_func=lambda k: TEMPLATES[k]["title"])
    template = TEMPLATES[domain]
    with st.expander("Domain evidence checklist"):
        st.text("Claim fields: " + ", ".join(template["fields"]))
        for entry in template["checklist"]:
            st.text("• " + entry)
        st.text(template["gap_prompt"])
    question = st.text_area("Question to investigate", value=analysis.question if analysis else template["question"], max_chars=4000)
    st.caption(f"1–{settings.max_files} PDF/TXT files · {settings.max_file_mb} MB each · {settings.max_pdf_pages} pages per PDF · up to {settings.max_claims} claims")
    if settings.gemma_remote:
        st.info("Live analysis sends relevant extracted text to the configured remote Gemma provider. Review its policy before uploading confidential content.")
        if settings.gemma_privacy_url.startswith("https://"):
            st.link_button("Provider privacy policy", settings.gemma_privacy_url)
        consent = st.checkbox("I approve sending the supplied text to this provider for live analysis")
    else:
        st.caption("Configured for local inference. Sources are processed by your local model endpoint.")
        consent = True
    st.caption("Draft job inputs/results are temporary server data (up to 24 hours if abandoned); consumed jobs are cleared. Saved cases use the configured retention policy.")
    uploads = st.file_uploader("Source documents", type=["pdf", "txt"], accept_multiple_files=True)
    use_ocr = st.checkbox("Use English OCR on pages without reliable text", value=settings.ocr_enabled)
    if st.button("Extract and inspect sources", disabled=busy or not uploads):
        try:
            if len(uploads) > settings.max_files or any(u.size > settings.max_file_mb * 1024 * 1024 for u in uploads):
                raise ValueError("Upload count or file size exceeds the displayed limits")
            queue("ingest", {"files": [{"filename": u.name, "content": base64.b64encode(u.getvalue()).decode()} for u in uploads], "ocr": use_ocr})
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    corpus = st.session_state.get("corpus")
    if corpus:
        st.subheader("Extraction review")
        st.dataframe([{"file": s.filename, "pages": s.page_count, "passages": sum(p.source_id == s.source_id for p in corpus["passages"]),
                       "warnings": "; ".join(s.warnings)} for s in corpus["sources"]], hide_index=True, use_container_width=True)
        if client.configuration_error():
            st.warning(client.configuration_error())
        if st.button("Analyze with Gemma 4", type="primary", disabled=busy or not consent or bool(client.configuration_error()) or not corpus["passages"]):
            queue("analysis", {"question": question, "sources": [s.model_dump(mode="json") for s in corpus["sources"]],
                               "passages": [p.model_dump(mode="json") for p in corpus["passages"]], "domain": domain})
            st.rerun()
    if analysis:
        st.subheader("Inspect or edit atomic claims")
        st.caption("Changing wording does not change the evidence. Re-run analysis to produce new findings. Use ISO dates and the same metric definitions.")
        try:
            edited = ui.edit_claims(analysis.claims)
            if st.button("Reanalyze edited claims", disabled=busy or not consent or bool(client.configuration_error())):
                queue("analysis", {"question": question, "sources": [s.model_dump(mode="json") for s in analysis.sources],
                                   "passages": [p.model_dump(mode="json") for p in analysis.passages],
                                   "claims": [c.model_dump(mode="json") for c in edited], "domain": domain})
                st.rerun()
        except ValueError as exc:
            st.error("Claim fields need correction: " + str(exc)[:400])
    with st.expander("Retrieve a source from an approved host"):
        st.caption("Allowlisted hosts: " + (", ".join(cases.allowed_hosts()) or "none; retrieval disabled"))
        url = st.text_input("Direct HTTPS PDF/TXT URL")
        if st.button("Fetch for review", disabled=busy or not url or not cases.allowed_hosts()):
            queue("fetch", {"url": url})
            st.rerun()
        retrieved = st.session_state.get("retrieved")
        if retrieved:
            content = base64.b64decode(retrieved["content"])
            st.text(f"Fetched: {retrieved['filename']}\nURL: {retrieved['url']}\nSHA-256: {retrieved['sha256']}")
            st.download_button("Inspect retrieved original", content, retrieved["filename"])
            if content.startswith(b"%PDF-"):
                st.caption("PDF: inspect the downloaded original before adding it.")
            else:
                st.code(content[:12000].decode("utf-8", errors="replace"), language=None, wrap_lines=True)
            if st.button("Approve and add to draft", disabled=busy):
                try:
                    current_sources = corpus["sources"] if corpus else []
                    if len(current_sources) >= settings.max_files:
                        raise ValueError("Document limit reached; start a new case or remove an upload")
                    item = ingest(retrieved["filename"], content, settings, acquisition_label="User-approved HTTPS retrieval", acquisition_url=retrieved["url"])
                    if any(s.sha256 == item.source.sha256 for s in current_sources):
                        raise ValueError("Identical source bytes are already present")
                    item.source.acquired_at = datetime.fromtimestamp(retrieved["fetched_at"], timezone.utc)
                    st.session_state.corpus = {"sources": current_sources + [item.source], "passages": (corpus["passages"] if corpus else []) + item.passages}
                    st.session_state.setdefault("source_bytes", {})[item.source.source_id] = content
                    st.session_state.pop("retrieved", None)
                    st.session_state.pop("analysis", None)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

if analysis:
    analysis = verified_analysis(analysis)
    source_bytes = st.session_state.get("source_bytes", {})
    with findings:
        ui.show_findings(analysis)
        st.subheader("Ask a follow-up about this evidence")
        followup = st.text_area("Follow-up question", max_chars=4000)
        if st.button("Review follow-up with Gemma 4", disabled=busy or not followup.strip() or not consent or bool(client.configuration_error())):
            queue("analysis", {"question": followup, "sources": [s.model_dump(mode="json") for s in analysis.sources],
                               "passages": [p.model_dump(mode="json") for p in analysis.passages], "domain": domain}, context="followup")
            st.rerun()
        if st.session_state.get("followup_analysis"):
            st.caption("Separate follow-up result; the original assessment remains above.")
            ui.show_findings(st.session_state.followup_analysis, prefix="followup")
            st.download_button("Export follow-up JSON", json_export(st.session_state.followup_analysis), "followup.json", "application/json")
    with sources_tab:
        ui.show_sources(analysis, source_bytes)
    with gaps_tab:
        ui.show_gaps(analysis)
    with trail_tab:
        st.subheader("Challenge Trail")
        st.caption("Recorded operations and observed results; this is not a model thinking transcript.")
        st.dataframe([e.model_dump(mode="json") for e in analysis.trail], hide_index=True, use_container_width=True)
        st.subheader("Source relationships")
        ui.show_graph(analysis)
    with lab_tab:
        ui.show_simulation(analysis)
    with review_tab:
        if not principal:
            st.info("Sign in through OIDC to save cases, invite reviewers, and record decisions.")
        else:
            title = st.text_input("Case title", value=snapshot.title if snapshot else "Evidence review")
            if snapshot:
                role = cases.role(principal.user_id, snapshot.case_id)
                st.caption(f"Version {snapshot.version} · your role: {role}")
                if st.button("Save analysis as next version", disabled=role == "viewer" or busy):
                    try:
                        st.session_state.snapshot = cases.update(principal.user_id, snapshot.case_id, snapshot.version, analysis, source_bytes)
                        st.success("New immutable case version saved")
                        st.rerun()
                    except ConflictError as exc:
                        st.error(str(exc))
                version = st.number_input("Inspect saved version", min_value=1, max_value=max(c["version"] for c in cases.list_cases(principal.user_id) if c["case_id"] == snapshot.case_id), value=snapshot.version, step=1)
                if st.button("Load selected version"):
                    chosen = cases.load(principal.user_id, snapshot.case_id, version)
                    st.session_state.snapshot, st.session_state.analysis = chosen, chosen.analysis
                    st.session_state.source_bytes = {s.source_id: cases.source_bytes(principal.user_id, chosen.case_id, s.source_id, version) for s in chosen.analysis.sources}
                    st.rerun()
                with st.form("review_annotation"):
                    kind = st.selectbox("Review action", ["comment", "decision", "override"])
                    claim_id = st.selectbox("Related claim", [c.claim_id for c in analysis.claims])
                    note = st.text_area("Comment, decision or override reason", max_chars=5000)
                    override = st.selectbox("Reviewer override status (used only for override)", list(Status), format_func=lambda s: s.value)
                    submitted = st.form_submit_button("Record review action", disabled=role == "viewer")
                if submitted:
                    try:
                        annotation = ReviewAnnotation(claim_id=claim_id, kind=kind, text=note,
                            override_status=override if kind == "override" else None, author_id=principal.user_id)
                        st.session_state.snapshot = cases.update(principal.user_id, snapshot.case_id, snapshot.version, snapshot.analysis, annotation=annotation)
                        st.rerun()
                    except (ValueError, ConflictError) as exc:
                        st.error(str(exc)[:400])
                for note in snapshot.annotations:
                    st.text(f"{note.created_at.isoformat()} · {note.kind} · {note.claim_id} · {note.author_id}\n{note.text}")
                    if note.override_status:
                        st.warning("Reviewer override: " + note.override_status.value + ". Computed verdict is unchanged.")
                with st.expander("Members and permissions"):
                    st.dataframe(cases.members(principal.user_id, snapshot.case_id), hide_index=True)
                    target = st.text_input("Recipient's signed-in user ID")
                    member_role = st.selectbox("Membership", ["viewer", "reviewer", "remove"])
                    if st.button("Update membership", disabled=role != "owner"):
                        try:
                            cases.set_member(principal.user_id, snapshot.case_id, target, None if member_role == "remove" else member_role)
                            st.rerun()
                        except (ValueError, AccessDenied) as exc:
                            st.error(str(exc))
                with st.expander("Audit history"):
                    st.dataframe(cases.audit(principal.user_id, snapshot.case_id), hide_index=True)
                confirm = st.checkbox("Delete this saved case and its source files")
                if st.button("Delete case", disabled=role != "owner" or not confirm):
                    cases.delete_case(principal.user_id, snapshot.case_id)
                    clear_draft()
                    st.rerun()
            elif st.button("Save case", disabled=busy):
                try:
                    st.session_state.snapshot = cases.create(principal.user_id, title, analysis, source_bytes)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    with export_tab:
        audit = cases.audit(principal.user_id, snapshot.case_id) if snapshot and principal else []
        ui.show_exports(analysis, source_bytes, snapshot.annotations if snapshot else [], audit)
else:
    for tab in (findings, sources_tab, gaps_tab, trail_tab, lab_tab, review_tab, export_tab):
        with tab:
            st.info("Upload sources in Intake, or load the fictional meal walkthrough from the sidebar.")

with admin_tab:
    st.subheader("Processing and retention")
    st.text(f"Saved-case retention: {cases.retention_days()} days\nDraft jobs: up to 24 hours if abandoned\nAudit metadata: up to 90 days\nOCR language: {settings.ocr_language}")
    st.caption("Reset clears this browser's working state; consumed job content is removed. RAM and third-party logs are not guaranteed to be securely erased.")
    if user and user["admin"]:
        hosts = st.text_area("Approved retrieval hostnames, one per line", value="\n".join(cases.allowed_hosts()))
        retention = st.number_input("Retention for new saved cases (days)", min_value=1, max_value=3650, value=cases.retention_days())
        if st.button("Save administrator settings"):
            try:
                cases.admin_settings(principal.user_id, hosts=[h.strip().lower() for h in hosts.splitlines() if h.strip()], retention_days=retention)
                st.success("Settings saved; existing expiry dates remain as recorded")
            except ValueError as exc:
                st.error(str(exc))
    else:
        st.caption("An administrator can configure the retrieval allowlist and retention. Use the admin bootstrap command for the first administrator.")

