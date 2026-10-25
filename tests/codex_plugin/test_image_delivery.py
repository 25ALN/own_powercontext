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

"""Observable collector delivery and recovery; fixtures do not prove host support."""

import base64
import importlib
import json
from time import monotonic

import pytest

from tests.codex_plugin.test_image_capture import _append


def _setup(module, monkeypatch, tmp_path, *, version="0.162.0-alpha.2"):
    config = importlib.import_module("image_capture_config")
    outbox = importlib.import_module("image_outbox")
    worker = importlib.import_module("image_capture_worker")
    monkeypatch.delenv("POWERCONTEXT_CODEX_CAPTURE_IMAGES", raising=False)
    settings = module.CodexPluginSettings()
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="scope", enabled=True)
    monkeypatch.setattr(worker, "launch", lambda: None)
    path = config.codex_home() / "sessions" / "rollout.jsonl"
    path.parent.mkdir(parents=True)
    _append(
        path,
        "session_meta",
        {"id": "session", "cli_version": version, "source": "vscode", "originator": "Codex Desktop"},
    )
    payload = {
        "session_id": "session",
        "turn_id": "turn-1",
        "cwd": str(tmp_path),
        "transcript_path": str(path),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "same prompt",
    }
    requests = []

    def post(route, request, **kwargs):
        requests.append(request)
        return {"status": "accepted", "request_id": "mr_" + str(len(requests)).zfill(64)}

    monkeypatch.setattr(worker, "_post_json", post)
    monkeypatch.setattr(module, "resolve_scope_id", lambda *args, **kwargs: "scope")
    monkeypatch.setattr(
        importlib.import_module("image_desktop_watch"), "resolve_scope_id", lambda *args, **kwargs: "scope"
    )
    return config, outbox.Outbox(outbox.outbox_path()), worker, settings, path, payload, requests


def _message(path, message_id, images):
    _append(
        path,
        "response_item",
        {
            "id": message_id,
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "same prompt"}]
            + [
                {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(image).decode()}
                for image in images
            ],
        },
    )


@pytest.mark.parametrize("version", ["0.162.0-alpha.2", "0.162.0-alpha.17.2"])
def test_live_registration_captures_multiple_messages_and_replay_once(
    image_capture_module, monkeypatch, tmp_path, version
):
    config, outbox, worker, settings, path, payload, requests = _setup(
        image_capture_module, monkeypatch, tmp_path, version=version
    )
    # Historical attachments must not be captured when consent starts.
    _append(path, "turn_context", {"turn_id": "old"})
    _message(path, "old", [b"\x89PNG\r\n\x1a\nold"])
    _append(path, "turn_context", {"turn_id": "turn-1"})
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    images = [b"\x89PNG\r\n\x1a\none", b"\x89PNG\r\n\x1a\ntwo"]
    _message(path, "msg-1", images)
    _message(path, "msg-2", images[:1])
    worker.drain(settings, wait_seconds=0)
    assert [base64.b64decode(request["bytes_base64"]) for request in requests] == images + images[:1]
    assert requests[0]["capture_key"] == requests[1]["capture_key"] != requests[2]["capture_key"]
    assert requests[0]["origin"]["representation"] == "host_image"
    image_capture_module.enqueue_capture(payload | {"hook_event_name": "Interrupt"}, settings, deadline=monotonic() + 6)
    worker.drain(settings, wait_seconds=0)
    assert len(requests) == 3
    assert all(item["state"] == "accepted" for item in outbox.status(config.workspace_key(tmp_path))[0]["attachments"])


