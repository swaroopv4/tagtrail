# Validation record

Local validation on November 10, 2026 used Python 3.11 on Windows and a real headless Chromium browser. All data was generated for this project. No Verno, UCI, employer or customer documents were used.

## Automated checks

- **38 pytest cases passed.** Coverage includes duplicate and malformed baselines; case, tag and inch normalization; formula-like input; partial versus complete coverage; missing columns; PDF evidence and unreadable pages; Windows DXF line endings; block text and attributes; malformed formats; review permissions; stale runs; concurrent decision versions; reopening; CSV and XLSX corrections; source immutability; hash manifests; source replacement; project scoping; origin and host checks; and persistence after app recreation.
- **Ruff lint and formatting checks passed.**
- **Real Chromium workflow passed.** It loaded the synthetic package, inspected the before/after cell proposal, approved a correction, downloaded a ZIP and verified actual contents. It reloaded the browser and inspected unchanged original records. It also tested a 390-pixel mobile viewport, HTML-like project names, custom CSV and XLSX uploads, invalidation after another upload, and a malformed PDF error. No uncaught page errors were observed.
- The screenshots, walkthrough and example export in this repository came from that workflow.

The synthetic demo has four active files, six register tags, eight findings and three editable cell proposals. Three separately evidenced unknown-tag findings refer to the same unregistered tag in the CSV, PDF and DXF; they are not three separate pieces of equipment.

## What these checks do not prove

This is a defined synthetic regression set, not accuracy measured on real engineering packages. Geometry, connectivity, standards compliance, scanned-drawing OCR, native DWG, large-file performance, malicious parser isolation and production multi-user operation are outside the implementation.

The [first published GitHub Actions run](https://github.com/swaroopv4/tagtrail/actions/runs/42796173945) passed on Ubuntu with Python 3.11, Python 3.12 and Chromium. Local runtime verification used Python 3.11 on Windows. A wheel was also built and inspected to verify that the browser assets and synthetic fixtures are included in the installable package. The supplied Docker image was not executed as part of this validation.

An upstream Starlette warning about its legacy httpx test-client adapter appeared locally; it did not fail the tests. The application browser test uses the real HTTP server rather than that adapter.

Development used AI assistance. The local checks above were executed against the application and generated files; screenshots and exports were inspected during implementation review.
