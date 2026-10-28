from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pylibseekdb
from dotenv import load_dotenv


def load_config() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    load_dotenv(Path.cwd() / ".env")


_seekdb_instance = None


def _sql(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def connect_db():
    global _seekdb_instance
    if _seekdb_instance is None:
        db_dir = os.getenv("SEEKDB_DIR", str(Path(__file__).resolve().parents[1] / "seekdb-data"))
        _seekdb_instance = pylibseekdb.open(db_dir=db_dir)
    database = os.getenv("SEEKDB_DATABASE", "minimax_context")
    bootstrap = _seekdb_instance.connect(database="test", autocommit=True)
    cursor = bootstrap.cursor()
    try:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    finally:
        cursor.close()
    bootstrap.close()
    return _seekdb_instance.connect(database=database, autocommit=True)


def import_docs(root: Path) -> int:
    db = connect_db()
    try:
        cur = db.cursor()
        try:
            cur.execute("CREATE TABLE IF NOT EXISTS project_docs (path VARCHAR(512) PRIMARY KEY, kind VARCHAR(32), body LONGTEXT)")
            cur.execute("DELETE FROM project_docs WHERE path LIKE '.venv/%' OR path LIKE 'venv/%' OR path LIKE '__pycache__/%' OR path LIKE 'site/%'")
            count = 0
            ignored = {".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "site", "dist"}
            paths = sorted(p for p in root.rglob("*") if p.suffix in {".md", ".py"} and not ignored.intersection(p.parts))
            for path in paths:
                body = path.read_text(encoding="utf-8")
                rel = path.relative_to(root).as_posix()
                kind = "spec" if "spec" in rel.lower() else "test" if "test" in rel.lower() else "code"
                cur.execute(f"REPLACE INTO project_docs(path,kind,body) VALUES({_sql(rel)},{_sql(kind)},{_sql(body)})")
                count += 1
            return count
        finally:
            cur.close()
    finally:
        db.close()


def retrieve(query: str) -> list[dict[str, str]]:
    db = connect_db()
    try:
        cur = db.cursor()
        try:
            terms = [term for term in query.split() if len(term) > 2][:12]
            predicate = " OR ".join(f"body LIKE {_sql('%' + term + '%')}" for term in terms) or f"body LIKE {_sql('%' + query + '%')}"
            cur.execute(f"SELECT path,kind,body FROM project_docs WHERE {predicate} LIMIT 200")
            rows = cur.fetchall()
            if not rows:
                cur.execute("SELECT path,kind,body FROM project_docs ORDER BY kind,path LIMIT 40")
                rows = cur.fetchall()
            rows.sort(key=lambda row: (-sum(term.lower() in (row[0] + " " + row[2]).lower() for term in terms), row[1] != "spec", row[0]))
            return [dict(zip(("path", "kind", "body"), row)) for row in rows[:12]]
        finally:
            cur.close()
    finally:
        db.close()


def pc_request(method: str, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base = os.getenv("POWERCONTEXT_URL", "http://127.0.0.1:8010").rstrip("/")
    with httpx.Client(trust_env=False, timeout=30) as client:
        response = client.request(method, base + route, json=payload)
        response.raise_for_status()
    return response.json() if response.content else {}


def save_state(task: str, done: list[str], todo: list[str], tests: str) -> str:
    scopes = pc_request("GET", "/v1/scopes?limit=100").get("items", [])
    scope = next((s for s in scopes if s.get("title") == "MiniMax SDD Task"), None)
    if not scope:
        scope = pc_request("POST", "/v1/scopes", {
            "title":"MiniMax SDD Task", "summary":"SDD task state",
            "external_references":[{"kind":"minimax-sdd","value":"task"}],
            "idempotency_key":"minimax-sdd-task-state",
        })
    sid = scope.get("scope_id") or scope.get("id")
    state = json.dumps({"task":task,"done":done,"todo":todo,"tests":tests}, ensure_ascii=False)
    pc_request("POST", "/v1/memory/remember", {"scope_id":sid,"kind":"handoff","text":state})
    return sid


def read_state(sid: str) -> list[dict[str, Any]]:
    return pc_request("POST", "/v1/memory/entries/list", {"scope_id":sid}).get("entries", [])


def make_context(task: str) -> dict[str, Any]:
    docs = retrieve(task)
    sid = os.getenv("POWERCONTEXT_SCOPE_ID", "")
    state = read_state(sid) if sid else []
    return {"task":task,"sources":[{"path":d["path"],"kind":d["kind"],"excerpt":d["body"][:7000]} for d in docs],"prior_state":state}


def generate_patch(context: dict[str, Any]) -> str:
    key = os.environ["MINIMAX_API_KEY"]
    base = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
    model = os.getenv("MINIMAX_MODEL", "MiniMax-M2.5")
    prompt = "Use the supplied task context. Return only a unified diff; do not claim tests passed.\n" + json.dumps(context, ensure_ascii=False)
    with httpx.Client(trust_env=False, timeout=180) as client:
        response = client.post(base + "/chat/completions", headers={"Authorization":f"Bearer {key}"},
            json={"model":model,"messages":[{"role":"user","content":prompt}],"temperature":0.1})
        response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    diff_start = content.find("diff --git ")
    if diff_start < 0:
        raise ValueError("MiniMax response did not contain a unified git diff")
    return content[diff_start:].strip()


def apply_patch(diff: str, repo: Path) -> None:
    if not diff.startswith("diff --git "):
        raise ValueError("MiniMax response is not a unified git diff")
    subprocess.run(["git", "apply", "--check", "-"], input=diff, text=True, cwd=repo, check=True)
    subprocess.run(["git", "apply", "-"], input=diff, text=True, cwd=repo, check=True)
