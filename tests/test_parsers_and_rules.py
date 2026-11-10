import io
from pathlib import Path

import ezdxf
import pytest
from openpyxl import Workbook
from pypdf import PdfWriter

from tagtrail.engine import reconcile
from tagtrail.parsers import ParseError, normalize, parse_document, tag_key

SAMPLES = Path(__file__).resolve().parents[1] / "tagtrail" / "samples"


def doc(name, content, role="reference", complete=False):
    return {
        "id": name,
        "filename": name,
        "sha256": "test-hash",
        "role": role,
        "complete_list": complete,
        "parsed": parse_document(name, content, role),
    }


def test_normalization_preserves_nominal_size_distinctions():
    assert tag_key("p101b") == "P-101B"
    assert normalize("nominal_size", '6"') == normalize("nominal_size", "6 inches")
    assert normalize("nominal_size", "DN150") != normalize("nominal_size", "6 in")
    assert normalize("material", "SS") != normalize("material", "CS")


@pytest.mark.parametrize(
    "content",
    [
        b"tag,tag\nP-101,P-101\n",
        b"name\nPump\n",
        b"tag\nINVALID\n",
        b"tag,material\n,SS\n",
        b"tag\nP-101\nP101\n",
    ],
)
def test_ambiguous_registers_rejected(content):
    with pytest.raises(ParseError):
        parse_document("register.csv", content, "register")


@pytest.mark.parametrize("value", ['=HYPERLINK("https://example.com")', "+cmd", "@SUM(A1)", "-cmd"])
def test_formula_cells_rejected(value):
    content = f'tag,material\nP-101,"{value.replace(chr(34), chr(34) * 2)}"\n'.encode()
    with pytest.raises(ParseError, match="Formula"):
        parse_document("reference.csv", content, "reference")


def test_partial_documents_do_not_trigger_false_missing_tags():
    register = doc("register.csv", b"tag,material\nP-101,CS\nP-102,SS\n", "register")
    reference = doc("ref.csv", b"tag,material\nP-101,CS\n")
    assert reconcile([register, reference]) == []
    reference["complete_list"] = True
    findings = reconcile([register, reference])
    assert [(f["tag"], f["kind"], f["proposal"]) for f in findings] == [("P-102", "missing_tag", None)]


def test_duplicate_target_rows_never_receive_auto_corrections():
    register = doc("register.csv", b"tag,material\nP-101,CS\n", "register")
    reference = doc("ref.csv", b"tag,material\nP-101,SS\nP101,OTHER\n")
    findings = reconcile([register, reference])
    assert len(findings) == 1
    assert findings[0]["kind"] == "duplicate_tag" and findings[0]["proposal"] is None


def test_absent_columns_are_not_invented_and_blank_baseline_is_not_authority():
    register = doc("register.csv", b"tag,material,service\nP-101,,Water\n", "register")
    reference = doc("ref.csv", b"tag,material\nP-101,CS\n")
    assert reconcile([register, reference]) == []


def test_csv_evidence_retains_physical_row_and_original_cells():
    parsed = parse_document("ref.csv", b"tag,material\n\nP101,ss\n", "reference")
    row = parsed["records"][0]
    assert row["row"] == 3 and row["location"] == "row 3" and row["tag"] == "P-101"
    assert row["values"]["material"] == "ss"


def test_xlsx_location_and_formula_policy():
    workbook = Workbook()
    workbook.active.title = "Valve list"
    workbook.active.append(["tag", "material"])
    workbook.active.append(["HV-201", "SS"])
    output = io.BytesIO()
    workbook.save(output)
    parsed = parse_document("valves.xlsx", output.getvalue(), "reference")
    assert parsed["records"][0]["location"] == "Valve list · row 2"
    workbook.active["B2"] = "=1+1"
    output = io.BytesIO()
    workbook.save(output)
    with pytest.raises(ParseError, match="Formula"):
        parse_document("valves.xlsx", output.getvalue(), "reference")


def test_multiple_xlsx_sheets_require_explicit_export():
    workbook = Workbook()
    workbook.create_sheet("Other")
    output = io.BytesIO()
    workbook.save(output)
    with pytest.raises(ParseError, match="one worksheet"):
        parse_document("ambiguous.xlsx", output.getvalue(), "register")


def test_pdf_tags_have_pages_and_no_specification_guesses():
    parsed = parse_document("drawing.pdf", (SAMPLES / "cooling_loop.pdf").read_bytes(), "reference")
    assert {r["tag"] for r in parsed["records"]} == {"P-101", "HV-201", "PT-301", "L-2101", "P-999"}
    assert all(r["location"] == "page 1" and r["values"] == {} for r in parsed["records"])


def test_unreadable_pdf_is_flagged_not_treated_as_checked():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    content = io.BytesIO()
    writer.write(content)
    parsed = parse_document("scan.pdf", content.getvalue(), "reference")
    assert parsed["records"] == [] and any("not checked" in w for w in parsed["warnings"])


def test_windows_dxf_text_and_mtext_are_extracted():
    data = (SAMPLES / "cooling_loop.dxf").read_bytes()
    assert b"\r\n" in data
    parsed = parse_document("drawing.dxf", data, "reference")
    assert {r["tag"] for r in parsed["records"]} == {"P-101", "HV-201", "PT-301", "L-2101", "P-999"}
    assert all("entity" in r["location"] for r in parsed["records"])


def test_dxf_tags_inside_blocks_and_attributes():
    drawing = ezdxf.new("R2010")
    block = drawing.blocks.new("PUMP")
    block.add_text("P-101", dxfattribs={"height": 2})
    insert = drawing.modelspace().add_blockref("PUMP", (0, 0))
    insert.add_attrib("TAG", "PT-301", (0, 0))
    output = io.StringIO()
    drawing.write(output)
    parsed = parse_document("block.dxf", output.getvalue().encode(), "reference")
    assert {r["tag"] for r in parsed["records"]} == {"P-101", "PT-301"}


@pytest.mark.parametrize(
    "name,data",
    [("bad.pdf", b"no pdf"), ("bad.xlsx", b"no workbook"), ("bad.dxf", b"broken"), ("bad.docx", b"no docx")],
)
def test_malformed_or_unsupported_documents_fail_clearly(name, data):
    with pytest.raises(ParseError):
        parse_document(name, data, "reference")
