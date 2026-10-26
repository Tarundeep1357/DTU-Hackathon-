"""Streamlit rendering. Source text is rendered as plain text, never raw HTML."""
import base64
import hashlib
import json

import pymupdf
import streamlit as st

from evidence_arena.core.contracts import Analysis, Claim, Facets, ReviewAnnotation, Status
from evidence_arena.core.templates import TEMPLATES
from evidence_arena.services.exports import evidence_packet, json_export, markdown_export, verified_analysis


STYLE = """<style>
.block-container {max-width: 1380px; padding: 2.4rem 3rem 3rem;}
h1,h2,h3 {letter-spacing: -.025em; font-weight:600;}
h1 {font-size:2.25rem !important;} h2 {font-size:1.55rem !important;} h3 {font-size:1.1rem !important;} p,li {line-height:1.55;}
header[data-testid='stHeader'] {background:rgba(250,250,250,.92);}
[data-testid='stSidebar'] {background:#18242C; min-width:238px !important; max-width:258px !important; border-right:1px solid #243741;}
[data-testid='stSidebar'] h1 {font-size:1.4rem !important;color:#F5F7F8;}
[data-testid='stSidebar'] p, [data-testid='stSidebar'] label, [data-testid='stSidebar'] [data-testid='stCaptionContainer'] {color:#C2CDD3 !important;}
[data-testid='stSidebar'] hr {border-color:#33454F;}
[data-testid='stSidebar'] button {background:#233640;color:#E8F1F5;border-color:#405560;border-radius:6px;}
[data-testid='stSidebar'] button:hover {background:#304C5A;border-color:#7897A8;}
[data-testid='stSidebar'] [data-testid='stRadio'] label {padding:.42rem .55rem;border-radius:5px;}
[data-testid='stSidebar'] [data-testid='stRadio'] label:has(input:checked) {background:#2D4653;}
[data-testid='stSidebar'] [data-testid='stRadio'] [role='radiogroup'] {gap:2px;}
[data-testid='stSidebar'] [data-testid='stRadio'] label div:first-child {display:none;}
button:focus-visible, input:focus-visible, textarea:focus-visible {outline:2px solid #235B76 !important; outline-offset:3px;}
div[data-testid='stMetric'] {border-bottom: 2px solid #B9CCD5; padding: .3rem 0 .8rem;}
div[data-testid='stMetricValue'] {font-size:2rem; font-weight:600;}
div[data-testid='stCode'] {border:1px solid #DAE2E6;background:#F5F7F8;border-radius:5px;}
div[data-testid='stVerticalBlockBorderWrapper'] {border-radius:7px;}
[data-testid='stExpander'] {border-color:#D8E0E4;border-radius:6px;}
button[kind='primary'] {background:#235B76;border-radius:5px;}
.ea-eyebrow {font-size:11px;font-weight:600;letter-spacing:.14em;color:#537183;text-transform:uppercase;margin-bottom:.7rem;}
.ea-stage {display:flex;gap:0;border:1px solid #D7E0E5;border-radius:6px;background:white;margin:1rem 0 1.5rem;overflow:hidden;}
.ea-stage span {padding:10px 16px;border-right:1px solid #E3E8EC;flex:1;font-size:12px;color:#5A6C76;}
.ea-stage b {color:#244E64;margin-right:7px;}
.ea-summary {border-left:3px solid #235B76;padding:.1rem 1rem;margin:1.2rem 0 1.5rem;color:#354D5B;font-size:14px;}
.ea-badge {display:inline-block;font-size:11px;letter-spacing:.04em;border:1px solid #C6D7E0;color:#235B76;padding:4px 8px;border-radius:4px;}
@media(max-width:700px){.block-container{padding:1rem;}h1{font-size:1.8rem !important;}}
</style>"""


def scope_line(analysis):
    ids = {p for v in analysis.verdicts for p in v.inspected_passage_ids}
    independent_uploads = sum(not s.duplicate_of for s in analysis.sources)
    return f"Based on {len(analysis.sources)} supplied files ({independent_uploads} distinct byte sequences) / {len(ids)} reviewed of {len(analysis.passages)} extracted passages. No external verification."


