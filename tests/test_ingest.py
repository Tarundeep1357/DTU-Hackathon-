import io
import shutil
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from evidence_arena.config import Settings
from evidence_arena.core.ingest import IngestionError, ingest, ingest_many


def pdf_bytes(text=None, encrypted=False):
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((60, 80), text)
    content = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner") if encrypted else doc.tobytes()
    doc.close()
    return content


def test_text_offsets_and_stable_ids():
    text = "  " + "word " * 500
    first, second = [ingest("test.txt", text.encode(), Settings()) for _ in range(2)]
    assert len(first.passages) == 3
    assert first.passages == second.passages
    for p in first.passages:
        assert text[p.char_start:p.char_end] == p.text


@pytest.mark.parametrize("name,data", [("bad.exe", b"abc"), ("bad.pdf", b"not pdf"), ("bad.txt", b"%PDF-1.7"), ("binary.txt", b"a\x00b"), ("invalid.txt", b"\xff"), ("empty.txt", b"")])
def test_bad_uploads(name, data):
    with pytest.raises(IngestionError):
        ingest(name, data, Settings())


def test_pdf_extraction_and_page_location():
    item = ingest("input.pdf", pdf_bytes("A reliable supporting source passage for this test."), Settings())
    assert item.passages[0].page == 1
    assert item.source.page_count == 1


def test_encrypted_and_damaged_pdf():
    with pytest.raises(IngestionError, match="Encrypted"):
        ingest("encrypted.pdf", pdf_bytes(encrypted=True), Settings())
    with pytest.raises(IngestionError):
        ingest("damaged.pdf", b"%PDF-1.7 damaged", Settings())


def test_limits_and_duplicates():
    with pytest.raises(IngestionError):
        ingest_many([("a.txt", b"a")] * 4, Settings())
    with pytest.raises(IngestionError):
        ingest("large.txt", b"x" * (1024 * 1024 + 1), Settings(max_file_mb=1))
    items = ingest_many([("a.txt", b"same data"), ("b.txt", b"same data")], Settings())
    assert items[1].source.duplicate_of == items[0].source.source_id
    assert not items[1].passages
    assert items[0].source.source_id != items[1].source.source_id


def test_scan_without_ocr():
    item = ingest("scan.pdf", pdf_bytes(), Settings(), ocr=False)
    assert not item.passages
    assert item.images[1].startswith(b"\x89PNG")
    assert any("not reliably extracted" in w for w in item.source.warnings)


def test_ocr_missing_runtime(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("missing tesseract")
    monkeypatch.setattr(pymupdf.Page, "get_textpage_ocr", unavailable)
    item = ingest("scan.pdf", pdf_bytes(), Settings(), ocr=True)
    assert any("OCR unavailable" in w for w in item.source.warnings)
    assert not item.passages


def test_ocr_result_is_labelled(monkeypatch):
    # Exercise integration plumbing without pretending a native OCR engine ran.
    document = pymupdf.open(stream=pdf_bytes("Recognized OCR passage with enough text for inspection."), filetype="pdf")
    textpage = document[0].get_textpage()
    original_get_text = pymupdf.Page.get_text
    monkeypatch.setattr(pymupdf.Page, "get_textpage_ocr", lambda *a, **k: textpage)
    monkeypatch.setattr(pymupdf.Page, "get_text", lambda self, *a, **k: "Recognized OCR passage with enough text for inspection." if k.get("textpage") is textpage else original_get_text(self, *a, **k))
    item = ingest("scan.pdf", pdf_bytes(), Settings(), ocr=True)
    assert item.passages[0].extraction_method == "ocr"
    assert item.images
    document.close()


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("tesseract") and not Path("data/tessdata/eng.traineddata").exists(), reason="Native OCR language data not installed on this host")
def test_native_ocr():
    image = Image.new("RGB", (1600, 300), "white")
    ImageDraw.Draw(image).text((60, 80), "Kitchen A served 500 meals per day", fill="black", font_size=50)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=800, height=150)
    page.insert_image(page.rect, stream=buffer.getvalue())
    item = ingest("scan.pdf", doc.tobytes(), Settings.from_env(), ocr=True)
    assert any("500" in p.text for p in item.passages)
    doc.close()