def test_bytes_staged_before_upload_survive_transcript_loss_and_new_turn(image_capture_module, monkeypatch, tmp_path):
    config, outbox, worker, settings, path, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    _append(path, "turn_context", {"turn_id": "turn-1"})
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    original = b"\x89PNG\r\n\x1a\nretained"
    _message(path, "message", [original])

    def offline(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(worker, "_post_json", offline)
    worker.drain(settings, wait_seconds=0)
    receipt = outbox.status(config.workspace_key(tmp_path))[0]["attachments"][0]
    assert receipt["state"] == "pending" and receipt["error"] == "image_upload_unavailable"
    # Registered later turns must not overwrite this durable delivery.
    _append(path, "turn_context", {"turn_id": "turn-2"})
    image_capture_module.enqueue_capture(payload | {"turn_id": "turn-2"}, settings, deadline=monotonic() + 6)
    path.unlink()
    outbox.reset_delivery(config.workspace_key(tmp_path))
    monkeypatch.setattr(
        worker,
        "_post_json",
        lambda route, request, **kw: requests.append(request) or {"status": "accepted", "request_id": "mr_" + "1" * 64},
    )
    worker.drain(settings, wait_seconds=0)
    assert base64.b64decode(requests[0]["bytes_base64"]) == original


def test_partial_record_waits_without_dropping_attachment(image_capture_module, monkeypatch, tmp_path):
    _, _, worker, settings, path, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    _append(path, "turn_context", {"turn_id": "turn-1"})
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    data = b"\x89PNG\r\n\x1a\npartial"
    saved = path.read_bytes()
    _message(path, "message", [data])
    raw = path.read_bytes()[len(saved) :]
    path.write_bytes(saved + raw[: len(raw) // 2])
    worker.drain(settings, wait_seconds=0)
    assert requests == []
    with path.open("ab") as stream:
        stream.write(raw[len(raw) // 2 :])
    worker.drain(settings, wait_seconds=0)
    assert base64.b64decode(requests[0]["bytes_base64"]) == data


@pytest.mark.parametrize("change", ["disable", "scope", "endpoint"])
def test_saved_consent_change_prevents_delivery(image_capture_module, monkeypatch, tmp_path, change):
    config, outbox, worker, settings, path, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    _append(path, "turn_context", {"turn_id": "turn-1"})
    _message(path, "message", [b"\x89PNG\r\n\x1a\ndata"])
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    config.write_profile(
        tmp_path,
        server_url="http://other.invalid" if change == "endpoint" else settings.server_url,
        scope_id="other" if change == "scope" else "scope",
        enabled=change != "disable",
    )
    worker.drain(settings, wait_seconds=0)
    assert requests == []
    assert outbox.status(config.workspace_key(tmp_path))


def test_wrong_turn_external_url_and_oversized_inputs_are_not_delivered(image_capture_module, monkeypatch, tmp_path):
    config, outbox, worker, settings, path, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    _append(path, "turn_context", {"turn_id": "turn-1"})
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    _append(
        path,
        "response_item",
        {
            "id": "remote",
            "type": "message",
            "role": "user",
            "content": [{"type": "input_image", "image_url": "https://private.example.invalid/image.png"}],
        },
    )
    worker.drain(settings, wait_seconds=0)
    assert requests == []
    assert outbox.status(config.workspace_key(tmp_path))[0]["error"] == "image_attachment_format"


def test_four_image_limit_is_visible_and_keeps_order(image_capture_module, monkeypatch, tmp_path):
    config, outbox, worker, settings, path, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    _append(path, "turn_context", {"turn_id": "turn-1"})
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    _message(path, "message", [b"\x89PNG\r\n\x1a\n" + bytes([i]) for i in range(5)])
    _append(path, "event_msg", {"type": "task_complete", "turn_id": "turn-1"})
    worker.drain(settings, wait_seconds=0)
    assert [request["origin"]["attachment_index"] for request in requests] == [0, 1, 2, 3]
    status = outbox.status(config.workspace_key(tmp_path))[0]
    assert status["state"] == "done" and status["error"] == "image_attachment_limit"


def test_consent_is_exact_workspace_and_main_default_off_is_quiet(image_capture_module, monkeypatch, tmp_path):
    config, _, _, settings, _, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    result = image_capture_module.enqueue_capture(payload | {"cwd": str(other)}, settings, deadline=monotonic() + 6)
    assert result["status"] == "disabled" and requests == []
    assert config.enabled_profile(other, settings.server_url) is None


def test_saved_disable_overrides_inherited_terminal_opt_in(image_capture_module, monkeypatch, tmp_path):
    config, _, _, settings, _, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    monkeypatch.setenv("POWERCONTEXT_CODEX_CAPTURE_IMAGES", "true")
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="scope", enabled=False)
    settings = settings.model_copy(update={"capture_images": True})
    assert image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)["status"] == "disabled"
    assert requests == []


def test_broken_optional_image_profile_does_not_disable_ordinary_settings(image_capture_module, monkeypatch, tmp_path):
    config, _, _, settings, _, _, _ = _setup(image_capture_module, monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    config.write_profile(tmp_path, server_url="http://other.invalid", scope_id="scope", enabled=True)
    restored = image_capture_module.CodexPluginSettings()
    assert restored.server_url == settings.server_url and not restored.capture_images
    config.config_path().write_text("{}", encoding="utf-8")
    assert not image_capture_module.CodexPluginSettings().capture_images


def test_changed_bytes_under_one_message_identity_report_conflict_after_delivery(
    image_capture_module, monkeypatch, tmp_path
):
    config, outbox, worker, settings, path, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    _append(path, "turn_context", {"turn_id": "turn-1"})
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    _message(path, "message", [b"\x89PNG\r\n\x1a\none"])
    worker.drain(settings, wait_seconds=0)
    # Delivered blobs can be released, but their fingerprint must remain.
    _message(path, "message", [b"\x89PNG\r\n\x1a\ntwo"])
    worker.drain(settings, wait_seconds=0)
    assert len(requests) == 1
    assert outbox.status(config.workspace_key(tmp_path))[0]["error"] == "image_capture_conflict"


@pytest.mark.parametrize("recovery", ["automatic", "retry", "legacy_error"])
def test_capacity_pressure_resumes_from_saved_cursor_after_upload(
    image_capture_module, monkeypatch, tmp_path, recovery
):
    config, outbox, worker, settings, path, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    pictures = [b"\x89PNG\r\n\x1a\none", b"\x89PNG\r\n\x1a\ntwo"]
    monkeypatch.setattr(importlib.import_module("image_outbox"), "MAX_PENDING_BYTES", len(pictures[0]))
    _append(path, "turn_context", {"turn_id": "turn-1"})
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    job = outbox.jobs()[0]
    job_id = job["id"]
    for index, picture in enumerate(pictures):
        _message(path, "message-" + str(index), [picture])
    worker.drain(settings, wait_seconds=0)
    assert [base64.b64decode(request["bytes_base64"]) for request in requests] == pictures[:1]
    if recovery == "retry":
        outbox.reset_delivery(config.workspace_key(tmp_path))
    elif recovery == "legacy_error":
        # Preserve recovery for the persisted state emitted by earlier collectors.
        outbox.finish(job_id, state="error", error="image_outbox_full")
        monkeypatch.setattr(importlib.import_module("image_outbox"), "MAX_JOBS", 1)
        with pytest.raises(ValueError, match="image_outbox_full"):
            outbox.register(json.loads(job["payload"]) | {"turn_id": "new-turn"}, cursor=path.stat().st_size)
    for _ in range(3):
        worker.drain(settings, wait_seconds=0)
    assert [base64.b64decode(request["bytes_base64"]) for request in requests] == pictures
    assert all(item["state"] == "accepted" for item in outbox.status(config.workspace_key(tmp_path))[0]["attachments"])


@pytest.mark.parametrize("change", ["disable", "endpoint"])
def test_ineligible_workspace_does_not_block_delivery_and_retains_its_backlog(
    image_capture_module, monkeypatch, tmp_path, change
):
    config, outbox, worker, settings, path, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    _append(path, "turn_context", {"turn_id": "turn-1"})
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    pictures = [b"\x89PNG\r\n\x1a\n" + str(index).encode() for index in range(8)]
    for index, picture in enumerate(pictures):
        _message(path, "message-" + str(index), [picture])
    other = tmp_path / "enabled"
    other.mkdir()
    config.write_profile(other, server_url=settings.server_url, scope_id="scope", enabled=True)
    other_path = path.with_name("rollout-enabled.jsonl")
    _append(
        other_path,
        "session_meta",
        {
            "id": "other-session",
            "cli_version": "0.162.0-alpha.2",
            "source": "vscode",
            "originator": "Codex Desktop",
        },
    )
    _append(other_path, "turn_context", {"turn_id": "other-turn"})
    image_capture_module.enqueue_capture(
        payload
        | {
            "cwd": str(other),
            "session_id": "other-session",
            "turn_id": "other-turn",
            "transcript_path": str(other_path),
        },
        settings,
        deadline=monotonic() + 6,
    )
    other_picture = b"\x89PNG\r\n\x1a\nother"
    _message(other_path, "other-message", [other_picture])
    upload = worker._post_json

    def offline(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(worker, "_post_json", offline)
    worker.drain(settings, wait_seconds=0)
    config.write_profile(
        tmp_path,
        server_url="http://other.invalid" if change == "endpoint" else settings.server_url,
        scope_id="scope",
        enabled=change != "disable",
    )
    _message(path, "off-period", [b"\x89PNG\r\n\x1a\nnot-authorized"])
    for workspace in (tmp_path, other):
        outbox.reset_delivery(config.workspace_key(workspace))
    monkeypatch.setattr(worker, "_post_json", upload)
    for _ in range(3):
        worker.drain(settings, wait_seconds=0)
    assert [base64.b64decode(request["bytes_base64"]) for request in requests] == [other_picture]
    backlog = outbox.status(config.workspace_key(tmp_path))[0]["attachments"]
    assert len(backlog) == 8 and all(item["state"] == "pending" for item in backlog)
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="scope", enabled=True)
    for _ in range(3):
        worker.drain(settings, wait_seconds=0)
    assert [base64.b64decode(request["bytes_base64"]) for request in requests] == [other_picture, *pictures]


def test_capacity_reader_recovery_does_not_backfill_after_consent_renewal(image_capture_module, monkeypatch, tmp_path):
    config, _, worker, settings, path, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    picture = b"\x89PNG\r\n\x1a\ndata"
    monkeypatch.setattr(importlib.import_module("image_outbox"), "MAX_PENDING_BYTES", len(picture))
    _append(path, "turn_context", {"turn_id": "turn-1"})
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    _message(path, "first", [picture])
    _message(path, "capacity-blocked", [picture])
    worker.drain(settings, wait_seconds=0)
    assert len(requests) == 1
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="scope", enabled=False)
    _message(path, "off-period", [picture])
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="scope", enabled=True)
    for _ in range(3):
        worker.drain(settings, wait_seconds=0)
    assert len(requests) == 1


@pytest.mark.parametrize("protected", ["watching", "pending", "error", "capacity"])
def test_completed_jobs_release_capacity_without_discarding_unfinished_work(
    image_capture_module, monkeypatch, tmp_path, protected
):
    config, outbox, _, settings, path, payload, _ = _setup(image_capture_module, monkeypatch, tmp_path)
    monkeypatch.setattr(importlib.import_module("image_outbox"), "MAX_JOBS", 3)
    _append(path, "turn_context", {"turn_id": "turn-1"})
    image_capture_module.enqueue_capture(payload, settings, deadline=monotonic() + 6)
    original = outbox.jobs()[0]
    job = json.loads(original["payload"])
    if protected in {"pending", "error"}:
        outbox.advance(
            original["id"],
            original["cursor"],
            [({"capture_key": "retained", "origin": {"attachment_index": 0}}, b"retained bytes")],
            reader={},
            state="done",
        )
        if protected == "error":
            outbox.retry(outbox.pending()[0]["id"], error="image_upload_http_409", permanent=True)
    elif protected == "capacity":
        outbox.finish(original["id"], state="error", error="image_outbox_full")
    for turn in ("finished-1", "finished-2"):
        identity = outbox.register(job | {"turn_id": turn}, cursor=0)
        outbox.finish(identity, state="done")

    # A live reader or undelivered attachment must not prevent recycling a
    # completed turn when the shared queue reaches its job budget.
    outbox.register(job | {"turn_id": "next-turn"}, cursor=0)
    status = {item["turn_id"]: item for item in outbox.status(config.workspace_key(tmp_path))}
    assert set(status) == {"turn-1", "finished-2", "next-turn"}
    if protected in {"pending", "error"}:
        outbox.reset_delivery(config.workspace_key(tmp_path))
        assert [row["bytes"] for row in outbox.pending()] == [b"retained bytes"]
    else:
        assert original["id"] in {row["id"] for row in outbox.jobs()}
