# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Installed 1.3.0 -> built wheel acceptance; every required phase must succeed.

No checkout runtime imports, migration fallbacks, skips or expected failures.
Fixtures establish dependencies rather than relying on test execution order.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "isolated-upgrade-acceptance-token"  # noqa: S105 -- disposable fixture, never a deployment credential


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def console(python):
    return str(python.with_name("powercontext.exe" if os.name == "nt" else "powercontext"))


def command(arguments, environment, work, log):
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            arguments, cwd=work, env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=1800, check=False
        )
    assert result.returncode == 0, f"Command failed ({result.returncode}); see {log}"
    return log.read_text(encoding="utf-8")


def post(client, path, body):
    response = client.post(path, json=body)
    assert response.is_success, f"{path}: HTTP {response.status_code}: {response.text}"
    return response.json()


@contextmanager
def server(python, environment, work, log):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [console(python), "server", "run", "--no-env-file", "--host", "127.0.0.1", "--port", str(port)],
            cwd=work,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            text=True,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": f"Bearer {TOKEN}"},
                trust_env=False,
                timeout=30,
            ) as client:
                deadline = time.monotonic() + 180
                while True:
                    assert process.poll() is None, f"Server exited before readiness; see {log}"
                    try:
                        response = client.get("/health/ready")
                        if response.status_code == 200 and response.json().get("status") in {"ready", "degraded"}:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, f"Server readiness timed out; see {log}"
                    time.sleep(0.2)
                yield client
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)


