"""Documents seen during a recording: bounded extraction, the files metadata on
the recording, media completion → extraction job, and access rules."""

import io
import zipfile
import zlib

import pytest

from tests.conftest import requires_db
from tests.test_web import bundle, company, objects  # noqa: F401 — fixtures
from vista import documents
from vista.jobs import handlers
from vista.jobs.worker import process_one
from vista.recordings import RecordingUpload

XML = '<?xml version="1.0" encoding="UTF-8"?>'
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def zipped(parts: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in parts.items():
            zf.writestr(name, text)
    return buf.getvalue()


def xlsx(rows_by_sheet: dict[str, list[list[str]]]) -> bytes:
    names = list(rows_by_sheet)
    shared, parts = [], {}
    for i, name in enumerate(names, 1):
        rows_xml = []
        for r, row in enumerate(rows_by_sheet[name], 1):
            cells = []
            for c, value in enumerate(row):
                ref = f"{chr(65 + c)}{r}"
                if value.replace(".", "", 1).isdigit():
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    shared.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
            rows_xml.append(f'<row r="{r}">{"".join(cells)}</row>')
        parts[f"xl/worksheets/sheet{i}.xml"] = f'{XML}<worksheet xmlns="{S}"><sheetData>{"".join(rows_xml)}</sheetData></worksheet>'
    parts["xl/workbook.xml"] = (
        f'{XML}<workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
        + "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1))
        + "</sheets></workbook>"
    )
    parts["xl/_rels/workbook.xml.rels"] = (
        f'{XML}<Relationships xmlns="{REL}">'
        + "".join(f'<Relationship Id="rId{i}" Type="x" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names) + 1))
        + "</Relationships>"
    )
    parts["xl/sharedStrings.xml"] = f'{XML}<sst xmlns="{S}">' + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>"
    return zipped(parts)


def docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    return zipped({"word/document.xml": f'{XML}<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'})


def pptx(slides: list[list[str]], notes: dict[int, str] | None = None) -> bytes:
    parts = {}
    for i, texts in enumerate(slides, 1):
        body = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts)
        parts[f"ppt/slides/slide{i}.xml"] = f'{XML}<p:sld xmlns:p="x" xmlns:a="{A}"><p:txBody>{body}</p:txBody></p:sld>'
    for i, n in (notes or {}).items():
        parts[f"ppt/notesSlides/notesSlide{i}.xml"] = f'{XML}<p:notes xmlns:p="x" xmlns:a="{A}"><a:p><a:t>{n}</a:t></a:p></p:notes>'
    return zipped(parts)


def pdf(texts: list[str]) -> bytes:
    stream = zlib.compress(b"BT " + b" ".join(f"({t}) Tj".encode() for t in texts) + b" ET")
    return b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n2 0 obj << /Type /Page >> endobj\nstream\n" + stream + b"\nendstream\n%%EOF"


def test_extract_workbook_table_document_presentation_pdf_text():
    wb = documents.extract(xlsx({"Ledger": [["Vendor", "Amount"], ["Acme", "120.5"]], "Notes": [["hi"]]}), ".xlsx")
    assert wb["kind"] == "workbook" and [s["name"] for s in wb["sheets"]] == ["Ledger", "Notes"]
    assert wb["sheets"][0]["rows"] == [["Vendor", "Amount"], ["Acme", "120.5"]]
    assert documents.summary(wb) == {"kind": "workbook", "sheets": 2, "rows": 3}

    tbl = documents.extract("\ufeffa,b\n1,2\n".encode(), ".csv")
    assert tbl["rows"] == [["a", "b"], ["1", "2"]] and documents.summary(tbl) == {"kind": "table", "rows": 2, "columns": 2}
    assert documents.extract(b"a\tb\n", ".tsv")["rows"] == [["a", "b"]]

    doc = documents.extract(docx(["Dear vendor,", "", "Please find the invoice."]), ".docx")
    assert doc["paragraphs"] == ["Dear vendor,", "Please find the invoice."] and doc["words"] == 6

    deck = documents.extract(pptx([["Q3 review", "Agenda"], ["Numbers"]], {2: "speak slowly"}), ".pptx")
    assert [s["text"] for s in deck["slides"]] == [["Q3 review", "Agenda"], ["Numbers"]] and deck["slides"][1]["notes"] == "speak slowly"

    p = documents.extract(pdf(["Invoice 1042", "Total 300"]), ".pdf")
    assert p["kind"] == "pdf" and p["page_count"] == 2 and p["text"] == "Invoice 1042 Total 300" and p["words"] == 4

    assert documents.extract(b"# notes\nhello", ".md") == {"kind": "text", "text": "# notes\nhello"}


def test_extract_is_bounded_and_never_raises():
    big = xlsx({"S": [[str(r), "x"] for r in range(documents.MAX_ROWS + 5)]})
    sheet = documents.extract(big, ".xlsx")["sheets"][0]
    assert sheet["row_count"] == documents.MAX_ROWS and sheet["truncated"]
    wide = documents.extract(",".join(["c"] * (documents.MAX_COLS + 3)).encode(), ".csv")
    assert len(wide["rows"][0]) == documents.MAX_COLS
    assert documents.extract(b"not a zip", ".docx")["kind"] == "failed"
    assert documents.extract(zipped({"other.xml": "<a/>"}), ".xlsx")["kind"] == "failed"
    assert documents.extract(zipped({"word/document.xml": "<broken"}), ".docx")["kind"] == "failed"
    assert documents.extract(b"x" * 10, ".exe") == {"kind": "unsupported"}
    assert documents.extract(b"x" * (documents.MAX_BYTES + 1), ".txt")["kind"] == "skipped"
    assert documents.can_extract(".XLSX") and not documents.can_extract(".exe")


def document(**over):
    base = {
        "id": "a1b2c3d4e5f6",
        "name": "Q3 budget.xlsx",
        "ext": ".xlsx",
        "folder": "Documents",
        "first_opened": "2026-09-19T10:00:00Z",
        "last_closed": "2026-09-19T10:05:00Z",
        "seconds": 300,
        "intervals": [{"start": "2026-09-19T10:00:00Z", "end": "2026-09-19T10:05:00Z", "app": "Microsoft Excel"}],
        "sources": ["ax"],
        "snapshot": "files/a1b2c3d4e5f6/Q3_budget.xlsx",
        "sha256": "ab" * 32,
        "size_bytes": 2048,
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    return {**base, **over}


def test_recording_document_schema_rejects_bad_markers_and_paths(bundle):  # noqa: F811
    RecordingUpload.model_validate({**bundle, "files": [document()]})
    for bad in (
        document(last_closed="2026-09-19T09:00:00Z"),
        document(snapshot="files/ffffffffffff/Q3.xlsx"),
        document(snapshot="../etc/passwd"),
        document(path="/Users/me/Documents/Q3.xlsx"),
        document(id="not-hex"),
        document(sources=["dropbox"]),
    ):
        with pytest.raises(ValueError):
            RecordingUpload.model_validate({**bundle, "files": [bad]})


def drain():
    while process_one():
        pass


@requires_db
def test_files_travel_with_the_recording_and_are_extracted_after_media_completes(client, tenant_factory, bundle, objects, monkeypatch):  # noqa: F811
    monkeypatch.setattr("vista.api.recordings.presigned_upload_url", lambda key, ct: f"https://s3.test/put/{key}")
    monkeypatch.setattr("vista.api.recordings.presigned_download_url", lambda key: f"https://s3.test/get/{key}")
    monkeypatch.setattr(handlers, "s3_client", lambda: objects)
    headers, _, _ = tenant_factory()
    deal = company(client, headers)
    csv_doc = document(id="ffffffffffff", name="statement.csv", ext=".csv", snapshot="files/ffffffffffff/statement.csv")
    files = [document(), csv_doc]
    record = client.post(f"/api/deals/{deal}/recordings", headers=headers, json={**bundle, "files": files}).json()
    rid = record["id"]
    assert [f["name"] for f in record["files"]] == ["Q3 budget.xlsx", "statement.csv"]
    assert "path" not in record["files"][0]

    # Nothing uploaded yet → both snapshots are missing; nothing is queued.
    done = client.post(f"/api/recordings/{rid}/media/complete", headers=headers)
    assert done.status_code == 200 and done.json()["queued"] == 0
    assert {f["extraction"]["status"] for f in done.json()["files"]} == {"missing"}
    assert client.get(f"/api/recordings/{rid}/files/a1b2c3d4e5f6/text", headers=headers, follow_redirects=False).status_code == 409

    # Sign + "upload" the workbook only, then complete.
    media = [{"name": "files/a1b2c3d4e5f6/Q3_budget.xlsx", "content_type": files[0]["content_type"], "size_bytes": 2048}]
    signed = client.post(f"/api/recordings/{rid}/media", headers=headers, json={"files": media}).json()
    key = signed["uploads"][0]["url"].split("/put/", 1)[1]
    objects.data[key] = xlsx({"Ledger": [["Vendor", "Amount"], ["Acme", "120.5"]]})
    done = client.post(f"/api/recordings/{rid}/media/complete", headers=headers).json()
    assert done["queued"] == 1
    by_id = {f["id"]: f["extraction"]["status"] for f in done["files"]}
    assert by_id == {"a1b2c3d4e5f6": "queued", "ffffffffffff": "missing"}

    drain()
    listed = {f["id"]: f for f in client.get(f"/api/recordings/{rid}/files", headers=headers).json()}
    ex = listed["a1b2c3d4e5f6"]["extraction"]
    assert ex["status"] == "done" and ex["key"] == f"{key}.extracted.json" and ex["sheets"] == 1 and ex["rows"] == 2
    assert b"Acme" in objects.data[ex["key"]]
    assert listed["ffffffffffff"]["extraction"]["status"] == "missing"
    text = client.get(f"/api/recordings/{rid}/files/a1b2c3d4e5f6/text", headers=headers, follow_redirects=False)
    assert text.status_code == 307 and text.headers["location"].endswith(".extracted.json")
    assert client.get(f"/api/recordings/{rid}/files/nope/text", headers=headers, follow_redirects=False).status_code == 404

    # Re-submitting the report (retry) keeps the extraction state; completing again queues nothing new.
    client.post(f"/api/deals/{deal}/recordings", headers=headers, json={**bundle, "files": files})
    assert client.post(f"/api/recordings/{rid}/media/complete", headers=headers).json()["queued"] == 0

    # Another company member may read files but not complete the upload.
    other, _, _ = tenant_factory()
    assert client.post(f"/api/recordings/{rid}/media/complete", headers=other).status_code in (403, 404)
    assert client.get(f"/api/recordings/{rid}/files", headers=other).status_code in (200, 403, 404)
