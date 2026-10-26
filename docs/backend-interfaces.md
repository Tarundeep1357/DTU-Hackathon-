# Backend interfaces and operational behavior

All public service methods are Python APIs called server-side. Input text is data. Functions do not grant a model tools.

| Interface | Behavior |
| --- | --- |
| `ingest_many(files, settings, ocr=...)` | Returns source records, exact page-slice passages, raw page text and scan images, with per-page warnings. Rejects invalid limits/signatures. |
| `retrieve(claim, passages, limit)` | Returns bounded TF-IDF candidates and IDs selected in a separate counter-query. Retrieval is not proof. |
| `check_edge(claim, edge, passage_lookup)` | Checks IDs, whitespace-only quote containment, grounded facets and deterministic comparability. |
| `adjudicate(claim, checked_edges, inspected_ids)` | Produces one conservative status. Invalid citations/incomplete extraction force Unverified. |
| `run_analysis(...)` | Decomposes claims, retrieves both sides, asks Gemma for bounded structured evidence, validates, adjudicates and creates gap tests. An explicit claim list supports edited claims/follow-ups. |
| `CaseService` | Identity registration, access-controlled immutable snapshots, optimistic saves, review annotations, memberships, original-file retrieval, audit and admin settings. |
| `JobService` | Owner-scoped submit/get/cancel/consume; atomic claims, heartbeats, two interruption attempts, sanitized failures and content cleanup. |
| `fetch_document(...)` | DNS-pinned allowlisted HTTPS PDF/TXT fetch; result remains outside the evidence until a user approves it. |
| `verified_analysis` / export functions | Re-check evidence at save/render/export boundaries; suppress invalid quotations; package integrity-checked source bytes. |

Jobs use transient database records for resumability. A worker loses ownership if its lease changes and cannot commit a result under another worker's token. Cancellation during a model request takes effect after that bounded request returns. A failed model response does not create a supported-looking fallback.

Case annotations and overrides are attributed to a signed-in user and versioned. Service permissions are owner/reviewer/viewer; administrator access only manages system settings and does not bypass membership. Case deletion removes versions/memberships and source files, while limited audit metadata records the deletion without source content.

