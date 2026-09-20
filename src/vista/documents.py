"""Bounded text extraction from the documents a recording ships (files/<id>/<name>).

Stdlib only: OOXML (xlsx/docx/pptx) is a zip of XML, CSV/TSV/TXT are text, PDF is
best-effort (string literals in content streams, deflate only). The result is a
small JSON document — sheets/rows, paragraphs, slides, or pages — capped so one
oversized workbook cannot exhaust the worker. Anything else yields metadata only."""

from __future__ import annotations

import csv
import io
import re
import zipfile
import zlib
from xml.etree import ElementTree as ET

MAX_BYTES = 50 * 1024 * 1024
MAX_ROWS = 2000
MAX_COLS = 64
MAX_CELL = 512
MAX_PARAGRAPHS = 2000
MAX_TEXT = 400_000
MAX_SHEETS = 20
MAX_SLIDES = 200
MAX_PAGES = 200

NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

EXTRACTORS = {".xlsx", ".xlsm", ".csv", ".tsv", ".docx", ".pptx", ".pdf", ".txt", ".md", ".json", ".xml"}


def can_extract(ext: str) -> bool:
    return ext.lower() in EXTRACTORS


def extract(data: bytes, ext: str) -> dict:
    """Never raises for malformed input: returns {"kind": ..., "error": ...} instead."""
    ext = ext.lower()
    if len(data) > MAX_BYTES:
        return {"kind": "skipped", "error": f"larger than {MAX_BYTES // 1048576} MB"}
    try:
        if ext in (".xlsx", ".xlsm"):
            return _xlsx(data)
        if ext in (".csv", ".tsv"):
            return _csv(data, "\t" if ext == ".tsv" else ",")
        if ext == ".docx":
            return _docx(data)
        if ext == ".pptx":
            return _pptx(data)
        if ext == ".pdf":
            return _pdf(data)
        if ext in (".txt", ".md", ".json", ".xml"):
            return {"kind": "text", "text": _clip(data.decode("utf-8", "replace"), MAX_TEXT)}
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, zlib.error, UnicodeDecodeError, csv.Error) as exc:
        return {"kind": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}
    return {"kind": "unsupported"}


def summary(result: dict) -> dict:
    """The few numbers the recorder shows next to a file ("3 sheets · 412 rows")."""
    kind = result.get("kind")
    if kind == "workbook":
        return {"kind": kind, "sheets": len(result["sheets"]), "rows": sum(s["row_count"] for s in result["sheets"])}
    if kind == "table":
        return {"kind": kind, "rows": result["row_count"], "columns": len(result["rows"][0]) if result["rows"] else 0}
    if kind == "document":
        return {"kind": kind, "paragraphs": result["paragraph_count"], "words": result["words"]}
    if kind == "presentation":
        return {"kind": kind, "slides": len(result["slides"])}
    if kind == "pdf":
        return {"kind": kind, "pages": result["page_count"], "words": result["words"]}
    if kind == "text":
        return {"kind": kind, "words": len(result["text"].split())}
    return {"kind": kind, "error": result.get("error")}


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n]


def _open_zip(data: bytes) -> zipfile.ZipFile:
    zf = zipfile.ZipFile(io.BytesIO(data))
    if sum(i.file_size for i in zf.infolist()) > 4 * MAX_BYTES:
        raise ValueError("archive expands too far")
    return zf


def _xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    # Untrusted OOXML part: bound its size and refuse DTDs (no entity expansion) so a
    # crafted part cannot blow up the worker. Office never writes a DOCTYPE.
    if zf.getinfo(name).file_size > MAX_BYTES:
        raise ValueError(f"{name} is too large to parse")
    raw = zf.read(name)
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError(f"{name} declares a DTD")
    return ET.fromstring(raw)


def _col_index(ref: str | None) -> int | None:
    """'C7' -> 2; None when the cell has no coordinate."""
    letters = "".join(ch for ch in (ref or "") if ch.isalpha()).upper()
    if not letters:
        return None
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _text_of(el: ET.Element) -> str:
    return "".join(el.itertext())


