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

"""Collector consent, indexed enrollment and incremental messages with fixture hosts."""

import importlib
import json
import os
import sqlite3
from time import monotonic, time

import pytest

from tests.codex_plugin.test_image_capture import _append
from tests.codex_plugin.test_image_delivery import _message, _setup


def _index(config, path, tmp_path, created):
    with sqlite3.connect(config.codex_home() / "state_5.sqlite") as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS threads(id,cwd,rollout_path,created_at,created_at_ms,updated_at,archived)"
        )
        connection.execute(
            "INSERT INTO threads VALUES(?,?,?,?,?,?,0)",
            ("session", str(tmp_path), str(path), int(created), int(created * 1000), int(time())),
        )


def test_existing_thread_starts_at_end_and_only_new_turn_is_delivered(image_capture_module, monkeypatch, tmp_path):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    _append(path, "turn_context", {"turn_id": "historical"})
    _message(path, "old", [b"\x89PNG\r\n\x1a\nold"])
    _index(config, path, tmp_path, time() - 100)
    began = time()
    watcher.poll(outbox, settings, began=began)
    assert requests == []
    _append(path, "turn_context", {"turn_id": "new"})
    _message(path, "new", [b"\x89PNG\r\n\x1a\nnew"])
    watcher.poll(outbox, settings, began=began)
    assert len(requests) == 1 and requests[0]["origin"]["turn_id"] == "new"
    watcher.poll(outbox, settings, began=began)
    assert len(requests) == 1


def test_newly_created_thread_keeps_first_image_and_restart_keeps_cursor(image_capture_module, monkeypatch, tmp_path):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    began = time() - 10
    _index(config, path, tmp_path, time())
    _append(path, "turn_context", {"turn_id": "turn-1"})
    _message(path, "one", [b"\x89PNG\r\n\x1a\nfirst"])
    watcher.poll(outbox, settings, began=began)
    assert len(requests) == 1
    _append(path, "turn_context", {"turn_id": "turn-2"})
    _message(path, "two", [b"\x89PNG\r\n\x1a\nsecond"])
    # Re-opening the companion must resume, rather than re-arm at EOF.
    watcher.poll(type(outbox)(outbox.path), settings, began=time())
    assert len(requests) == 2


def test_new_authorized_thread_created_during_collector_downtime_is_delivered(
    image_capture_module, monkeypatch, tmp_path
):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    _index(config, path, tmp_path, time())
    _append(path, "event_msg", {"type": "task_started", "turn_id": "during-downtime"})
    _message(path, "offline-first", [b"\x89PNG\r\n\x1a\nfirst"])
    # The persisted consent predates this thread, while the recovering collector
    # starts afterwards. No prior enrollment or hook receipt exists.
    watcher.poll(outbox, settings, began=time() + 10)
    watcher.poll(type(outbox)(outbox.path), settings, began=time() + 20)
    assert [request["origin"]["message_id"] for request in requests] == ["offline-first"]


def test_full_watch_budget_does_not_block_staged_delivery(image_capture_module, monkeypatch, tmp_path):
    config, outbox, worker, settings, path, payload, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    _index(config, path, tmp_path, time())
    _append(path, "event_msg", {"type": "task_started", "turn_id": "turn-1"})
    _message(path, "pending", [b"\x89PNG\r\n\x1a\nstaged"])
    image_capture_module.capture(payload, settings, deadline=monotonic() + 20, background=True)
    worker._read_jobs(outbox, settings)
    assert len(outbox.pending()) == 1
    for index in range(importlib.import_module("image_outbox").MAX_JOBS):
        retained = path.with_name(f"old-{index}.jsonl")
        retained.write_bytes(path.read_bytes())
        # These pre-consent cursors may not be discarded and backfilled.
        outbox.enroll(
            {
                "transcript_path": str(retained),
                "server_url": settings.server_url,
                "workspace": config.workspace_key(tmp_path),
                "epoch": config.enabled_profile(tmp_path, settings.server_url)["epoch"],
                "session_id": "session",
                "file_identity": watcher.transcript_info({"transcript_path": str(retained), "session_id": "session"})[
                    2
                ],
                "variant": "desktop",
            },
            retained.stat().st_size,
            skip_partial=False,
        )
    assert watcher.poll(outbox, settings, began=time()) == "image_watcher_limit"
    assert len(requests) == 1 and outbox.pending() == []


def test_idle_authorized_threads_release_watch_budget(image_capture_module, monkeypatch, tmp_path):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    _index(config, path, tmp_path, time())
    _append(path, "event_msg", {"type": "task_started", "turn_id": "new"})
    _message(path, "new", [b"\x89PNG\r\n\x1a\nnew"])
    profile = config.enabled_profile(tmp_path, settings.server_url)
    for index in range(importlib.import_module("image_outbox").MAX_JOBS):
        retained = path.with_name(f"completed-{index}.jsonl")
        retained.write_bytes(path.read_bytes())
        outbox.enroll(
            {
                "transcript_path": str(retained),
                "server_url": settings.server_url,
                "workspace": config.workspace_key(tmp_path),
                "epoch": profile["epoch"],
                "created_at": int(time() * 1000),
            },
            retained.stat().st_size,
            skip_partial=False,
        )
    assert watcher.poll(outbox, settings, began=time()) is None
    assert len(requests) == 1 and len(outbox.watches()) == 1


