"""Rebuild synthetic fixtures; no real plant or customer data is used."""

import csv
from pathlib import Path

import ezdxf
from openpyxl import Workbook
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

DESTINATION = Path(__file__).resolve().parents[1] / "tagtrail" / "samples"
DESTINATION.mkdir(parents=True, exist_ok=True)
headers = ["tag", "description", "nominal_size", "material", "service"]
register = [
    ["P-101", "Main cooling pump", "6 in", "CS", "Cooling water"],
    ["P-101B", "Standby cooling pump", "6 in", "CS", "Cooling water"],
    ["HV-201", "Isolation valve", "6 in", "CS", "Isolation"],
    ["PT-301", "Pressure transmitter", "", "SS", "Pressure monitoring"],
    ["L-2101", "Discharge line", "8 in", "CS", "Transfer"],
    ["FV-401", "Control valve", "3 in", "SS", "Control"],
]
schedule = [
    ["P101", "Main cooling pump", '6"', "cs", "Cooling water"],
    ["P-101B", "Standby cooling pump", "6 in", "CS", "Cooling water"],
    ["HV-201", "Isolation valve", "4 in", "CS", "Isolation"],
    ["PT-301", "Pressure transmitter", "", "", "Pressure monitoring"],
    ["L-2101", "Discharge line", "6 in", "CS", "Transfer"],
    ["P-101B", "Duplicate legacy entry", "6 in", "CS", "Cooling water"],
    ["P-999", "Unregistered pump", "2 in", "SS", "Drain"],
]
for name, rows in [("equipment_register.csv", register), ("equipment_schedule.csv", schedule)]:
    with (DESTINATION / name).open("w", newline="", encoding="utf-8") as output:
        csv.writer(output, lineterminator="\n").writerows([headers] + rows)
workbook = Workbook()
workbook.active.title = "Equipment"
for row in [headers] + schedule:
    workbook.active.append(row)
workbook.save(DESTINATION / "equipment_schedule.xlsx")

pdf = canvas.Canvas(str(DESTINATION / "cooling_loop.pdf"), pagesize=(792, 612), invariant=1)
pdf.setTitle("Synthetic cooling loop - TagTrail fixture")
pdf.setFillColor(HexColor("#f5f8f2"))
pdf.rect(0, 0, 792, 612, fill=1, stroke=0)
pdf.setStrokeColor(HexColor("#d7e3d5"))
for x in range(24, 792, 24):
    pdf.line(x, 90, x, 515)
for y in range(90, 515, 24):
    pdf.line(24, y, 768, y)
pdf.setFillColor(HexColor("#173f35"))
pdf.setFont("Helvetica-Bold", 23)
pdf.drawString(38, 555, "NORTH LOOP / COOLING WATER")
pdf.setFont("Courier", 10)
pdf.drawString(40, 534, "PID-001 | Revision A | SYNTHETIC REFERENCE DRAWING")
pdf.setStrokeColor(HexColor("#305e4a"))
pdf.setLineWidth(2)
pdf.line(60, 305, 700, 305)
for x, tag in [(160, "P-101"), (370, "HV-201"), (620, "P-999")]:
    pdf.circle(x, 305, 28)
    pdf.setFont("Courier-Bold", 13)
    pdf.drawCentredString(x, 252, tag)
    pdf.setFont("Helvetica", 9)
    pdf.drawCentredString(x, 237, "drawing label")
pdf.line(450, 305, 450, 415)
pdf.circle(450, 415, 23)
pdf.setFont("Courier-Bold", 13)
pdf.drawCentredString(450, 457, "PT-301")
pdf.drawString(470, 328, "L-2101")
pdf.setFont("Courier", 10)
pdf.drawString(40, 125, "NOTE: P-999 is intentionally absent from the comparison register.")
pdf.setFont("Helvetica-Bold", 11)
pdf.drawString(40, 60, "SYNTHETIC TEST DATA - NOT FOR DESIGN, PROCUREMENT OR CONSTRUCTION")
pdf.setFont("Helvetica", 9)
pdf.drawString(
    40, 43, "TagTrail checks labels, not the geometry or engineering validity of this illustration."
)
pdf.save()

dxf = ezdxf.new("R2010")
space = dxf.modelspace()
dxf.layers.new("EQUIPMENT_TAGS")
for x, tag in [(0, "P-101"), (30, "HV-201"), (60, "P-999")]:
    space.add_circle((x, 0), 5)
    space.add_text(tag, dxfattribs={"height": 2, "layer": "EQUIPMENT_TAGS"}).set_placement((x - 5, -10))
space.add_line((0, 0), (60, 0))
space.add_mtext("PT-301\\PL-2101", dxfattribs={"insert": (40, 15), "char_height": 2})
space.add_text("SYNTHETIC - NOT FOR CONSTRUCTION", dxfattribs={"height": 1}).set_placement((-10, -20))
dxf.saveas(DESTINATION / "cooling_loop.dxf")
print("Generated five synthetic fixture files.")
