import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import reconcile
from .exports import build_export
from .parsers import ParseError, parse_document, safe_name
from .storage import Store, audit, documents, identifier, now, public_document, public_finding

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD = 8 * 1024 * 1024
MAX_PACKAGE = 32 * 1024 * 1024


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=100)


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    run_id: str
    expected_version: int = Field(ge=0)
    status: Literal["approved", "dismissed", "acknowledged", "pending"]
    reviewer: str = Field(min_length=2, max_length=80)
    note: str = Field(default="", max_length=800)

    @field_validator("reviewer")
    @classmethod
    def real_name(cls, value):
        if not value.strip():
            raise ValueError("Enter a reviewer name.")
        return value


def get_project(db, project_id):
    project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        raise HTTPException(404, "Project not found.")
    return dict(project)


def state(db, project_id):
    project = get_project(db, project_id)
    docs = documents(db, project_id)
    findings = []
    if project["active_run"]:
        findings = [
            public_finding(r)
            for r in db.execute(
                "SELECT * FROM findings WHERE run_id=? ORDER BY rowid", (project["active_run"],)
            )
        ]
    events = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM audit WHERE project_id=? ORDER BY id DESC LIMIT 500", (project_id,)
        )
    ]
    for event in events:
        event["detail"] = json.loads(event["detail"])
    return {
        **project,
        "documents": [public_document(d) for d in docs],
        "findings": findings,
        "audit": events,
        "warnings": [
            {"filename": d["filename"], "message": w} for d in docs for w in d["parsed"]["warnings"]
        ],
    }


