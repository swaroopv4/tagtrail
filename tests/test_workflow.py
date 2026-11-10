import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

from openpyxl import load_workbook

SAMPLES = Path(__file__).resolve().parents[1] / "tagtrail" / "samples"


def review(client, project, finding, status="approved", **changes):
    body = {
        "run_id": project["active_run"],
        "expected_version": finding["decision_version"],
        "status": status,
        "reviewer": "Demo Reviewer",
        "note": "Checked against the chosen baseline.",
    }
    body.update(changes)
    return client.post(f"/api/projects/{project['id']}/findings/{finding['id']}/decision", json=body)


def export(client, project):
    response = client.post(f"/api/projects/{project['id']}/export", params={"run_id": project["active_run"]})
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_demo_has_known_findings_and_three_reviewable_corrections(demo):
    assert len(demo["documents"]) == 4
    assert len(demo["findings"]) == 8
    proposed = [f for f in demo["findings"] if f["proposal"]]
    assert {(f["tag"], f["field"]) for f in proposed} == {
        ("HV-201", "nominal_size"),
        ("L-2101", "nominal_size"),
        ("PT-301", "material"),
    }
    assert all(f["source"]["sha256"] and f["source"]["location"] for f in demo["findings"])


def test_unapproved_exports_do_not_change_any_file(client, demo):
    archive = export(client, demo)
    assert not any(n.startswith("reviewed_copies/") for n in archive.namelist())
    for item in json.loads(archive.read("manifest.json"))["files"]:
        assert hashlib.sha256(archive.read(item["original"])).hexdigest() == item["source_sha256"]


def test_review_and_export_changes_only_approved_cells(client, demo):
    for finding in demo["findings"]:
        if finding["proposal"]:
            response = review(client, demo, finding)
            assert response.status_code == 200
    archive = export(client, demo)
    manifest = json.loads(archive.read("manifest.json"))
    changed = [f for f in manifest["files"] if f["approved_cell_changes"]]
    assert len(changed) == 1 and changed[0]["approved_cell_changes"] == 3
    content = archive.read(changed[0]["reviewed_copy"])
    assert hashlib.sha256(content).hexdigest() == changed[0]["reviewed_sha256"]
    rows = list(csv.DictReader(io.StringIO(content.decode())))
    assert next(r for r in rows if r["tag"] == "HV-201")["nominal_size"] == "6 in"
    assert next(r for r in rows if r["tag"] == "L-2101")["nominal_size"] == "8 in"
    assert next(r for r in rows if r["tag"] == "PT-301")["material"] == "SS"
    assert len(rows) == 7 and sum(r["tag"] == "P-101B" for r in rows) == 2
    assert any(r["tag"] == "P-999" for r in rows) and not any(r["tag"] == "FV-401" for r in rows)
    assert archive.read(changed[0]["original"]) == (SAMPLES / "equipment_schedule.csv").read_bytes()
    audit = json.loads(archive.read("audit.json"))
    assert len([a for a in audit if a["action"] == "review_decision"]) == 3
    assert audit[-1]["action"] == "review_pack_exported"


def test_manual_findings_cannot_be_approved_as_cell_edits(client, demo):
    finding = next(f for f in demo["findings"] if f["kind"] == "duplicate_tag")
    assert review(client, demo, finding).status_code == 422
    assert review(client, demo, finding, "acknowledged", note="").status_code == 422
    response = review(client, demo, finding, "acknowledged")
    assert response.status_code == 200
    updated = next(f for f in response.json()["findings"] if f["id"] == finding["id"])
    assert updated["status"] == "acknowledged" and updated["proposal"] is None


def test_concurrent_review_uses_compare_and_swap(client, demo):
    finding = next(f for f in demo["findings"] if f["proposal"])
    assert review(client, demo, finding).status_code == 200
    assert review(client, demo, finding, "dismissed").status_code == 409


def test_approval_can_be_reopened_without_editing_original(client, demo):
    finding = next(f for f in demo["findings"] if f["proposal"])
    approved = review(client, demo, finding).json()
    updated = next(f for f in approved["findings"] if f["id"] == finding["id"])
    reopened = review(client, approved, updated, "pending")
    assert reopened.status_code == 200
    assert not any(n.startswith("reviewed_copies/") for n in export(client, reopened.json()).namelist())


