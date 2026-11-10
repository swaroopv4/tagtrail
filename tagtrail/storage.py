import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def identifier():
    return uuid4().hex


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 0, active_run TEXT
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    filename TEXT NOT NULL, role TEXT NOT NULL, revision TEXT NOT NULL,
                    complete_list INTEGER NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,
                    parsed TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    generation INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS findings (
                    run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, data TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', decision_version INTEGER NOT NULL DEFAULT 0,
                    reviewer TEXT, note TEXT, reviewed_at TEXT, PRIMARY KEY(run_id, id)
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id),
                    created_at TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def audit(db, project_id, action, actor, detail):
    db.execute(
        "INSERT INTO audit(project_id,created_at,action,actor,detail) VALUES(?,?,?,?,?)",
        (project_id, now(), action, actor, json.dumps(detail)),
    )


def documents(db, project_id):
    rows = db.execute(
        "SELECT * FROM documents WHERE project_id=? AND active=1 ORDER BY created_at,id", (project_id,)
    ).fetchall()
    return [{**dict(row), "parsed": json.loads(row["parsed"])} for row in rows]


def public_document(document, include_records=False):
    keys = ("id", "filename", "role", "revision", "complete_list", "sha256", "created_at")
    result = {key: document[key] for key in keys}
    result.update(
        kind=document["parsed"]["kind"],
        warnings=document["parsed"]["warnings"],
        record_count=len(document["parsed"]["records"]),
        byte_size=len(document["content"]),
    )
    if include_records:
        result["records"] = document["parsed"]["records"]
    return result


def public_finding(row):
    return {
        **json.loads(row["data"]),
        **{key: row[key] for key in ("status", "decision_version", "reviewer", "note", "reviewed_at")},
    }