def add_document(db, project_id, filename, content, role, revision, complete_list, parsed):
    get_project(db, project_id)
    active = documents(db, project_id)
    digest = hashlib.sha256(content).hexdigest()
    if any(d["sha256"] == digest and d["role"] == role for d in active):
        raise HTTPException(409, "This content is already active in the project.")
    retained = [d for d in active if not (role == "register" and d["role"] == "register")]
    if sum(len(d["content"]) for d in retained) + len(content) > MAX_PACKAGE:
        raise HTTPException(413, "Project exceeds the 32 MB active-file limit.")
    if len(retained) >= 20:
        raise HTTPException(422, "A project supports at most 20 active files.")
    if role == "register":
        db.execute("UPDATE documents SET active=0 WHERE project_id=? AND role='register'", (project_id,))
    doc_id = identifier()
    db.execute(
        """INSERT INTO documents
        (id,project_id,filename,role,revision,complete_list,sha256,content,parsed,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            doc_id,
            project_id,
            filename,
            role,
            revision,
            int(complete_list),
            digest,
            content,
            json.dumps(parsed),
            now(),
        ),
    )
    db.execute("UPDATE projects SET generation=generation+1,active_run=NULL WHERE id=?", (project_id,))
    audit(
        db,
        project_id,
        "document_added",
        "local operator",
        {
            "document_id": doc_id,
            "filename": filename,
            "sha256": digest,
            "role": role,
            "revision": revision,
            "complete_list": complete_list,
            "previous_reviews_invalidated": True,
        },
    )
    return doc_id


def analyze(db, project_id):
    project = get_project(db, project_id)
    docs = documents(db, project_id)
    if len(docs) < 2 or not any(d["role"] == "reference" for d in docs):
        raise HTTPException(422, "Add a comparison register and at least one reference document.")
    try:
        findings = reconcile(docs)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    run_id = identifier()
    db.execute("INSERT INTO runs VALUES(?,?,?,?)", (run_id, project_id, project["generation"], now()))
    for finding in findings:
        db.execute(
            "INSERT INTO findings(run_id,id,data) VALUES(?,?,?)", (run_id, finding["id"], json.dumps(finding))
        )
    db.execute("UPDATE projects SET active_run=? WHERE id=?", (run_id, project_id))
    audit(
        db,
        project_id,
        "checks_completed",
        "checker",
        {
            "run_id": run_id,
            "generation": project["generation"],
            "finding_count": len(findings),
            "documents": [{"id": d["id"], "sha256": d["sha256"]} for d in docs],
        },
    )


def create_app(database_path=None):
    store = Store(database_path or os.environ.get("TAGTRAIL_DB", "data/tagtrail.sqlite"))
    app = FastAPI(title="TagTrail", version="0.1.0", description="Local document consistency review API")
    app.state.store = store
    allowed_hosts = os.getenv("TAGTRAIL_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return Response("Cross-origin changes are not allowed.", status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > MAX_UPLOAD + 65536:
                    return Response("Upload exceeds 8 MB.", status_code=413)
            except ValueError:
                return Response("Invalid request length.", status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health():
        with store.connection() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/projects")
    def list_projects():
        with store.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY created_at DESC")]

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectInput):
        with store.connection(write=True) as db:
            project_id = identifier()
            db.execute(
                "INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", (project_id, body.name, now())
            )
            audit(db, project_id, "project_created", "local operator", {"name": body.name})
            return state(db, project_id)

    @app.get("/api/projects/{project_id}")
    def get_state(project_id: str):
        with store.connection() as db:
            return state(db, project_id)

    @app.post("/api/projects/{project_id}/documents", status_code=201)
    def upload(
        project_id: str,
        file: Annotated[UploadFile, File()],
        role: Annotated[Literal["register", "reference"], Form()],
        revision: Annotated[str, Form(max_length=50)] = "Unspecified",
        complete_list: Annotated[bool, Form()] = False,
    ):
        content = file.file.read(MAX_UPLOAD + 1)
        if not content or len(content) > MAX_UPLOAD:
            raise HTTPException(413, "Supply a non-empty file no larger than 8 MB.")
        filename = safe_name(file.filename or "document")
        try:
            parsed = parse_document(filename, content, role)
        except ParseError as error:
            raise HTTPException(422, str(error)) from error
        if complete_list and parsed["kind"] != "table":
            raise HTTPException(422, "Complete-list coverage is available only for CSV and XLSX tables.")
        with store.connection(write=True) as db:
            add_document(
                db,
                project_id,
                filename,
                content,
                role,
                revision.strip() or "Unspecified",
                complete_list,
                parsed,
            )
            return state(db, project_id)

    @app.get("/api/projects/{project_id}/documents/{document_id}")
    def source(project_id: str, document_id: str):
        with store.connection() as db:
            get_project(db, project_id)
            document = next((d for d in documents(db, project_id) if d["id"] == document_id), None)
            if not document:
                raise HTTPException(404, "Active document not found.")
            return public_document(document, include_records=True)

    @app.get("/api/projects/{project_id}/documents/{document_id}/download")
    def download_source(project_id: str, document_id: str):
        with store.connection() as db:
            get_project(db, project_id)
            document = next((d for d in documents(db, project_id) if d["id"] == document_id), None)
            if not document:
                raise HTTPException(404, "Active document not found.")
            return Response(
                document["content"],
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{document["filename"]}"'},
            )

    @app.delete("/api/projects/{project_id}/documents/{document_id}")
    def remove_document(project_id: str, document_id: str):
        with store.connection(write=True) as db:
            get_project(db, project_id)
            result = db.execute(
                "UPDATE documents SET active=0 WHERE id=? AND project_id=? AND active=1",
                (document_id, project_id),
            )
            if not result.rowcount:
                raise HTTPException(404, "Active document not found.")
            db.execute(
                "UPDATE projects SET generation=generation+1,active_run=NULL WHERE id=?", (project_id,)
            )
            audit(db, project_id, "document_removed", "local operator", {"document_id": document_id})
            return state(db, project_id)

    @app.post("/api/projects/{project_id}/analyze")
    def run_checks(project_id: str):
        with store.connection(write=True) as db:
            analyze(db, project_id)
            return state(db, project_id)

    @app.post("/api/projects/{project_id}/findings/{finding_id}/decision")
    def decide(project_id: str, finding_id: str, body: DecisionInput):
        with store.connection(write=True) as db:
            project = get_project(db, project_id)
            if not project["active_run"] or body.run_id != project["active_run"]:
                raise HTTPException(409, "Sources or the check run changed. Reload and run checks again.")
            finding = db.execute(
                "SELECT * FROM findings WHERE run_id=? AND id=?", (body.run_id, finding_id)
            ).fetchone()
            if not finding:
                raise HTTPException(404, "Finding not found in this run.")
            if body.expected_version != finding["decision_version"]:
                raise HTTPException(409, "Another review changed this finding. Reload before deciding.")
            data = json.loads(finding["data"])
            if body.status == "approved" and not data["proposal"]:
                raise HTTPException(
                    422, "This finding has no safe cell correction. Acknowledge it for manual review."
                )
            if body.status == "acknowledged" and data["proposal"]:
                raise HTTPException(422, "Approve or dismiss a proposed cell correction.")
            if body.status in {"dismissed", "acknowledged"} and len(body.note) < 8:
                raise HTTPException(422, "Add a note of at least eight characters explaining the decision.")
            db.execute(
                """UPDATE findings SET status=?,decision_version=decision_version+1,
                reviewer=?,note=?,reviewed_at=? WHERE run_id=? AND id=?""",
                (body.status, body.reviewer, body.note, now(), body.run_id, finding_id),
            )
            audit(
                db,
                project_id,
                "review_decision",
                body.reviewer,
                {
                    "run_id": body.run_id,
                    "finding_id": finding_id,
                    "tag": data["tag"],
                    "previous_status": finding["status"],
                    "status": body.status,
                    "note": body.note,
                    "decision_version": body.expected_version + 1,
                    "proposal": data["proposal"],
                },
            )
            return state(db, project_id)

    @app.post("/api/projects/{project_id}/export")
    def export(project_id: str, run_id: str):
        with store.connection(write=True) as db:
            project = get_project(db, project_id)
            if not project["active_run"] or run_id != project["active_run"]:
                raise HTTPException(409, "The review pack is stale. Reload and run checks again.")
            findings = [
                public_finding(r) for r in db.execute("SELECT * FROM findings WHERE run_id=?", (run_id,))
            ]
            audit(
                db,
                project_id,
                "review_pack_exported",
                "local operator",
                {
                    "run_id": run_id,
                    "approved_corrections": sum(f["status"] == "approved" for f in findings),
                },
            )
            events = [
                dict(r)
                for r in db.execute("SELECT * FROM audit WHERE project_id=? ORDER BY id", (project_id,))
            ]
            for event in events:
                event["detail"] = json.loads(event["detail"])
            try:
                content = build_export(project, run_id, documents(db, project_id), findings, events)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            return Response(
                content,
                media_type="application/zip",
                headers={
                    "Content-Disposition": 'attachment; filename="tagtrail-review-pack.zip"',
                },
            )

    @app.post("/api/demo", status_code=201)
    def demo():
        samples = [
            ("equipment_register.csv", "register", False),
            ("equipment_schedule.csv", "reference", True),
            ("cooling_loop.pdf", "reference", False),
            ("cooling_loop.dxf", "reference", False),
        ]
        with store.connection(write=True) as db:
            project_id = identifier()
            db.execute(
                "INSERT INTO projects(id,name,created_at) VALUES(?,?,?)",
                (project_id, "North Loop · synthetic demo", now()),
            )
            audit(db, project_id, "project_created", "demo loader", {"synthetic": True})
            for filename, role, complete in samples:
                content = (ROOT / "samples" / filename).read_bytes()
                add_document(
                    db,
                    project_id,
                    filename,
                    content,
                    role,
                    "Demo A",
                    complete,
                    parse_document(filename, content, role),
                )
            analyze(db, project_id)
            return state(db, project_id)

    @app.get("/api/template")
    def template():
        return Response(
            "tag,description,nominal_size,material,service\nP-101,Main pump,6 in,CS,Cooling water\n",
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="register-template.csv"'},
        )

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app


app = create_app()
