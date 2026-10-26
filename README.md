# Evidence Arena 2.0

An evidence review backend with a Streamlit client. Gemma 4 proposes claims and passage relationships; deterministic validation and comparison rules determine source-scoped findings. The original `dd.docx` specification is preserved.

## Local startup on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env   # Skip if .env is already configured
.\.venv\Scripts\python.exe -m evidence_arena.cli migrate
.\start.ps1
```

The app is at http://127.0.0.1:8501. `start.ps1` starts the worker, optionally starts the project-local Ollama runtime on port 11435, and launches Streamlit. It stops its worker when the foreground app exits. For manual control:

```powershell
.\.venv\Scripts\python.exe -m evidence_arena.worker
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Guest draft analysis is available without an account. Saving and sharing cases requires OIDC. The fictional walkthrough never makes a model request and is clearly labelled.

## Gemma 4 on this laptop

This 8 GB RAM machine is configured for the text-only Gemma 4 E2B instruction model in Q2_K quantization (approximately 2.99 GB of weights):

```dotenv
GEMMA_BASE_URL=http://127.0.0.1:11435/v1
GEMMA_MODEL_ID=hf.co/mradermacher/gemma-4-E2B-it-GGUF:Q2_K
GEMMA_API_KEY=local
GEMMA_REMOTE=false
GEMMA_TIMEOUT_SECONDS=120
```

The GGUF is a community quantization of Google's instruction model, distributed through Hugging Face. It trades quality for memory use. Do not infer a quality guarantee from its ability to run. Source: https://huggingface.co/mradermacher/gemma-4-E2B-it-GGUF

The installed system Ollama was too old for Gemma 4. `deploy/extract_cpu_runtime.py` downloads CPU components from the official v0.40.11 release ZIP using resumable byte ranges. HTTPS authenticates the source and ZIP CRC checks each component. This selective path does not verify the checksum of the entire ZIP; `deploy/download_ollama.py` provides a full-archive SHA-256 path for an unrestricted connection.

```powershell
.\.venv\Scripts\python.exe deploy\extract_cpu_runtime.py
# Start the local runtime via start.ps1, or serve it with OLLAMA_HOST and OLLAMA_MODELS set.
.\.venv\Scripts\python.exe deploy\pull_model.py
.\.venv\Scripts\python.exe deploy\import_local_model.py  # Recovery if metadata redirects fail after the weights download
.\.venv\Scripts\python.exe -m evidence_arena.cli smoke
```

Keep the runtime on loopback. CPU inference may be slow on this memory-constrained machine. The adapter accepts only Gemma 4 model identifiers and rejects endpoint responses identifying another model family. A configured endpoint is not proof of successful inference; use the smoke test and validation report.

For a remote OpenAI-compatible Gemma provider, set its HTTPS base URL ending in `/v1`, exact Gemma 4 model ID, key, `GEMMA_REMOTE=true`, and an HTTPS `GEMMA_PRIVACY_URL`. Remote processing requires client consent. There is no silent model substitution or automatic sample fallback.

## Backend modules

- `core/`: typed contracts, ingestion, TF-IDF/counterevidence retrieval, citation validation, facet comparison, verdicts, gap tests and domain templates.
- `services/`: Gemma transport, orchestration, authorization, saved versions, source storage, audit, jobs, safe retrieval and exports.
- `storage.py`: SQLAlchemy records; `alembic/`: explicit schema migrations.
- `worker.py`: leased jobs, progress, cancellation, interruption recovery and scheduled retention cleanup.

The Python service interface is the backend API. A separate public HTTP API is not included. This keeps model credentials and case operations server-side.

## OCR

PyMuPDF includes the OCR integration; it needs English Tesseract language data. The project uses `data/tessdata/eng.traineddata` when present, or `TESSDATA_PREFIX`. Docker installs Tesseract and English data. Local language data comes from the official `tesseract-ocr/tessdata_fast` repository. OCR passages are labelled, with original page images available for inspection.

