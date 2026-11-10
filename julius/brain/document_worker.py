"""Bounded disposable document parser. Input is a scoped snapshot, never a path."""

import io
import json
import sys
import xml.etree.ElementTree as ET
import zipfile

MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 20000
MAX_PAGES = 20


def extract(raw, suffix):
    chunks = []
    truncated = False
    warnings = []
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("This PDF is encrypted. Provide an unencrypted copy.")
        total = len(reader.pages)
        truncated = total > MAX_PAGES
        for i in range(min(total, MAX_PAGES)):
            page = reader.pages[i]
            stream = page.get_contents()
            if stream is not None and len(stream.get_data()) > 8 * 1024 * 1024:
                raise ValueError("A PDF page exceeds the safe extraction budget.")
            chunks.append({"location": f"page {i + 1}", "text": page.extract_text() or ""})
        empty = [c["location"] for c in chunks if not c["text"].strip()]
        if empty:
            warnings.append(
                "No text layer found on "
                + ", ".join(empty)
                + ". These pages may be scans/images; OCR is not enabled."
            )
    elif suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError("DOCX content exceeds the extraction budget.")
            xml = archive.read(info)
        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            raise ValueError("XML entity declarations are not supported.")
        root = ET.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for i, paragraph in enumerate(root.findall(".//w:p", ns)):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
            if text:
                chunks.append({"location": f"paragraph {i + 1}", "text": text})
            if len(chunks) >= 200:
                truncated = True
                break
        warnings.append(
            "DOCX body text only; headers, footers, comments and layout are not extracted."
        )
    elif suffix in {".txt", ".md", ".csv", ".json", ".log"}:
        if b"\x00" in raw[:8192]:
            raise ValueError("This file contains binary data.")
        text = raw.decode("utf-8-sig")
        for i, line in enumerate(text.splitlines()):
            if line.strip():
                chunks.append({"location": f"line {i + 1}", "text": line})
            if len(chunks) >= 200:
                truncated = True
                break
    else:
        raise ValueError(
            "Supported documents: PDF, DOCX and UTF-8 text. Legacy .doc files and OCR are not supported."
        )
    bounded = []
    remaining = MAX_TEXT
    for chunk in chunks:
        if remaining <= 0:
            truncated = True
            break
        clipped = chunk["text"][:remaining]
        if len(clipped) < len(chunk["text"]):
            truncated = True
        bounded.append(dict(chunk, text=clipped))
        remaining -= len(clipped)
    if not any(c["text"].strip() for c in bounded):
        return {
            "ok": False,
            "code": "no_text",
            "message": "No readable text was extracted. This may require OCR or another format.",
            "data": {"chunks": bounded, "warnings": warnings, "truncated": truncated},
        }
    return {
        "ok": True,
        "code": "extracted",
        "message": "Document text extracted.",
        "data": {"chunks": bounded, "warnings": warnings, "truncated": truncated},
    }


if __name__ == "__main__":
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        resource.setrlimit(resource.RLIMIT_DATA, (256 * 1024 * 1024, 256 * 1024 * 1024))
    except (ImportError, ValueError, OSError):
        pass
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Document exceeds the 20 MiB limit.")
        out = extract(raw, sys.argv[1])
    except Exception as exc:
        out = {
            "ok": False,
            "code": "extraction_failed",
            "message": f"Document extraction failed: {type(exc).__name__}: {exc}",
            "data": {},
        }
    print(json.dumps(out, ensure_ascii=False))