def environment_for(case, *, fresh=False):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "PYTEST_", "OPENAI_", "ANTHROPIC_", "OTEL_"))
        and key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment.update({
        "POWERCONTEXT_HOME": str(case["work"] / ("fresh-home" if fresh else "home")),
        "POWERCONTEXT_SERVER_AUTH_TOKEN": TOKEN,
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET": "isolated-upgrade-cursor-signing-key-0001",
        "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE": "all",
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    })
    database = case["work"] / ("fresh" if fresh else "database")
    backend = case["backend"]
    environment["POWERCONTEXT_SERVER_DATABASE_KIND"] = backend
    if backend == "sqlite":
        environment["POWERCONTEXT_SERVER_DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    elif backend == "seekdb":
        environment["POWERCONTEXT_SERVER_DATABASE_PATH"] = str(database)
    else:
        name = "PC_UPGRADE_OCEANBASE_FRESH_URL" if fresh else "PC_UPGRADE_OCEANBASE_URL"
        url = os.environ[name]
        parsed = urlsplit(url)
        expected = "/pc_upgrade_fresh" if fresh else "/pc_upgrade_test"
        assert parsed.scheme == "mysql+aoceanbase" and parsed.hostname in {"127.0.0.1", "localhost"}
        assert parsed.path == expected, "Only dedicated disposable OceanBase databases may be used"
        environment["POWERCONTEXT_SERVER_DATABASE_URL"] = url
        environment["PC_UPGRADE_OCEANBASE_URL"] = url
    return environment


def snapshot(case, role, name, *, fresh=False):
    path = case["report"] / f"{name}-schema.json"
    command(
        [
            str(case[role]),
            "-I",
            str(ROOT / "scripts/upgrade_database_probe.py"),
            "--backend",
            case["backend"],
            "--database",
            str(case["work"] / ("fresh" if fresh else "database")),
            "--output",
            str(path),
        ],
        environment_for(case, fresh=fresh),
        case["work"],
        case["report"] / f"{name}-probe.log",
    )
    return json.loads(path.read_text())


def task_state(case, role, operation, scope):
    path = case["report"] / f"task-{operation}.json"
    command(
        [
            str(case[role]),
            "-I",
            str(ROOT / "tests/release_upgrades/processing_fixture.py"),
            operation,
            scope,
            str(path),
        ],
        environment_for(case),
        case["work"],
        case["report"] / f"task-{operation}.log",
    )
    return json.loads(path.read_text())


def verify_authorization(case, role, historical):
    fixture = case["report"] / "authorization-fixture.json"
    fixture.write_text(json.dumps(historical), encoding="utf-8")
    command(
        [str(case[role]), "-I", str(ROOT / "tests/release_upgrades/authorization_fixture.py"), str(fixture)],
        environment_for(case),
        case["work"],
        case["report"] / f"{role}-authorization.log",
    )


def seed(client, *, candidate_prefix):
    scopes = [
        post(
            client,
            "/v1/scopes",
            {
                "title": f"Upgrade fixture {i}",
                "summary": "Installed release acceptance",
                "idempotency_key": f"upgrade-scope-{i}",
            },
        )
        for i in range(3)
    ]
    scope = scopes[0]["scope_id"]
    memory = [
        post(client, "/v1/memory/remember", {"scope_id": scope, "kind": "fact", "text": text})["records"][0]
        for text in ("Upgrade fixture preserves historical storage.", "Upgrade fixture retains exact references.")
    ]
    captured = post(
        client,
        "/v1/sources/content",
        {
            "scope_id": scope,
            "source_id": "upgrade-evidence",
            "content": "Historical source evidence for upgrade candidate review.",
        },
    )["source"]
    proposal = {
        "situation": "A release upgrades storage",
        "action": "Run migration acceptance",
        "outcome": "History survives",
        "lesson": "Validate real old databases",
    }
    body = {"scope_id": scope, "proposal": proposal, "source_refs": [captured], "artifact_refs": []}
    candidate = post(client, "/v1/experience/propose", body)
    first = candidate
    candidate = post(
        client,
        f"{candidate_prefix}/revise",
        {
            **body,
            "candidate_id": candidate["candidate_id"],
            "expected_version": candidate["version"],
            "proposal": {**proposal, "lesson": "Validate every required backend"},
        },
    )
    published = post(client, "/v1/experience/propose", body)
    published = post(
        client,
        f"{candidate_prefix}/approve",
        {"scope_id": scope, "candidate_id": published["candidate_id"], "expected_version": published["version"]},
    )
    artifact = post(client, "/v1/experience/get", {"scope_id": scope, "artifact": published["result_artifact"]})
    grant = {
        "subject": {"type": "user", "id": "upgrade-viewer"},
        "resource": {"type": "scope", "scope_id": scope},
        "role": "scope.viewer",
        "idempotency_key": "upgrade-viewer",
    }
    binding = post(client, "/v1/access/bindings/create", grant)
    first_page = client.get("/v1/scopes", params={"limit": 1}).json()
    cursor = first_page["next_cursor"]
    assert cursor
    continuation = client.get("/v1/scopes", params={"limit": 1, "cursor": cursor}).json()
    assert continuation["items"]
    readback = post(client, "/v1/memory/entries/list", {"scope_id": scope})["entries"]
    assert {digest(item) for item in readback} >= {digest(item) for item in memory}
    return {
        "scope": scope,
        "ungranted_scope": scopes[1]["scope_id"],
        "memory": memory,
        "candidate": candidate,
        "first_candidate": first,
        "published": published,
        "artifact": artifact,
        "grant": grant,
        "binding": binding,
        "cursor": cursor,
        "continuation": continuation,
    }


@pytest.fixture(scope="session")
def case():
    # Missing gate inputs are errors, never skips or an implicit local database.
    assert os.environ["PC_UPGRADE_SOURCE_VERSION"] == "1.3.0", "Add a reviewed adapter for another source release"
    configuration = os.environ["PC_UPGRADE_CONFIGURATION"]
    if configuration != "full-server-no-vector":
        pytest.fail(f"Unimplemented acceptance configuration: {configuration}")
    return {
        "configuration": configuration,
        "source": Path(os.environ["PC_UPGRADE_SOURCE_PYTHON"]),
        "target": Path(os.environ["PC_UPGRADE_TARGET_PYTHON"]),
        "backend": os.environ["PC_UPGRADE_BACKEND"],
        "work": Path(os.environ["PC_UPGRADE_WORK"]),
        "report": Path(os.environ["PC_UPGRADE_REPORT_DIR"]),
    }


@pytest.fixture(scope="session")
def historical(case):
    if case["backend"] == "oceanbase":
        assert not snapshot(case, "source", "empty")["tables"], "Refusing to write into a nonempty test database"
        assert not snapshot(case, "target", "fresh-empty", fresh=True)["tables"]
    with server(case["source"], environment_for(case), case["work"], case["report"] / "source-server.log") as client:
        value = seed(client, candidate_prefix="/v1/artifact-candidates")
    verify_authorization(case, "source", value)
    value["task"] = task_state(case, "source", "seed", value["scope"])
    value["schema"] = snapshot(case, "source", "source")
    assert {"pc_artifact_candidate_heads", "pc_artifact_candidate_versions", "pc_scopes", "pc_artifacts"} <= set(
        value["schema"]["tables"]
    )
    (case["report"] / "source-fixture.json").write_text(json.dumps(value, indent=2) + "\n")
    return value


@pytest.fixture(scope="session")
def migrated(case, historical):
    environment = environment_for(case)
    env_file = case["work"] / "deployment.env"
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in environment.items() if key.startswith("POWERCONTEXT_"))
    )
    common = ["--env-file", str(env_file)]
    if case["backend"] == "oceanbase":
        common += ["--evidence-dir", str(case["report"] / "maintenance"), "--lock-coordination", "single-host"]

    def migration(operation, *options):
        text = command(
            [console(case["target"]), "server", "db-migrate", operation, *common, *options],
            environment,
            case["work"],
            case["report"] / f"migration-{operation}.log",
        )
        result = json.loads(text)
        assert not result.get("error"), result
        return result

    migration("status")
    plan = migration("plan", "--backup", "skip")
    assert plan.get("plan_id"), "The public plan must identify exactly what will be applied"
    # These are disposable fixtures; all Server processes are stopped. This
    # explicit no-backup choice is never a recommendation for production.
    migration(
        "apply",
        "--backup",
        "skip",
        "--plan-id",
        plan["plan_id"],
        "--yes",
        "--maintenance-confirmed",
        "--accept-no-backup",
    )
    verified = migration("verify")
    assert verified.get("state") == "ready" and verified.get("server_ready") is True, (
        "Full Server readiness is required; registered-bundle verification is insufficient",
        verified,
    )
    return {"verified": verified, "schema": snapshot(case, "target", "migrated"), "historical": historical}


