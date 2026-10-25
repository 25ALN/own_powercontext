# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Simulated Hook/rollout inputs protect bounded capture and identity handling."""

import base64
import json
import os
from datetime import UTC, datetime
from io import StringIO
from time import monotonic, sleep

import pytest


def _environment(module, monkeypatch, tmp_path):
    home = tmp_path / "codex"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(module, "resolve_scope_id", lambda *args, **kwargs: "scope")
    requests = []

    def post(path, payload, **kwargs):
        requests.append(dict(payload))
        return {"status": "accepted", "request_id": "mr_" + str(len(requests)).zfill(64)}

    monkeypatch.setattr(module, "_post_json", post)
    settings = module.CodexPluginSettings(capture_images=True, image_capture_budget_seconds=0.2)
    transcript = home / "sessions" / "rollout.jsonl"
    transcript.parent.mkdir()
    _append(
        transcript,
        "session_meta",
        {"id": "session", "cli_version": "0.141.0", "source": "exec", "originator": "codex_exec"},
    )
    payload = {
        "session_id": "session",
        "turn_id": "turn-1",
        "cwd": str(tmp_path),
        "transcript_path": str(transcript),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "same text",
    }
    return settings, transcript, payload, requests


def _append(path, kind, payload):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"timestamp": datetime.now(UTC).isoformat(), "type": kind, "payload": payload}) + "\n")


def _turn(path, turn_id, images, text="same text"):
    _append(path, "turn_context", {"turn_id": turn_id})
    _append(
        path, "response_item", {"type": "message", "role": "user", "content": [{"type": "input_image"} for _ in images]}
    )
    _append(
        path,
        "event_msg",
        {"type": "user_message", "message": text, "local_images": [str(p) for p in images], "images": []},
    )


def _image(tmp_path, name="original.png"):
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    return path


def test_opt_in_capture_preserves_order_exact_bytes_and_replay_identity(image_capture_module, monkeypatch, tmp_path):
    module = image_capture_module
    settings, path, payload, requests = _environment(module, monkeypatch, tmp_path)
    originals = [_image(tmp_path, "red.png"), _image(tmp_path, "blue.png")]
    assert module.capture(payload, settings, deadline=monotonic() + 5)["status"] == "armed"
    _turn(path, payload["turn_id"], originals)
    module.capture(payload, settings, deadline=monotonic() + 5)
    stop = payload | {"hook_event_name": "Stop"}
    result = module.capture(stop, settings, deadline=monotonic() + 5)
    assert result["status"] == "accepted" and len(result["accepted"]) == 2
    assert [base64.b64decode(r["bytes_base64"]) for r in requests] == [p.read_bytes() for p in originals]
    assert [r["origin"]["attachment_index"] for r in requests] == [0, 1]
    assert module.capture(stop, settings, deadline=monotonic() + 5)["status"] == "replayed"
    assert len(requests) == 2
    next_payload = payload | {"turn_id": "turn-2"}
    module.capture(next_payload, settings, deadline=monotonic() + 5)
    _turn(path, "turn-2", [originals[1]])
    module.capture(next_payload | {"hook_event_name": "Stop"}, settings, deadline=monotonic() + 5)
    assert requests[0]["capture_key"] != requests[2]["capture_key"]


def test_empty_text_file_input_and_attachment_limit_are_explicit(image_capture_module, monkeypatch, tmp_path):
    module = image_capture_module
    settings, path, payload, requests = _environment(module, monkeypatch, tmp_path)
    payload["prompt"] = ""
    module.capture(payload, settings, deadline=monotonic() + 5)
    _turn(path, "turn-1", [_image(tmp_path, f"{i}.png") for i in range(5)], text="")
    result = module.capture(payload | {"hook_event_name": "Stop"}, settings, deadline=monotonic() + 5)
    assert len(requests) == 4 and result["not_accepted"] == 1
    assert all(r["context"]["text"] == "" for r in requests)


