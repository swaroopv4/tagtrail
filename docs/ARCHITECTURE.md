# How TagTrail works

## A deliberately small problem

The user chooses a comparison register, uploads references, inspects discrepancies and decides which explicit table-cell proposals to export. This complements the document-checking part of the workflow described by Verno. It does not attempt to reproduce Verno's CAD editing, domain knowledge or production system.

The core decision was to keep comparison deterministic. An LLM explanation would not establish that two table cells agree or that an action is permitted. A useful future model component would extract candidate tags and fields from scanned drawings, with confidence, page locations and human confirmation before entering this same review pipeline. No unimplemented AI feature is presented in the UI as working.

## Data flow

```text
CSV / XLSX register ──┐
CSV / XLSX schedules ─┼─> parse + evidence locations ─> reconcile ─> immutable check run
PDF / DXF labels ────┘                                      │
                                                           v
                                                versioned human decisions
                                                           │
                                                           v
                            originals + approved copies + findings + audit + manifest
```

FastAPI serves both the API and the static browser client. SQLite stores source bytes, SHA256 values, extracted records, run metadata, review decisions and audit events. A file upload is real backend ingestion, not a client-side demo substitution. The sample package goes through the same parsers and comparison code as user uploads.

## Authority and comparison rules

- A project has exactly one active register. Duplicate register tags are rejected because choosing a row would invent authority.
- Table duplicates produce one manual finding per tag and disable cell proposals for that tag in the file. Multiple labels in a drawing do not count as duplicate equipment rows.
- A source value is compared only when the corresponding column exists and the register has a non-empty value.
- Blank source cells become fill proposals; differing values become replacement proposals.
- Unknown extracted tag tokens are candidates for review. They can be intentional additions or identifiers of another kind.
- An absent tag becomes a finding only if a reference table was explicitly declared complete. Missing rows never receive automatic insertion proposals.
- PDF and DXF extraction supplies tag locations, not inferred materials or sizes. A no-text or no-tags warning prevents an unreadable drawing from silently looking fully checked.

## State transitions

Every document addition, replacement or removal increments the project's generation and clears its active run. Check runs retain the source IDs and hashes they evaluated. Re-running checks creates a fresh run with fresh pending decisions.

A decision must name the current run and the finding's expected decision version. `BEGIN IMMEDIATE` serializes the compare-and-update. A stale source, old run or conflicting reviewer version returns HTTP 409. The API never accepts an arbitrary field or replacement value from the client; it uses the server-generated proposal.

Approving records a decision; it does not mutate a source. The export takes a transactional snapshot, applies only approved proposals to new table copies, verifies the original cell and source hash, and includes the evidence and review history. An acknowledgement is not a correction. Dismissing or acknowledging requires a reason, and decisions can be reopened.

## Data handling and limits

The application performs no network requests to parse or review documents. The browser has no external scripts or fonts. Source records are rendered through text nodes rather than untrusted HTML. Formula-like table cells are rejected before they can reach an exported spreadsheet.

Uploads have file and package limits, workbook expansion limits, row/page/entity limits and PDF content-stream checks. The app checks Host and Origin for local browser writes, applies a restrictive CSP and does not execute supplied code or macros. These measures support a local demo; they are not a claim of production parser sandboxing. PDF libraries can allocate memory during decompression, and untrusted parsing should be moved into constrained worker processes before shared deployment.

The Docker port is bound to loopback. There is no authentication, authorization between users, signed audit chain, qualified engineering signature or document-system integration. SQLite history is application-level audit history; a person with database access can alter it. Old source bytes stay in local history, so a real retention policy would be needed in a deployed service.

## What I would build next with actual users

1. Validate the tag schema and completeness assumptions against their real document packages.
2. Add supported multi-sheet selection and explicit per-discipline coverage, with schema previews.
3. Add OCR or model-based extraction as reviewed candidates, using a labeled extraction benchmark.
4. Expand rule sets with engineers, distinguishing drafting discrepancies from engineering violations.
5. Add authenticated reviewers, immutable external audit storage and isolated parsing workers before hosting real customer documents.

The useful extension point is `parse_document`: a new extractor returns the same tag, value and evidence-location structure. The existing rules, stale-run protection, review and export tests then apply without changing the action boundary.