@pytest.fixture
def target(case, migrated):
    with server(case["target"], environment_for(case), case["work"], case["report"] / "target-server.log") as client:
        yield client


def test_source_release_database(historical):
    assert historical["memory"] and historical["candidate"]["version"] == 2
    assert historical["task"]["requested_generation"] > historical["task"]["handled_generation"]


def test_public_migration_commands(migrated):
    assert migrated["verified"]["server_ready"] is True


def test_target_startup(target):
    response = target.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] in {"ready", "degraded"}  # No inference provider in this explicit-write profile.


def test_read_write_and_retrieval(target, historical):
    scope = historical["scope"]
    records = post(target, "/v1/memory/entries/list", {"scope_id": scope})["entries"]
    assert {digest(item) for item in records} >= {digest(item) for item in historical["memory"]}
    text = "Upgrade fixture accepts new writes after migration."
    written = post(target, "/v1/memory/remember", {"scope_id": scope, "kind": "fact", "text": text})["records"]
    assert written and written[0]["text"] == text
    found = post(target, "/v1/memory/search", {"scope_id": scope, "query": "Upgrade fixture", "mode": "fts"})["hits"]
    texts = {item["memory"]["text"] for item in found}
    assert texts >= {text, *(item["text"] for item in historical["memory"])}


def test_authorization(case, migrated):
    historical = migrated["historical"]
    with server(
        case["target"], environment_for(case), case["work"], case["report"] / "authorization-server.log"
    ) as client:
        assert client.get("/v1/scopes", headers={"Authorization": "Bearer invalid-fixture-token"}).status_code == 401
        # The stable idempotency key must resolve to the SAME persisted grant.
        assert post(client, "/v1/access/bindings/create", historical["grant"]) == historical["binding"]
    # Stop the CLI before opening the same database with the installed SDK,
    # especially for the embedded engine's exclusive process ownership.
    verify_authorization(case, "target", historical)


def test_candidates_and_processing(case, migrated):
    historical = migrated["historical"]
    # Native typed SDK loading/acknowledgment verifies stored invocation formats
    # and generation idempotency; model-driven Worker quality is a separate suite.
    state = task_state(case, "target", "verify", historical["scope"])
    assert {key: state["before"][key] for key in historical["task"]} == historical["task"]
    assert state["after"]["handled_generation"] == historical["task"]["requested_generation"]
    with server(case["target"], environment_for(case), case["work"], case["report"] / "candidate-server.log") as client:
        candidate = historical["candidate"]
        request = {"scope_id": historical["scope"], "candidate_id": candidate["candidate_id"]}
        current = post(client, "/v1/candidates/get", request)
        for key in ("candidate_id", "version", "status", "proposal", "source_refs", "artifact_refs"):
            assert current[key] == candidate[key]
        approved = post(client, "/v1/candidates/approve", {**request, "expected_version": candidate["version"]})
        assert approved["status"] == "approved" and approved["result_artifact"]
        repeated = client.post("/v1/candidates/approve", json={**request, "expected_version": candidate["version"]})
        assert repeated.status_code == 409, "A stale approval must not publish a duplicate Artifact"