def show_findings(analysis, *, prefix="main"):
    analysis = verified_analysis(analysis)
    if analysis.mode == "prepared_walkthrough":
        st.warning("PREPARED FICTIONAL WALKTHROUGH · fixed proposals, locally executed validation; no live Gemma inference")
    else:
        st.caption(f"Live Gemma 4 analysis · {analysis.model_id}")
    st.text(analysis.question)
    st.caption(scope_line(analysis))
    columns = st.columns(4)
    for column, status in zip(columns, Status):
        column.metric(status.value, sum(v.status == status for v in analysis.verdicts))
    lookup = {p.passage_id: p for p in analysis.passages}
    sources = {s.source_id: s for s in analysis.sources}
    for claim in analysis.claims:
        verdict = next(v for v in analysis.verdicts if v.claim_id == claim.claim_id)
        with st.container(border=True):
            st.subheader(f"{claim.claim_id} · {verdict.status.value}")
            st.text(claim.text)
            f = claim.facets
            st.caption(" · ".join(str(x) for x in [f.subject, f.metric, f.unit,
                       f"{f.period_start} → {f.period_end}" if f.period_start else "Period not specified"] if x))
            st.write(verdict.reasoning)
            st.caption(verdict.strength)
            for index, checked in enumerate(analysis.edges):
                if checked.edge.claim_id != claim.claim_id:
                    continue
                if not checked.citation_valid:
                    st.error("Source-validation error: an unsupported quotation was suppressed. Claim remains Unverified.")
                    continue
                edge = checked.edge
                passage = lookup[edge.passage_id]
                source = sources[passage.source_id]
                st.caption(f"{edge.relation.upper()} · {source.filename} · page {passage.page} · {passage.extraction_method.upper()}")
                st.code(edge.quote, language=None, wrap_lines=True)
                if not checked.comparable:
                    st.caption("Not directly comparable: " + "; ".join(checked.reasons))
                def inspect_source(pid=passage.passage_id):
                    st.session_state.workspace_nav = "Sources"
                    st.session_state.source_selection = pid
                    st.session_state[f"{prefix}_source_select"] = pid
                if st.button("Inspect source passage →", key=f"{prefix}_inspect_{index}", on_click=inspect_source):
                    st.session_state.source_selection = passage.passage_id
                    st.session_state[f"{prefix}_source_select"] = passage.passage_id
                    st.session_state.show_source_hint = True
            with st.expander("Scope and limitations"):
                for limitation in verdict.limitations:
                    st.text("• " + limitation)
    if st.session_state.get("show_source_hint"):
        st.info("Open the Sources tab to inspect the selected passage and original file.")


def show_sources(analysis, source_bytes, *, prefix="main"):
    choices = [p.passage_id for p in analysis.passages]
    source_lookup = {s.source_id: s for s in analysis.sources}
    if not choices:
        st.warning("No readable passages. Review extraction warnings and try OCR.")
    else:
        selected = st.session_state.get("source_selection")
        selected = selected if selected in choices else choices[0]
        passage_id = st.selectbox("Passage", choices, index=choices.index(selected),
                                  format_func=lambda pid: f"{source_lookup[next(p.source_id for p in analysis.passages if p.passage_id == pid)].filename} · {pid}",
                                  key=f"{prefix}_source_select")
        passage = next(p for p in analysis.passages if p.passage_id == passage_id)
        st.caption(f"Page {passage.page} · page characters {passage.char_start}–{passage.char_end} · {passage.extraction_method.upper()}")
        st.code(passage.text, language=None, wrap_lines=True)
        data = source_bytes.get(passage.source_id)
        if data and source_lookup[passage.source_id].file_type == "pdf":
            try:
                with pymupdf.open(stream=data, filetype="pdf") as pdf:
                    page = pdf[passage.page - 1]
                    scale = min(1.5, 1400 / max(page.rect.width, page.rect.height, 1))
                    image = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
                st.image(image, caption="Original page appearance. OCR text can contain recognition errors.")
            except Exception:
                st.warning("Page image unavailable; download the original file for inspection.")
    st.subheader("Acquisition and integrity")
    for source in analysis.sources:
        with st.expander(source.filename):
            st.text(f"Source ID: {source.source_id}\nSHA-256: {source.sha256}\nAcquired: {source.acquired_at.isoformat()}\nLabel: {source.acquisition_label}")
            if source.acquisition_url:
                st.text("Acquisition URL: " + source.acquisition_url)
                st.caption("Freshness: this acquisition timestamp records retrieval, not the original publication date.")
            if source.duplicate_of:
                st.warning("Duplicate of " + source.duplicate_of + "; does not provide independent corroboration.")
            for warning in source.warnings:
                st.warning(warning)
            data = source_bytes.get(source.source_id)
            if data:
                if hashlib.sha256(data).hexdigest() == source.sha256:
                    st.success("Stored bytes match the acquisition hash. This does not certify authenticity.")
                    st.download_button("Download original", data, file_name=source.filename,
                                       mime="application/pdf" if source.file_type == "pdf" else "text/plain", key=f"{prefix}_download_{source.source_id}")
                else:
                    st.error("Integrity mismatch; original-file download suppressed.")