def test_disabled_period_and_scope_change_are_not_backfilled(image_capture_module, monkeypatch, tmp_path):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    _index(config, path, tmp_path, time() - 100)
    watcher.poll(outbox, settings, began=time())
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="scope", enabled=False)
    _append(path, "turn_context", {"turn_id": "disabled-turn"})
    _message(path, "disabled", [b"\x89PNG\r\n\x1a\ndisabled"])
    watcher.poll(outbox, settings, began=time())
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="new-scope", enabled=True)
    monkeypatch.setattr(watcher, "resolve_scope_id", lambda *args, **kwargs: "new-scope")
    watcher.poll(outbox, settings, began=time())
    assert requests == []
    _append(path, "turn_context", {"turn_id": "new-scope-turn"})
    _message(path, "new", [b"\x89PNG\r\n\x1a\nnew"])
    watcher.poll(outbox, settings, began=time())
    assert requests[0]["scope_id"] == "new-scope"


def test_reenable_does_not_backfill_an_already_watched_turn(image_capture_module, monkeypatch, tmp_path):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    _index(config, path, tmp_path, time())
    _append(path, "event_msg", {"type": "task_started", "turn_id": "active"})
    _message(path, "before-disable", [b"\x89PNG\r\n\x1a\nbefore"])
    watcher.poll(outbox, settings, began=time() - 10)
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="scope", enabled=False)
    _message(path, "during-disable", [b"\x89PNG\r\n\x1a\nduring"])
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="scope", enabled=True)
    watcher.poll(outbox, settings, began=time())
    _append(path, "event_msg", {"type": "task_started", "turn_id": "new"})
    _message(path, "after-enable", [b"\x89PNG\r\n\x1a\nafter"])
    watcher.poll(outbox, settings, began=time())
    assert [request["origin"]["message_id"] for request in requests] == ["before-disable", "after-enable"]


def test_index_query_does_not_accept_unconfigured_workspace(image_capture_module, monkeypatch, tmp_path):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    other = tmp_path / "unconfigured"
    other.mkdir()
    _index(config, path, other, time())
    _append(path, "turn_context", {"turn_id": "other"})
    _message(path, "other", [b"\x89PNG\r\n\x1a\nother"])
    watcher.poll(outbox, settings, began=time() - 5)
    assert requests == [] and outbox.watches() == []


def test_partial_line_at_enrollment_does_not_break_next_turn(image_capture_module, monkeypatch, tmp_path):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    _index(config, path, tmp_path, time() - 100)
    with path.open("ab") as stream:
        stream.write(b'{"type":"response_item","payload":')
    watcher.poll(outbox, settings, began=time())
    with path.open("ab") as stream:
        stream.write(b"{}}\n")
    _append(path, "turn_context", {"turn_id": "new"})
    _message(path, "new", [b"\x89PNG\r\n\x1a\nnew"])
    watcher.poll(outbox, settings, began=time())
    assert len(requests) == 1
    assert json.loads(outbox.watches()[0]["payload"])["scope_id"] == "scope"


def test_second_turn_without_repeated_context_is_captured(image_capture_module, monkeypatch, tmp_path):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    _index(config, path, tmp_path, time())
    for turn in ("one", "two"):
        _append(path, "event_msg", {"type": "task_started", "turn_id": turn})
        if turn == "one":
            _append(path, "turn_context", {"turn_id": turn})
        _message(path, turn, [b"\x89PNG\r\n\x1a\n" + turn.encode()])
        _append(path, "event_msg", {"type": "task_complete", "turn_id": turn})
    watcher.poll(outbox, settings, began=time() - 10)
    assert [request["origin"]["turn_id"] for request in requests] == ["one", "two"]


def test_windows_verbatim_index_paths_match_project_consent(image_capture_module, monkeypatch, tmp_path):
    if os.name != "nt":
        import pytest

        pytest.skip("Windows path representation")
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    from pathlib import Path

    _index(config, Path("\\\\?\\" + str(path)), Path("\\\\?\\" + str(tmp_path)), time())
    _append(path, "event_msg", {"type": "task_started", "turn_id": "one"})
    _message(path, "message", [b"\x89PNG\r\n\x1a\ndata"])
    watcher.poll(outbox, settings, began=time() - 10)
    assert len(requests) == 1


@pytest.mark.parametrize("unavailable", ["unsupported_header", "removed_watch", "removed_workspace"])
def test_unavailable_old_session_does_not_block_current_images(
    image_capture_module, monkeypatch, tmp_path, unavailable
):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    old = path.with_name("old.jsonl")
    old.write_bytes(path.read_bytes())
    _index(config, old, tmp_path, time() - 100)
    if unavailable == "removed_watch":
        watcher.poll(outbox, settings, began=time())
        old.unlink()
    elif unavailable == "unsupported_header":
        old.write_text(
            json.dumps({"type": "session_meta", "payload": {"source": {"subagent": "review"}}}) + "\n",
            encoding="utf-8",
        )
    else:
        removed = tmp_path / "removed-project"
        removed.mkdir()
        config.write_profile(removed, server_url=settings.server_url, scope_id="scope", enabled=True)
        removed.rmdir()
    _index(config, path, tmp_path, time())
    _append(path, "event_msg", {"type": "task_started", "turn_id": "current"})
    _message(path, "current", [b"\x89PNG\r\n\x1a\ncurrent"])
    watcher.poll(outbox, settings, began=time() - 10)
    assert len(requests) == 1 and requests[0]["origin"]["turn_id"] == "current"
    if unavailable == "removed_watch":
        assert all(row["path"] != str(old) for row in outbox.watches())
