from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///data/evidence_arena.db"
    storage_dir: Path = Path("data/sources")
    retention_days: int = 30
    max_files: int = 3
    max_file_mb: int = 10
    max_pdf_pages: int = 20
    max_claims: int = 5
    retrieval_limit: int = 6
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    tessdata_dir: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    admin_identities: tuple[str, ...] = ()
    gemma_base_url: str = ""
    gemma_model_id: str = ""
    gemma_api_key: str = ""
    gemma_timeout: float = 45
    gemma_privacy_url: str = ""
    gemma_remote: bool = True

    @classmethod
    def from_env(cls):
        load_dotenv(override=False)
        def integer(name, default, maximum):
            return max(1, min(int(os.getenv(name, str(default))), maximum))
        return cls(
            database_url=os.getenv("EA_DATABASE_URL", cls.database_url),
            storage_dir=Path(os.getenv("EA_STORAGE_DIR", "data/sources")),
            retention_days=integer("EA_RETENTION_DAYS", 30, 3650),
            max_files=integer("EA_MAX_FILES", 3, 20),
            max_file_mb=integer("EA_MAX_FILE_MB", 10, 50),
            max_pdf_pages=integer("EA_MAX_PDF_PAGES", 20, 200),
            max_claims=integer("EA_MAX_CLAIMS", 5, 20),
            retrieval_limit=integer("EA_RETRIEVAL_LIMIT", 6, 12),
            ocr_enabled=os.getenv("EA_OCR_ENABLED", "true").lower() == "true",
            ocr_language=os.getenv("EA_OCR_LANGUAGE", "eng"),
            tessdata_dir=os.getenv("TESSDATA_PREFIX") or (str(Path("data/tessdata").resolve()) if Path("data/tessdata/eng.traineddata").exists() else None),
            allowed_hosts=tuple(h.strip().lower() for h in os.getenv("EA_ALLOWED_HOSTS", "").split(",") if h.strip()),
            admin_identities=tuple(h.strip() for h in os.getenv("EA_ADMIN_IDENTITIES", "").split(",") if h.strip()),
            gemma_base_url=os.getenv("GEMMA_BASE_URL", "").rstrip("/"),
            gemma_model_id=os.getenv("GEMMA_MODEL_ID", ""),
            gemma_api_key=os.getenv("GEMMA_API_KEY", ""),
            gemma_timeout=float(os.getenv("GEMMA_TIMEOUT_SECONDS", "45")),
            gemma_privacy_url=os.getenv("GEMMA_PRIVACY_URL", ""),
            gemma_remote=os.getenv("GEMMA_REMOTE", "true").lower() == "true",
        )