def test_source_changes_invalidate_approvals_and_export(client, demo):
    finding = next(f for f in demo["findings"] if f["proposal"])
    assert review(client, demo, finding).status_code == 200
    response = client.post(
        f"/api/projects/{demo['id']}/documents",
        data={"role": "reference"},
        files={"file": ("new.csv", b"tag,material\nP-101,CS\n", "text/csv")},
    )
    assert response.status_code == 201 and response.json()["active_run"] is None
    assert review(client, demo, finding).status_code == 409
    assert (
        client.post(f"/api/projects/{demo['id']}/export", params={"run_id": demo["active_run"]}).status_code
        == 409
    )


def test_new_check_run_also_rejects_old_decision(client, demo):
    finding = next(f for f in demo["findings"] if f["proposal"])
    response = client.post(f"/api/projects/{demo['id']}/analyze")
    assert response.status_code == 200 and response.json()["active_run"] != demo["active_run"]
    assert review(client, demo, finding).status_code == 409


def test_duplicate_upload_does_not_discard_current_review(client, demo):
    response = client.post(
        f"/api/projects/{demo['id']}/documents",
        data={"role": "reference"},
        files={"file": ("same.csv", (SAMPLES / "equipment_schedule.csv").read_bytes(), "text/csv")},
    )
    assert response.status_code == 409
    assert client.get(f"/api/projects/{demo['id']}").json()["active_run"] == demo["active_run"]


def test_xlsx_approved_copy_preserves_other_values(client):
    project = client.post("/api/projects", json={"name": "Workbook review"}).json()
    for filename, role in [("equipment_register.csv", "register"), ("equipment_schedule.xlsx", "reference")]:
        response = client.post(
            f"/api/projects/{project['id']}/documents",
            data={"role": role},
            files={"file": (filename, (SAMPLES / filename).read_bytes())},
        )
        assert response.status_code == 201
    project = client.post(f"/api/projects/{project['id']}/analyze").json()
    finding = next(f for f in project["findings"] if f["tag"] == "HV-201")
    approved = review(client, project, finding).json()
    archive = export(client, approved)
    name = next(n for n in archive.namelist() if n.startswith("reviewed_copies/"))
    workbook = load_workbook(io.BytesIO(archive.read(name)))
    assert workbook["Equipment"]["C4"].value == "6 in"
    assert workbook["Equipment"]["C6"].value == "6 in"  # Unapproved line discrepancy stays unchanged.
    assert workbook["Equipment"].max_row == 8
    workbook.close()


def test_project_boundaries_and_unknown_resources(client, demo):
    other = client.post("/api/projects", json={"name": "Other"}).json()
    source = demo["documents"][0]["id"]
    assert client.get(f"/api/projects/{other['id']}/documents/{source}").status_code == 404
    assert client.get("/api/projects/does-not-exist").status_code == 404
    assert client.post(f"/api/projects/{other['id']}/analyze").status_code == 422


def test_replacing_baseline_keeps_one_active_register(client, demo):
    content = (SAMPLES / "equipment_register.csv").read_bytes().replace(b"Cooling water", b"CHW")
    response = client.post(
        f"/api/projects/{demo['id']}/documents",
        data={"role": "register"},
        files={"file": ("new-register.csv", content)},
    )
    assert response.status_code == 201
    updated = response.json()
    assert sum(d["role"] == "register" for d in updated["documents"]) == 1
    assert updated["active_run"] is None


def test_upload_validation_and_origin_boundary(client, demo):
    endpoint = f"/api/projects/{demo['id']}/documents"
    assert (
        client.post(endpoint, data={"role": "register"}, files={"file": ("drawing.pdf", b"bad")}).status_code
        == 422
    )
    assert (
        client.post(endpoint, data={"role": "reference"}, files={"file": ("empty.csv", b"")}).status_code
        == 413
    )
    assert (
        client.post(
            "/api/projects", json={"name": "Blocked"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400


def test_persistence_survives_new_application_instance(client, demo):
    from fastapi.testclient import TestClient

    from tagtrail.app import create_app

    path = client.app.state.store.path
    with TestClient(create_app(path), base_url="http://localhost") as restarted:
        response = restarted.get(f"/api/projects/{demo['id']}")
        assert response.status_code == 200 and len(response.json()["findings"]) == 8
