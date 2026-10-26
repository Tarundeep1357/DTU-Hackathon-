import hashlib
import io
import json
import zipfile

from evidence_arena.core.compare import check_edge
from evidence_arena.core.contracts import Analysis
from evidence_arena.core.verdict import adjudicate


def verified_analysis(analysis: Analysis) -> Analysis:
    """Re-run trust boundaries for rendering, saving and exporting."""
    result = analysis.model_copy(deep=True)
    sources = {s.source_id: s for s in result.sources}
    passages = {p.passage_id: p for p in result.passages}
    claims = {c.claim_id: c for c in result.claims}
    if len(sources) != len(result.sources) or len(passages) != len(result.passages) or len(claims) != len(result.claims):
        raise ValueError("Duplicate source, passage or claim identifiers")
    for p in result.passages:
        if p.source_id not in sources or p.page > sources[p.source_id].page_count or p.char_end - p.char_start != len(p.text):
            raise ValueError("Broken passage provenance")
    checked = []
    old_verdicts = {v.claim_id: v for v in result.verdicts}
    for old in result.edges:
        if old.edge.claim_id not in claims:
            raise ValueError("Evidence references an unknown claim")
        current = check_edge(claims[old.edge.claim_id], old.edge, passages)
        # A failed candidate-whitelist check must never be laundered on export.
        previous = old_verdicts.get(old.edge.claim_id)
        if previous and old.edge.passage_id not in previous.inspected_passage_ids:
            current.citation_valid = False
            current.comparable = False
            current.reasons.append("Passage was not part of the reviewed candidate set")
        checked.append(current)
    result.edges = checked
    incomplete = any(any("not reliably extracted" in w or "replacement characters" in w for w in s.warnings) for s in result.sources)
    result.verdicts = [adjudicate(c, checked, old_verdicts[c.claim_id].inspected_passage_ids if c.claim_id in old_verdicts else [],
                                 extraction_incomplete=incomplete) for c in result.claims]
    result.gaps = [g for g in result.gaps if g.claim_id in claims]
    return result


def export_payload(analysis, annotations=None, audit=None):
    safe = verified_analysis(analysis)
    body = safe.model_dump(mode="json")
    body["edges"] = [e.model_dump(mode="json") for e in safe.edges if e.citation_valid]
    body["suppressed_citations"] = sum(not e.citation_valid for e in safe.edges)
    return {"format_version": "2.0", "analysis": body,
            "review_annotations": [a.model_dump(mode="json") for a in (annotations or [])], "audit_events": audit or [],
            "notice": "Source-scoped review. Document authenticity and source independence are not certified."}


def json_export(analysis, annotations=None, audit=None):
    return json.dumps(export_payload(analysis, annotations, audit), ensure_ascii=False, indent=2).encode("utf-8")


def markdown_export(analysis, annotations=None, audit=None):
    payload = export_payload(analysis, annotations, audit)
    a = payload["analysis"]
    claims = {c["claim_id"]: c for c in a["claims"]}
    passages = {p["passage_id"]: p for p in a["passages"]}
    sources = {s["source_id"]: s for s in a["sources"]}
    lines = ["# Evidence Arena review", "", a["question"], "", f"Mode: {a['mode']}", "", payload["notice"], ""]
    for v in a["verdicts"]:
        lines += [f"## {v['claim_id']} — {v['status']}", "", claims[v["claim_id"]]["text"], "", v["reasoning"], "", v["strength"], ""]
        for edge in a["edges"]:
            e = edge["edge"]
            if e["claim_id"] != v["claim_id"]:
                continue
            p = passages[e["passage_id"]]
            lines += [f"{e['relation']} — {sources[p['source_id']]['filename']}, page {p['page']} ({p['extraction_method']})", "",
                      *["> " + line for line in e["quote"].splitlines()], ""]
        lines += ["Limitations: " + "; ".join(v["limitations"]), ""]
    for g in a["gaps"]:
        lines += [f"## Next evidence for {g['claim_id']}", "", g["requested_record"], "", g["why"], "",
                  "Outcome A: " + g["outcome_a"]["observation"] + " " + g["outcome_a"]["effect"], "",
                  "Outcome B: " + g["outcome_b"]["observation"] + " " + g["outcome_b"]["effect"], ""]
    lines += ["## Review history", "", *[f"- {r['created_at']} — {r['kind']}: {r['text']}" for r in payload["review_annotations"]], ""]
    return "\n".join(lines).encode("utf-8")


def evidence_packet(analysis, source_bytes, annotations=None, audit=None):
    safe = verified_analysis(analysis)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("review.json", json_export(safe, annotations, audit))
        archive.writestr("review.md", markdown_export(safe, annotations, audit))
        manifest = []
        for source in safe.sources:
            content = source_bytes.get(source.source_id)
            if content is None or hashlib.sha256(content).hexdigest() != source.sha256:
                raise ValueError("Packet source bytes are missing or failed integrity validation")
            # Never use an uploaded filename as a ZIP path.
            path = f"sources/{source.source_id}.{source.file_type}"
            archive.writestr(path, content)
            manifest.append({"path": path, "original_filename": source.filename, "sha256": source.sha256,
                             "acquisition_url": source.acquisition_url, "acquired_at": source.acquired_at.isoformat()})
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buffer.getvalue()