## OIDC and saved cases

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and configure your provider's discovery URL, client credentials, redirect URI and a random cookie secret. Use `http://localhost:8501/oauth2callback` for loopback development. Production requires HTTPS and provider registration. Identity is keyed by issuer plus subject, not email. Never commit secrets.

Case owners can add existing signed-in users by their user ID as reviewers/viewers, remove them, and delete cases. Reviewers can save versions and annotations; viewers can inspect/export accessible data. Overrides require a claim and reason and remain separate from computed verdicts. Concurrent saves require the expected version and fail rather than overwrite another review.

```powershell
.\.venv\Scripts\python.exe -m evidence_arena.cli bootstrap-admin --issuer YOUR_ISSUER --subject YOUR_SUBJECT
```

Administrators configure the exact-host retrieval allowlist and retention for new cases. Administrator status does not bypass case membership to read other users' source files.

## Docker and development identity provider

Set `POSTGRES_PASSWORD` and `KEYCLOAK_ADMIN_PASSWORD` in `.env` (random URL-safe values). Create `.streamlit/secrets.toml` even if empty for guest mode, then:

```powershell
docker compose up --build -d db migrate app worker
```

For local OIDC, copy `deploy/secrets.dev.toml.example` to `.streamlit/secrets.toml`, replace the cookie secret, and run:

```powershell
docker compose --profile oidc-dev up --build -d
```

Keycloak is reachable at http://keycloak.localhost:8080 and imports the `evidence` realm. Its Docker network alias lets the app resolve the same issuer hostname as the browser. Register a local test user using the provider's registration page. The included client secret is **development only**; replace it and configure HTTPS before exposing any service. Only app/OIDC ports are bound, and both use loopback.

The app container uses PostgreSQL and a named source volume. The worker shares that volume. The migration job must complete before app/worker startup. Windows host Ollama is reached through `host.docker.internal`; its listener must be explicitly configured for container access before this path can work. The local default listener intentionally remains loopback only. A remote HTTPS provider is the simplest Docker integration without changing host exposure.

## Tests and evaluation

```powershell
.\.venv\Scripts\python.exe -m pytest --junitxml=docs/test-results.xml
.\.venv\Scripts\python.exe -m evidence_arena.cli evaluate
.\.venv\Scripts\python.exe -m evidence_arena.cli smoke
.\.venv\Scripts\python.exe -m evidence_arena.cli purge
```

`docs/evaluation-results.json` measures deterministic rules on labelled synthetic proposals; it is not a benchmark of live Gemma extraction quality. `docs/validation.md` distinguishes executed checks from configuration-only integrations. Do not describe skipped checks as passed.

## Privacy and limits

Defaults: three files, 10 MB each, twenty PDF pages, five claims, six candidate passages per claim. TXT must be UTF-8. Encrypted/damaged inputs fail clearly. Empty/poor extraction blocks confident findings. Duplicate bytes preserve upload labels without counting as independent corroboration.

Draft jobs temporarily persist payloads/results for recovery, up to 24 hours if abandoned. Consumed or cancelled jobs clear content; saved cases expire after thirty days by default. Technical audit metadata expires after ninety days. Source originals live outside the database under opaque identifiers, and service methods check case access and SHA-256 before retrieval. Backup and third-party retention remain deployment responsibilities. Secure erasure from RAM/disks is not promised.

Uploaded text is untrusted data and never executed. The model has no tools or filesystem/web privileges. Safe retrieval is a separate user-initiated operation: exact-host HTTPS allowlist, public DNS addresses only, pinned destination IP with TLS hostname validation, no redirects, and size/time/content-type caps. Retrieved material requires explicit review before addition to evidence.

Supported means supported in reviewed comparable passages, not proven true. Source traceability does not authenticate a document. There are no truth scores, calibrated probabilities, autonomous high-stakes decisions, unrestricted crawling, or bulk spreadsheet import.