def show_gaps(analysis):
    if not analysis.gaps:
        st.info("No unresolved gap tests were returned for this review. Supporting findings are still source-scoped.")
    for gap in analysis.gaps:
        st.subheader(f"What would change {gap.claim_id}?")
        st.text(gap.requested_record)
        st.text(gap.why)
        st.caption("Expected source: " + gap.expected_source)
        with st.expander("Competing explanations"):
            for hypothesis in gap.hypotheses:
                st.text("• " + hypothesis)
        a, b = st.columns(2)
        for column, label, outcome in [(a, "Outcome A", gap.outcome_a), (b, "Outcome B", gap.outcome_b)]:
            with column:
                st.markdown(f"**{label}**")
                st.text(outcome.observation)
                st.text(outcome.effect)
        st.divider()


def show_graph(analysis):
    q = json.dumps
    lines = ["digraph Evidence {", 'rankdir="LR"; node [shape="box",fontname="Arial"];']
    for c in analysis.claims:
        lines.append(f"{q(c.claim_id)} [label={q(c.claim_id + ': ' + c.text[:90])}];")
    for source in analysis.sources:
        lines.append(f"{q(source.source_id)} [label={q(source.filename)},shape=folder];")
    sources = {p.passage_id: p.source_id for p in analysis.passages}
    for checked in analysis.edges:
        if checked.citation_valid:
            edge = checked.edge
            label = edge.relation if checked.comparable else "context / scope mismatch"
            lines.append(f"{q(sources[edge.passage_id])} -> {q(edge.claim_id)} [label={q(label)}];")
    lines.append("}")
    st.graphviz_chart("\n".join(lines))
    st.caption("Edges reflect validated source references. This graph does not certify source independence.")


def show_simulation(analysis):
    st.warning("SIMULATION · hypothetical inputs remain separate from uploaded evidence and saved findings")
    claim = st.selectbox("Claim to simulate", analysis.claims, format_func=lambda c: c.claim_id + ": " + c.text, key="sim_claim")
    observation = st.text_area("Hypothetical observation", key="sim_observation", placeholder="A complete signed ledger reports a different measured result…")
    relation = st.radio("Assume this observation provides", ["support", "opposition", "inconclusive evidence"], horizontal=True)
    comparable = st.checkbox("Assume the same metric, population, period and observation definition")
    if st.button("Calculate conditional result", disabled=not observation.strip()):
        original = next(v for v in analysis.verdicts if v.claim_id == claim.claim_id)
        support, oppose = bool(original.supporting_ids), bool(original.opposing_ids)
        if comparable:
            support |= relation == "support"
            oppose |= relation == "opposition"
        conditional = Status.CONTESTED if support and oppose else Status.SUPPORTED if support else Status.CONTRADICTED if oppose else Status.UNVERIFIED
        st.session_state.simulation = {"claim": claim.claim_id, "original": original.status.value,
            "conditional": conditional.value if comparable else original.status.value, "observation": observation,
            "notice": "Conditional only: assumes provenance and comparability; unresolved extraction/validation errors still need correction."}
    if st.session_state.get("simulation"):
        st.json(st.session_state.simulation)


def edit_claims(claims):
    rows = [{"claim_id": c.claim_id, "text": c.text, **{k: str(v) if v is not None else "" for k, v in c.facets.model_dump().items()}} for c in claims]
    edited = st.data_editor(rows, disabled=["claim_id"], hide_index=True, use_container_width=True, key="claims_editor")
    result = []
    for row in edited:
        facets = {k: v if v != "" else None for k, v in row.items() if k not in {"claim_id", "text"}}
        if isinstance(facets.get("observed"), str):
            if facets["observed"].lower() not in {"true", "false"}:
                raise ValueError("Observed must be True, False or blank")
            facets["observed"] = facets["observed"].lower() == "true"
        result.append(Claim(claim_id=row["claim_id"], text=row["text"], facets=Facets.model_validate(facets)))
    return result


def show_exports(analysis, source_bytes, annotations=None, audit=None):
    st.caption("Exports revalidate source references and suppress invalid quotations. Reviewer overrides are kept separate from computed verdicts.")
    a, b, c = st.columns(3)
    a.download_button("JSON review", json_export(analysis, annotations, audit), "evidence-review.json", "application/json")
    b.download_button("Markdown review", markdown_export(analysis, annotations, audit), "evidence-review.md", "text/markdown")
    if len(source_bytes) == len(analysis.sources):
        c.download_button("Evidence packet ZIP", evidence_packet(analysis, source_bytes, annotations, audit), "evidence-packet.zip", "application/zip")