def test_current_turn_context_written_before_submit_is_captured(image_capture_module, monkeypatch, tmp_path):
    module = image_capture_module
    settings, path, payload, requests = _environment(module, monkeypatch, tmp_path)
    original = _image(tmp_path)
    _turn(path, "old-turn", [original])
    _append(path, "turn_context", {"turn_id": payload["turn_id"]})
    module.capture(payload, settings, deadline=monotonic() + 5)
    _append(path, "response_item", {"type": "message", "role": "user", "content": [{"type": "input_image"}]})
    _append(
        path,
        "event_msg",
        {"type": "user_message", "message": payload["prompt"], "local_images": [str(original)], "images": []},
    )
    result = module.capture(payload | {"hook_event_name": "Stop"}, settings, deadline=monotonic() + 5)
    assert result["status"] == "accepted" and len(requests) == 1
    assert requests[0]["origin"]["turn_id"] == payload["turn_id"]
    assert base64.b64decode(requests[0]["bytes_base64"]) == original.read_bytes()


@pytest.mark.parametrize(
    "failure", ["old-turn", "missing-original", "changed-original", "unknown-version", "truncated", "oversized"]
)
def test_capture_rejects_unverified_or_unavailable_inputs(image_capture_module, monkeypatch, tmp_path, failure):
    module = image_capture_module
    settings, path, payload, requests = _environment(module, monkeypatch, tmp_path)
    original = _image(tmp_path)
    module.capture(payload, settings, deadline=monotonic() + 5)
    _turn(path, "wrong-turn" if failure == "old-turn" else "turn-1", [original])
    if failure == "missing-original":
        original.unlink()
    elif failure == "changed-original":
        future = datetime.now(UTC).timestamp() + 60
        os.utime(original, (future, future))
    elif failure == "unknown-version":
        path.write_text(path.read_text().replace("0.141.0", "0.999.0"))
    elif failure == "truncated":
        path.write_text(path.read_text() + '{"type":')
    elif failure == "oversized":
        with path.open("a") as stream:
            stream.write(" " * (module.MAX_WINDOW + 1))
    with pytest.raises((module.CaptureError, OSError)):
        module.capture(payload | {"hook_event_name": "Stop"}, settings, deadline=monotonic() + 5)
    assert requests == []


def test_hook_default_off_does_not_read_input_and_timeout_never_blocks_host(
    image_capture_module, monkeypatch, tmp_path, capsys
):
    module = image_capture_module
    monkeypatch.delenv("POWERCONTEXT_CODEX_CAPTURE_IMAGES", raising=False)
    assert module.main() == 0
    assert not (tmp_path / "codex" / "powercontext" / "image-capture").exists()
    settings = module.CodexPluginSettings(capture_images=True, image_capture_budget_seconds=0.02)
    monkeypatch.setattr(module.sys, "stdin", StringIO("{}"))

    def delayed(*args, **kwargs):
        sleep(0.15)
        return {"status": "no_images"}

    monkeypatch.setattr(module, "capture", delayed)
    started = monotonic()
    assert module.main(settings) == 0 and monotonic() - started < 0.1
    assert "image_capture_timeout" in capsys.readouterr().out


def test_malformed_deep_hook_json_does_not_interrupt_host(image_capture_module, monkeypatch, capsys):
    module = image_capture_module
    settings = module.CodexPluginSettings(capture_images=True)
    depth = module.sys.getrecursionlimit() + 100
    monkeypatch.setattr(module.sys, "stdin", StringIO("[" * depth + "0" + "]" * depth))
    assert module.main(settings) == 0
    assert "use explicit image upload" in capsys.readouterr().out.lower()


def test_original_changed_between_metadata_check_and_open_is_not_accepted(image_capture_module, monkeypatch, tmp_path):
    module = image_capture_module
    settings, path, payload, requests = _environment(module, monkeypatch, tmp_path)
    original = _image(tmp_path)
    module.capture(payload, settings, deadline=monotonic() + 5)
    _turn(path, "turn-1", [original])
    open_file = module.os.open

    def change_before_open(name, *args, **kwargs):
        if str(name) == str(original):
            data = original.read_bytes()
            original.write_bytes(data[:8] + b"X" * (len(data) - 8))
            future = datetime.now(UTC).timestamp() + 60
            os.utime(original, (future, future))
        return open_file(name, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", change_before_open)
    with pytest.raises(module.CaptureError, match="image_original_changed"):
        module.capture(payload | {"hook_event_name": "Stop"}, settings, deadline=monotonic() + 5)
    assert requests == []
