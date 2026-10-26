# Backend validation status

Updated 26 October 2026. These observations distinguish executed tests from integrations that still require environment verification.

| Check | Observed result |
| --- | --- |
| Automated suite | 76 tests passed, including native OCR, API CSRF/isolation/provenance checks, and the legacy guest walkthrough. See `test-results.xml`. |
| Labelled rule benchmark | 12/12 expected statuses; zero seeded false conflicts. This measures rules with prepared proposals, not live model accuracy. |
| SQLite migration | Upgrade → downgrade → upgrade executed successfully. |
| Local application health | React/FastAPI serves on port 8501. API health and worker liveness are available at `/api/health`. |
| Gemma installation | Gemma 4 E2B instruction Q2_K GGUF, 2,989,086,144 bytes, verified SHA-256 `dfd66cc509c7e3361f54f1b7f911e9c6de57e2548b52cefa773d37b394beb410`. |
| Model runtime | Official Ollama 0.40.11 CPU components; loaded `gemma4:e2b-q2-local`, model family `gemma4`, context 8192, approximately 3.6 GB runtime memory. |
| Live structured inference | Q2 build returned valid JSON, but failed an independent factual extraction probe. The smoke command now checks factual output as well as JSON syntax. Official Gemma 4 E2B QAT replacement is being installed. |
| Longer live evidence review | Completed through live inference, conservative Unverified output, case save/load, cross-user rejection and grounded export in 281 seconds. This small Q2 model did not recover useful evidence in that run; do not treat this as passing model-quality acceptance. |
| React production build | TypeScript and Vite build passed. The fictional review loads and shows C1 Contested / C2 Unverified in the browser. |
| Docker Compose configuration | `docker compose config --quiet` passed. |
| PostgreSQL / Keycloak runtime | Not yet verified here. Docker Desktop did not finish starting; its stalled diagnostics were stopped to free memory for Gemma. |
| OIDC application authorization | Issuer/subject identity and role/isolation paths pass automated service tests. Real-provider browser login is not yet verified. |

The local backend can use SQLite and local Gemma without Docker. Saved-case sign-in still requires a configured OIDC provider. Do not describe Docker/OIDC runtime checks as passed until they have actually run.

