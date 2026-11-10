"""Exercise the real UI and download, with an isolated server and synthetic data only."""

import argparse
import csv
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true", help="Update synthetic demo screenshots and video")
    args = parser.parse_args()
    results = ROOT / "test-results" / "browser"
    results.mkdir(parents=True, exist_ok=True)
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="tagtrail-browser-", dir=results) as temp:
        environment = os.environ.copy()
        environment["TAGTRAIL_DB"] = str(Path(temp) / "browser.sqlite")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "tagtrail.app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(80):
                try:
                    with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("Test server exited before becoming ready.") from None
                    time.sleep(0.15)
            else:
                raise RuntimeError("Test server did not become ready.")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(
                    viewport={"width": 1440, "height": 1000},
                    accept_downloads=True,
                    **(
                        {
                            "record_video_dir": str(results / "video"),
                            "record_video_size": {"width": 1440, "height": 1000},
                        }
                        if args.record
                        else {}
                    ),
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                expect(
                    page.get_by_role("heading", name="One tag. Every document.", exact=False)
                ).to_be_visible()
                page.get_by_role("button", name="Load demo package").click()
                expect(page.locator("#finding-count")).to_have_text("8")
                expect(page.locator("#documents .doc-card")).to_have_count(4)
                if args.record:
                    page.screenshot(path=str(docs / "overview.png"), full_page=True)
                page.get_by_role(
                    "button", name="HV-201: Nominal Size differs from register", exact=True
                ).click()
                expect(page.locator(".proposal")).to_contain_text("4 in")
                expect(page.locator(".proposal")).to_contain_text("6 in")
                page.locator("#reviewer").fill("Demo Reviewer")
                page.locator("#review-note").fill(
                    "Compared the original row with the designated equipment register."
                )
                if args.record:
                    page.wait_for_timeout(1200)
                page.get_by_role("button", name="Approve correction", exact=True).click()
                expect(page.locator(".decision-meta")).to_contain_text("APPROVED by Demo Reviewer")
                with page.expect_download() as download:
                    page.get_by_role("button", name="Export review pack").click()
                saved = results / "browser-review-pack.zip"
                download.value.save_as(saved)
                with zipfile.ZipFile(saved) as archive:
                    changed = next(name for name in archive.namelist() if name.startswith("reviewed_copies/"))
                    rows = list(csv.DictReader(io.StringIO(archive.read(changed).decode())))
                    assert next(r for r in rows if r["tag"] == "HV-201")["nominal_size"] == "6 in"
                    assert next(r for r in rows if r["tag"] == "L-2101")["nominal_size"] == "6 in"
                    assert next(r for r in rows if r["tag"] == "PT-301")["material"] == ""
                if args.record:
                    shutil.copyfile(saved, docs / "example-review-pack.zip")
                    page.screenshot(path=str(docs / "approved-review.png"), full_page=True)
                    page.wait_for_timeout(1000)
                page.reload()
                expect(page.locator("#finding-count")).to_have_text("8")
                page.get_by_role(
                    "button", name="HV-201: Nominal Size differs from register", exact=True
                ).click()
                expect(page.locator(".decision-meta")).to_contain_text("APPROVED")
                page.locator(".doc-card").filter(has_text="equipment_schedule.csv").get_by_role(
                    "button", name="Inspect records"
                ).click()
                expect(page.locator(".record-source")).to_contain_text("equipment_schedule.csv")
                expect(page.locator(".record-table tbody tr").filter(has_text="HV-201")).to_contain_text(
                    "4 in"
                )
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                    "Mobile horizontal overflow"
                )
                if args.record:
                    page.screenshot(path=str(docs / "mobile.png"), full_page=True)
                recorded_video = page.video
                context.close()
                if args.record:
                    shutil.copyfile(recorded_video.path(), docs / "walkthrough.webm")

                # A second context tests user-supplied files and untrusted names, not the demo endpoint.
                context = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                page.get_by_role("button", name="New project", exact=True).click()
                unsafe_name = '<img src=x onerror="alert(1)"> cooling loop'
                page.get_by_label("Project name", exact=True).fill(unsafe_name)
                page.get_by_role("button", name="Create project", exact=True).click()
                expect(page.locator("#notification")).to_contain_text("Project created")
                assert page.locator("img").count() == 0
                page.locator("#upload-role").select_option("register")
                page.locator("#upload-file").set_input_files(
                    str(ROOT / "tagtrail/samples/equipment_register.csv")
                )
                page.get_by_role("button", name="Upload document", exact=True).click()
                expect(page.locator(".doc-card")).to_have_count(1)
                page.locator("#upload-role").select_option("reference")
                page.locator("#upload-file").set_input_files(
                    str(ROOT / "tagtrail/samples/equipment_schedule.xlsx")
                )
                page.locator("#complete-list").check()
                page.get_by_role("button", name="Upload document", exact=True).click()
                expect(page.locator(".doc-card")).to_have_count(2)
                page.get_by_role("button", name="Run checks", exact=True).click()
                expect(page.locator("#finding-count")).to_have_text("6")
                page.get_by_role("button", name="PT-301: Material is missing", exact=True).click()
                page.locator("#reviewer").fill("Upload Reviewer")
                page.get_by_role("button", name="Approve correction", exact=True).click()
                expect(page.locator(".decision-meta")).to_contain_text("APPROVED")
                with page.expect_download() as download:
                    page.get_by_role("button", name="Export review pack").click()
                download.value.save_as(results / "uploaded-workbook.zip")
                page.locator("#complete-list").uncheck()
                page.locator("#upload-file").set_input_files(
                    {"name": "new-scope.csv", "mimeType": "text/csv", "buffer": b"tag,material\nP-101,CS\n"}
                )
                page.get_by_role("button", name="Upload document", exact=True).click()
                expect(page.locator("#run-state")).to_have_text("Sources changed · run checks")
                expect(page.get_by_role("button", name="Export review pack")).to_be_disabled()
                page.locator("#upload-file").set_input_files(
                    {"name": "broken.pdf", "mimeType": "application/pdf", "buffer": b"not a pdf"}
                )
                page.get_by_role("button", name="Upload document", exact=True).click()
                expect(page.locator("#notification")).to_contain_text("Could not parse")
                assert not errors, errors
                context.close()
                browser.close()
                (results / "result.json").write_text(
                    json.dumps(
                        {
                            "passed": True,
                            "browser": "Chromium",
                            "checks": [
                                "demo reconciliation",
                                "evidence diff",
                                "approval",
                                "ZIP contents",
                                "reload persistence",
                                "source inspection",
                                "mobile overflow",
                                "untrusted project name",
                                "CSV and XLSX uploads",
                                "source invalidation",
                                "malformed PDF error",
                                "no uncaught browser errors",
                            ],
                        },
                        indent=2,
                    )
                )
                print("Browser workflow passed: uploads, reviews, downloads, persistence and mobile layout.")
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
