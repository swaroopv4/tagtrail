"""Extract explicit values and locations. Never infer specifications from drawing geometry."""

import csv
import io
import re
import zipfile
from pathlib import PurePosixPath

FIELDS = ("nominal_size", "material", "service")
MAX_ROWS = 5000
MAX_TEXT = 1_000_000
TAG_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,8}[-–‑]\d{1,8}[A-Z]{0,3})(?![A-Za-z0-9])", re.I)
TAG_FULL = re.compile(r"[A-Z]{1,8}-?\d{1,8}[A-Z]{0,3}")
IGNORED_PREFIXES = {"PID", "DWG", "DOC", "REV", "SHEET"}


class ParseError(ValueError):
    pass


def safe_name(name: str) -> str:
    name = PurePosixPath(name.replace("\\", "/")).name
    return re.sub(r"[^A-Za-z0-9._ ()-]", "_", name)[:120] or "document"


def tag_key(value: str) -> str:
    value = re.sub(r"\s+", "", value).upper().replace("–", "-").replace("‑", "-")
    if not TAG_FULL.fullmatch(value):
        raise ParseError(f"Invalid tag {value!r}. Use tags such as P-101, HV-201 or L-2101.")
    return re.sub(r"^([A-Z]+)-?(\d)", r"\1-\2", value)


def normalize(field: str, value: str) -> str:
    value = " ".join(value.strip().split()).casefold()
    if field == "nominal_size":
        value = value.replace("″", '"').replace("”", '"')
        value = re.sub(r"\s*(?:inches|inch|in\.?|\")\s*$", " in", value)
    return value


def _cell(value) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) > 4000:
        raise ParseError("A cell exceeds the 4,000-character limit.")
    if text.startswith(("=", "+", "@")) or (text.startswith("-") and not re.fullmatch(r"-\d+(\.\d+)?", text)):
        raise ParseError("Formula-like cells are not accepted. Export or paste plain values first.")
    return text


def _table(rows: list, sheet: str | None = None) -> dict:
    if not rows:
        raise ParseError("The table is empty.")
    headers = [_cell(v).lower() for v in rows[0]]
    if len(headers) != len(set(headers)) or "tag" not in headers:
        raise ParseError("Use one unique header row with a required 'tag' column.")
    if len(headers) > 50 or len(rows) > MAX_ROWS + 1:
        raise ParseError("Tables support at most 50 columns and 5,000 data rows.")
    records = []
    for row_number, row in enumerate(rows[1:], start=2):
        cells = [_cell(v) for v in row]
        if not any(cells):
            continue
        if len(cells) > len(headers) and any(cells[len(headers) :]):
            raise ParseError(f"Row {row_number} has more values than the header.")
        cells = (cells + [""] * len(headers))[: len(headers)]
        values = dict(zip(headers, cells, strict=True))
        if not values["tag"]:
            raise ParseError(f"Row {row_number} has values but no tag.")
        tag = tag_key(values["tag"])
        records.append(
            {
                "tag": tag,
                "values": {f: values.get(f, "") for f in FIELDS},
                "row": row_number,
                "sheet": sheet,
                "location": f"{sheet} · row {row_number}" if sheet else f"row {row_number}",
                "excerpt": " | ".join(f"{k}: {v}" for k, v in values.items() if v),
            }
        )
    if not records:
        raise ParseError("No tagged rows found.")
    return {"kind": "table", "headers": headers, "sheet": sheet, "records": records, "warnings": []}


def _tags(text: str, location: str) -> list:
    found = []
    for match in TAG_PATTERN.finditer(text):
        tag = tag_key(match.group(1))
        if tag.split("-")[0] in IGNORED_PREFIXES:
            continue
        excerpt = " ".join(text[max(0, match.start() - 55) : match.end() + 85].split())
        found.append({"tag": tag, "values": {}, "location": location, "excerpt": excerpt})
    return found