def _xlsx(data: bytes) -> dict:
    zf = _open_zip(data)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        shared = [_clip(_text_of(si), MAX_CELL) for si in _xml(zf, "xl/sharedStrings.xml").findall("s:si", NS)]
    wb = _xml(zf, "xl/workbook.xml")
    rels = {r.get("Id"): r.get("Target") for r in _xml(zf, "xl/_rels/workbook.xml.rels").findall("rel:Relationship", NS)}
    sheets = []
    for sh in wb.findall("s:sheets/s:sheet", NS)[:MAX_SHEETS]:
        target = rels.get(sh.get(f"{{{NS['r']}}}id"), "")
        part = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        if part not in zf.namelist():
            continue
        rows, truncated = [], False
        for row in _xml(zf, part).iter(f"{{{NS['s']}}}row"):
            if len(rows) >= MAX_ROWS:
                truncated = True
                break
            cells: list[str] = []
            for c in row.findall("s:c", NS):
                col = _col_index(c.get("r"))
                col = len(cells) if col is None else col
                if col >= MAX_COLS:
                    truncated = True
                    break
                cells.extend("" for _ in range(col - len(cells)))
                cells.append(_cell(c, shared))
            rows.append(cells)
        sheets.append({"name": sh.get("name", ""), "rows": rows, "row_count": len(rows), "truncated": truncated})
    return {"kind": "workbook", "sheets": sheets}


def _cell(c: ET.Element, shared: list[str]) -> str:
    t = c.get("t")
    if t == "inlineStr":
        return _clip(_text_of(c), MAX_CELL)
    v = c.find("s:v", NS)
    if v is None or v.text is None:
        return ""
    if t == "s":
        try:
            return shared[int(v.text)]
        except (IndexError, ValueError):
            return ""
    return _clip(v.text, MAX_CELL)


def _csv(data: bytes, delimiter: str) -> dict:
    text = data.decode("utf-8-sig", "replace")
    rows, truncated = [], False
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        if len(rows) >= MAX_ROWS:
            truncated = True
            break
        rows.append([_clip(c, MAX_CELL) for c in row[:MAX_COLS]])
    return {"kind": "table", "rows": rows, "row_count": len(rows), "truncated": truncated}


def _docx(data: bytes) -> dict:
    zf = _open_zip(data)
    body = _xml(zf, "word/document.xml")
    paras = []
    for p in body.iter(f"{{{NS['w']}}}p"):
        if len(paras) >= MAX_PARAGRAPHS:
            break
        t = _text_of(p).strip()
        if t:
            paras.append(_clip(t, MAX_CELL * 8))
    text = "\n".join(paras)
    return {"kind": "document", "paragraphs": paras, "paragraph_count": len(paras), "words": len(text.split())}


def _pptx(data: bytes) -> dict:
    zf = _open_zip(data)
    names = sorted(
        (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
        key=lambda n: int(re.search(r"(\d+)", n).group(1)),
    )
    slides = []
    for i, n in enumerate(names[:MAX_SLIDES], 1):
        texts = [_clip(_text_of(p).strip(), MAX_CELL * 4) for p in _xml(zf, n).iter(f"{{{NS['a']}}}p")]
        notes_part = f"ppt/notesSlides/notesSlide{i}.xml"
        notes = _text_of(_xml(zf, notes_part)).strip() if notes_part in zf.namelist() else ""
        slides.append({"index": i, "text": [t for t in texts if t], "notes": _clip(notes, MAX_CELL * 8)})
    return {"kind": "presentation", "slides": slides}


_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
_TJ = re.compile(rb"\((?:\\.|[^\\)])*\)")


def _pdf(data: bytes) -> dict:
    """Best effort without a PDF library: literal strings from every content
    stream (deflated or plain). Good enough to know what the file was about."""
    page_count = len(re.findall(rb"/Type\s*/Page[^s]", data))
    words: list[str] = []
    for m in _STREAM.finditer(data):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        for lit in _TJ.findall(raw):
            s = lit[1:-1].replace(b"\\(", b"(").replace(b"\\)", b")").decode("latin-1", "replace")
            if s.strip():
                words.append(s)
        if sum(len(w) for w in words) > MAX_TEXT:
            break
    text = _clip(" ".join(words), MAX_TEXT)
    return {"kind": "pdf", "page_count": min(page_count, MAX_PAGES * 50), "text": text, "words": len(text.split())}
