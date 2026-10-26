"""Bounded parsing. Raw page text is never rewritten to repair a quotation."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

import pymupdf

from evidence_arena.config import Settings
from .contracts import Passage, Source


class IngestionError(ValueError):
    pass


@dataclass
class Ingested:
    source: Source
    passages: list[Passage]
    pages: dict[int, str]
    images: dict[int, bytes]


def chunk_page(text: str, source_id: str, page: int, method="text") -> list[Passage]:
    # Split at whitespace boundaries; slices refer to untouched page text.
    words = list(re.finditer(r"\S+", text))
    chunks = []
    for offset in range(0, len(words), 220):
        group = words[offset:offset + 220]
        start, end = group[0].start(), group[-1].end()
        chunks.append(Passage(passage_id=f"{source_id}:p{page}:{start}-{end}",
                              source_id=source_id, page=page, char_start=start,
                              char_end=end, text=text[start:end], extraction_method=method))
    return chunks


def ingest(filename: str, data: bytes, settings: Settings, *, ocr=False,
           acquisition_label="User upload", acquisition_url=None) -> Ingested:
    if not data or len(data) > settings.max_file_mb * 1024 * 1024:
        raise IngestionError(f"File must contain data and be at most {settings.max_file_mb} MB")
    filename = Path(filename.replace("\\", "/")).name[:200]
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".txt"}:
        raise IngestionError("Only PDF and UTF-8 TXT files are supported")
    digest = hashlib.sha256(data).hexdigest()
    source_id = f"S_{digest[:20]}"
    warnings, passages, pages, images = [], [], {}, {}
    if suffix == ".txt":
        if data.startswith(b"%PDF-") or b"\x00" in data:
            raise IngestionError("File content does not match UTF-8 plain text")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise IngestionError("TXT must use UTF-8 encoding") from exc
        if any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
            raise IngestionError("Binary control characters found in TXT")
        pages[1] = text
        passages = chunk_page(text, source_id, 1)
        count = 1
    else:
        if not data.startswith(b"%PDF-"):
            raise IngestionError("PDF signature does not match the file extension")
        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass:
                    raise IngestionError("Encrypted PDFs are unsupported; upload an unlocked copy")
                count = len(doc)
                if count < 1 or count > settings.max_pdf_pages:
                    raise IngestionError(f"PDF must have 1–{settings.max_pdf_pages} pages")
                for page_index, page in enumerate(doc):
                    number, method = page_index + 1, "text"
                    text = page.get_text(sort=True)
                    if len(text.strip()) < 25:
                        if ocr and settings.ocr_enabled:
                            try:
                                textpage = page.get_textpage_ocr(language=settings.ocr_language, dpi=150, full=True, tessdata=settings.tessdata_dir)
                                text = page.get_text(textpage=textpage, sort=True)
                                method = "ocr"
                                warnings.append(f"Page {number}: OCR text; inspect the page image for recognition errors")
                            except Exception:
                                warnings.append(f"Page {number}: OCR unavailable or failed; install Tesseract language data")
                        else:
                            warnings.append(f"Page {number}: little extractable text; request OCR or a text-based source")
                        # Bound image dimensions and memory independently of the PDF page size.
                        scale = min(1.5, 1400 / max(page.rect.width, page.rect.height, 1))
                        images[number] = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
                    if len(text.strip()) < 25:
                        warnings.append(f"Page {number}: text not reliably extracted")
                    if "\ufffd" in text:
                        warnings.append(f"Page {number}: replacement characters; extraction may be unreliable")
                    pages[number] = text
                    passages.extend(chunk_page(text, source_id, number, method))
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError("PDF could not be safely parsed") from exc
    if not passages:
        warnings.append("No readable passages extracted; findings must remain unverified")
    source = Source(source_id=source_id, filename=filename, sha256=digest,
                    file_type=suffix[1:], page_count=count, warnings=warnings,
                    acquisition_label=acquisition_label, acquisition_url=acquisition_url)
    return Ingested(source, passages, pages, images)


def ingest_many(files: list[tuple[str, bytes]], settings: Settings, *, ocr=False) -> list[Ingested]:
    if not 1 <= len(files) <= settings.max_files:
        raise IngestionError(f"Provide 1–{settings.max_files} files")
    results, hashes = [], {}
    for index, (name, data) in enumerate(files):
        item = ingest(name, data, settings, ocr=ocr)
        if item.source.sha256 in hashes:
            original = hashes[item.source.sha256]
            item.source.duplicate_of = original
            # Keep each upload label, but never count duplicate bytes as corroboration.
            item.source.source_id += f"_duplicate{index}"
            item.source.warnings.append("Duplicate bytes; excluded from independent evidence counts")
            item.passages = []
        else:
            hashes[item.source.sha256] = item.source.source_id
        results.append(item)
    return results