def _xlsx(data: bytes) -> dict:
    from openpyxl import load_workbook

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(z.file_size for z in archive.infolist()) > 40_000_000:
            raise ParseError("Expanded workbook exceeds 40 MB.")
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
    try:
        if len(workbook.worksheets) != 1:
            raise ParseError("Use a workbook with one worksheet so the reviewed table is unambiguous.")
        sheet = workbook.active
        rows = []
        for row in sheet.iter_rows(values_only=True):
            rows.append(list(row))
            if len(rows) > MAX_ROWS + 1:
                raise ParseError("Workbook exceeds 5,000 data rows.")
        return _table(rows, sheet.title)
    finally:
        workbook.close()


def _pdf(data: bytes) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ParseError("Encrypted PDFs are not supported. Supply an unlocked, text-based export.")
    if len(reader.pages) > 60:
        raise ParseError("PDFs support at most 60 pages.")
    records, warnings, total = [], [], 0
    for number, page in enumerate(reader.pages, start=1):
        contents = page.get_contents()
        if contents and len(contents.get_data()) > 5_000_000:
            raise ParseError("A PDF page content stream exceeds 5 MB.")
        text = page.extract_text() or ""
        total += len(text)
        if total > MAX_TEXT:
            raise ParseError("Extracted PDF text exceeds the limit.")
        if not text.strip():
            warnings.append(
                f"Page {number} has no selectable text. OCR is needed; this page was not checked."
            )
        records.extend(_tags(text, f"page {number}"))
    if not records:
        warnings.append("No supported tag tokens found. This is not evidence of a consistent drawing.")
    return {"kind": "drawing", "records": records, "warnings": warnings}


def _dxf(data: bytes) -> dict:
    import ezdxf
    from ezdxf.filemanagement import dxf_stream_info

    info = dxf_stream_info(io.StringIO(data.decode("latin-1"), newline=None))
    text = data.decode(info.encoding, errors="replace")
    drawing = ezdxf.read(io.StringIO(text, newline=None))
    records, visited = [], 0

    def walk(entities, parent="", depth=0):
        nonlocal visited
        if depth > 6:
            raise ParseError("DXF blocks exceed the supported nesting depth.")
        for entity in entities:
            visited += 1
            if visited > 50_000:
                raise ParseError("DXF exceeds 50,000 inspected entities.")
            handle = entity.dxf.get("handle", "virtual")
            location = (
                f"entity {parent + '/' if parent else ''}{handle} · layer {entity.dxf.get('layer', '0')}"
            )
            kind = entity.dxftype()
            if kind in {"TEXT", "ATTRIB", "ATTDEF"}:
                records.extend(_tags(entity.dxf.text, location))
            elif kind == "MTEXT":
                records.extend(_tags(entity.plain_text(), location))
            elif kind == "INSERT":
                walk(entity.attribs, str(handle), depth + 1)
                walk(entity.virtual_entities(), str(handle), depth + 1)

    walk(drawing.modelspace())
    warnings = [
        "DXF checks TEXT, MTEXT and block attributes in model space; "
        "geometry and connectivity are not interpreted."
    ]
    if not records:
        warnings.append("No supported tag tokens found. Inspect unrecognized labels manually.")
    return {"kind": "drawing", "records": records, "warnings": warnings}


def parse_document(name: str, data: bytes, role: str) -> dict:
    suffix = PurePosixPath(name).suffix.lower()
    if role == "register" and suffix not in {".csv", ".xlsx"}:
        raise ParseError("The comparison register must be CSV or XLSX.")
    try:
        if suffix == ".csv":
            text = data.decode("utf-8-sig")
            result = _table(list(csv.reader(io.StringIO(text))))
        elif suffix == ".xlsx":
            result = _xlsx(data)
        elif suffix == ".pdf":
            result = _pdf(data)
        elif suffix == ".dxf":
            result = _dxf(data)
        else:
            raise ParseError("Supported formats: CSV, XLSX, selectable-text PDF and text-bearing DXF.")
        if role == "register":
            tags = [r["tag"] for r in result["records"]]
            if len(tags) != len(set(tags)):
                raise ParseError(
                    "The comparison register contains duplicate tags. "
                    "Resolve them before using it as a baseline."
                )
        return result
    except ParseError:
        raise
    except Exception as error:
        raise ParseError(
            f"Could not parse {suffix or 'file'}: check the format and supply an uncorrupted export."
        ) from error