def test_preserved_history_and_cursors(target, historical):
    expected = historical["continuation"]
    page = target.get("/v1/scopes", params={"limit": 1, "cursor": historical["cursor"]})
    assert page.status_code == 200
    assert page.json() == expected
    artifact = post(
        target,
        "/v1/experience/get",
        {"scope_id": historical["scope"], "artifact": historical["published"]["result_artifact"]},
    )
    assert artifact == historical["artifact"]
    history = post(
        target,
        "/v1/candidates/history",
        {"scope_id": historical["scope"], "candidate_id": historical["candidate"]["candidate_id"]},
    )["versions"]
    for version in (historical["first_candidate"], historical["candidate"]):
        stored = next(item for item in history if item["version"] == version["version"])
        for key in ("version", "proposal", "source_refs", "artifact_refs"):
            assert stored[key] == version[key]


# Quoted tokens and comments are opaque: whitespace, quote characters and
# AUTO_INCREMENT text inside them are schema content, not formatting.
DDL_TOKENS = re.compile(
    r"""'(?:''|\\.|[^'\\])*' | "(?:""|\\.|[^"\\])*" | `(?:``|\\.|[^`\\])*`
    | \[(?:\]\]|[^\]])*\] | /\*.*?\*/ | --[^\r\n]*
    | [A-Za-z_\u0080-\U0010ffff][A-Za-z_0-9$\u0080-\U0010ffff]* | [0-9]+ | ->> | -> | <= | >= | <> | != | \|\| | << | >> | [^\s]
    """,
    re.VERBOSE | re.DOTALL,
)


def canonical_schema(snapshot):
    def normalize(sql):
        tokens = DDL_TOKENS.findall(sql)
        table = [lexeme.upper() for lexeme in tokens[:2]] == ["CREATE", "TABLE"]
        depth = 0
        closed_definition = False
        normalized = []
        index = 0
        while index < len(tokens):
            lexeme = tokens[index]
            # Only ignore the table-level next-id counter after the definition.
            # Column AUTO_INCREMENT attributes and all quoted contents remain.
            if (
                table
                and closed_definition
                and depth == 0
                and lexeme.upper() == "AUTO_INCREMENT"
                and tokens[index + 1 : index + 2] == ["="]
                and index + 2 < len(tokens)
                and tokens[index + 2].isdigit()
            ):
                index += 3
                continue
            if lexeme == "(":
                depth += 1
            elif lexeme == ")":
                depth -= 1
                closed_definition = depth == 0
            normalized.append(lexeme)
            index += 1
        return tuple(normalized)

    return {
        kind: {name: normalize(sql or "") for name, sql in snapshot.get(kind, {}).items()}
        for kind in ("tables", "indexes", "views", "triggers")
    }


def test_fresh_upgrade_equivalence(case, migrated):
    with server(
        case["target"], environment_for(case, fresh=True), case["work"], case["report"] / "fresh-server.log"
    ) as client:
        fresh = seed(client, candidate_prefix="/v1/candidates")
        hits = post(
            client, "/v1/memory/search", {"scope_id": fresh["scope"], "query": "Upgrade fixture", "mode": "fts"}
        )["hits"]
        assert {item["memory"]["text"] for item in hits} >= {item["text"] for item in fresh["memory"]}
    inventory = snapshot(case, "target", "fresh", fresh=True)
    actual = canonical_schema(migrated["schema"])
    expected = canonical_schema(inventory)
    assert actual == expected, (
        "Upgraded and fresh database objects differ; no blanket table/constraint exclusions allowed"
    )
    evidence = {
        "backend": case["backend"],
        "configuration": case["configuration"],
        "engine_version": inventory["engine_version"],
        "driver_version": inventory["driver_version"],
        "source_schema": digest(migrated["historical"]["schema"]),
        "target_schema": digest(actual),
        "fresh_schema": digest(expected),
    }
    if case["backend"] == "oceanbase":
        evidence.update(tenant_mode=inventory["tenant_mode"], proxy_version=inventory["proxy_version"])
    Path(os.environ["PC_UPGRADE_EVIDENCE"]).write_text(json.dumps(evidence, indent=2) + "\n")
