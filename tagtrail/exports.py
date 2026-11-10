import csv
import hashlib
import io
import json
import zipfile
from pathlib import PurePosixPath

from .storage import now


def build_export(project, run_id, documents, findings, events):
    """Return a self-contained snapshot. Source bytes are never overwritten."""
    output = io.BytesIO()
    manifest = {
        "format": "tagtrail-review-pack-v1",
        "project": project["name"],
        "run_id": run_id,
        "generation": project["generation"],
        "exported_at": now(),
        "files": [],
        "note": (
            "Comparison and local review only. Not design verification or authenticated engineering sign-off."
        ),
    }
    patches = {}
    for finding in findings:
        if finding["status"] == "approved" and finding["proposal"]:
            patches.setdefault(finding["proposal"]["document_id"], []).append(finding["proposal"])
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for index, document in enumerate(documents, start=1):
            name = f"{index:02d}_{document['filename']}"
            original_path = f"originals/{name}"
            bundle.writestr(original_path, document["content"])
            entry = {
                "document_id": document["id"],
                "original": original_path,
                "source_sha256": document["sha256"],
                "role": document["role"],
                "revision_label": document["revision"],
                "approved_cell_changes": 0,
            }
            document_patches = patches.get(document["id"], [])
            if document_patches:
                if any(p["source_sha256"] != document["sha256"] for p in document_patches):
                    raise ValueError("A correction refers to a stale source.")
                suffix = PurePosixPath(document["filename"]).suffix.lower()
                if suffix == ".csv":
                    rows = list(csv.reader(io.StringIO(document["content"].decode("utf-8-sig"))))
                    headers = [h.strip().lower() for h in rows[0]]
                    for patch in document_patches:
                        row = rows[patch["row"] - 1]
                        column = headers.index(patch["field"])
                        row.extend([""] * max(0, len(headers) - len(row)))
                        if row[column].strip() != patch["before"]:
                            raise ValueError("Original cell no longer matches the reviewed proposal.")
                        row[column] = patch["after"]
                    text = io.StringIO(newline="")
                    csv.writer(text, lineterminator="\n").writerows(rows)
                    corrected = text.getvalue().encode("utf-8")
                elif suffix == ".xlsx":
                    from openpyxl import load_workbook

                    workbook = load_workbook(io.BytesIO(document["content"]), keep_links=False)
                    for patch in document_patches:
                        sheet = workbook[patch["sheet"]]
                        headers = [str(c.value or "").strip().lower() for c in sheet[1]]
                        cell = sheet.cell(patch["row"], headers.index(patch["field"]) + 1)
                        if str(cell.value or "").strip() != patch["before"]:
                            raise ValueError("Original cell no longer matches the reviewed proposal.")
                        cell.value = patch["after"]
                    buffer = io.BytesIO()
                    workbook.save(buffer)
                    workbook.close()
                    corrected = buffer.getvalue()
                else:
                    raise ValueError("Corrections are restricted to CSV and XLSX cells.")
                corrected_path = f"reviewed_copies/{name}"
                bundle.writestr(corrected_path, corrected)
                entry.update(
                    reviewed_copy=corrected_path,
                    reviewed_sha256=hashlib.sha256(corrected).hexdigest(),
                    approved_cell_changes=len(document_patches),
                )
            manifest["files"].append(entry)
        bundle.writestr("findings.json", json.dumps(findings, indent=2))
        bundle.writestr("audit.json", json.dumps(events, indent=2))
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2))
    return output.getvalue()
