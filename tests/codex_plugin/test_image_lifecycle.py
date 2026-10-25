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

"""Managed client consent and recovery; native OS execution is tested separately."""

import importlib
import subprocess
import sys
from time import time

import pytest

from tests.codex_plugin.test_image_capture import _append
from tests.codex_plugin.test_image_delivery import _message, _setup
from tests.codex_plugin.test_image_desktop_watch import _index


def test_setup_resolves_current_binding_and_last_disable_removes_lifecycle(image_capture_module, monkeypatch, tmp_path):
    config, _, _, settings, _, _, _ = _setup(image_capture_module, monkeypatch, tmp_path)
    service = importlib.import_module("collector_service")
    scope = importlib.import_module("scope_binding")
    actions = []
    bindings = {}
    monkeypatch.setattr(scope, "resolve_scope_id", lambda *args, **kwargs: "active-binding")
    monkeypatch.setattr(
        scope, "bind_scope", lambda workspace, selected, **kwargs: bindings.update({workspace: selected})
    )
    monkeypatch.setattr(
        scope,
        "_post_json",
        lambda *args, **kwargs: {
            "media": dict.fromkeys(("enabled", "available", "vision_configured", "processing_enabled"), True)
        },
    )
    monkeypatch.setattr(service, "install", lambda: actions.append("install"))
    monkeypatch.setattr(service, "uninstall", lambda: actions.append("uninstall"))
    other = tmp_path / "second-project"
    other.mkdir()
    config.configure_capture("setup", str(tmp_path), None, settings)
    config.configure_capture("setup", str(other), None, settings)
    assert config.enabled_profile(tmp_path, settings.server_url)["scope_id"] == "active-binding"
    assert bindings == {str(tmp_path): "active-binding", str(other): "active-binding"}
    config.configure_capture("disable", str(tmp_path), None, settings)
    assert "uninstall" not in actions and config.enabled_profile(other, settings.server_url)
    config.configure_capture("disable", str(other), None, settings)
    assert not config.any_enabled() and actions[-1] == "uninstall"


def test_setup_refuses_unready_server_without_authorizing_capture(image_capture_module, monkeypatch, tmp_path):
    config, _, _, settings, _, _, _ = _setup(image_capture_module, monkeypatch, tmp_path)
    scope = importlib.import_module("scope_binding")
    monkeypatch.setattr(scope, "resolve_scope_id", lambda *args, **kwargs: "active-binding")
    monkeypatch.setattr(scope, "_post_json", lambda *args, **kwargs: {"enabled": False})
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="active-binding", enabled=False)
    with pytest.raises(ValueError, match="image_server_not_ready"):
        config.configure_capture("setup", str(tmp_path), None, settings)
    assert not config.any_enabled()


def test_desktop_session_scope_switch_preserves_staged_original_destination(
    image_capture_module, monkeypatch, tmp_path
):
    config, outbox, worker, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    current = {"scope": "session-first"}
    monkeypatch.setattr(watcher, "resolve_scope_id", lambda *args, **kwargs: current["scope"])
    _index(config, path, tmp_path, time())
    _append(path, "turn_context", {"turn_id": "first"})
    _message(path, "first-image", [b"\x89PNG\r\n\x1a\nfirst"])
    original_post = worker._post_json
    monkeypatch.setattr(worker, "_post_json", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    watcher.poll(outbox, settings, began=time() - 10)
    assert requests == []
    current["scope"] = "session-second"
    monkeypatch.setattr(worker, "_post_json", original_post)
    outbox.reset_delivery(config.workspace_key(tmp_path))
    _append(path, "turn_context", {"turn_id": "second"})
    _message(path, "second-image", [b"\x89PNG\r\n\x1a\nsecond"])
    watcher.poll(outbox, settings, began=time() - 10)
    assert {request["origin"]["message_id"]: request["scope_id"] for request in requests} == {
        "first-image": "session-first",
        "second-image": "session-second",
    }


def test_scope_service_outage_leaves_cursor_recoverable(image_capture_module, monkeypatch, tmp_path):
    config, outbox, _, settings, path, _, requests = _setup(image_capture_module, monkeypatch, tmp_path)
    watcher = importlib.import_module("image_desktop_watch")
    _index(config, path, tmp_path, time())
    _append(path, "turn_context", {"turn_id": "first"})
    _message(path, "image", [b"\x89PNG\r\n\x1a\nfirst"])

    def unavailable(*args, **kwargs):
        raise watcher.ScopeBindingError

    monkeypatch.setattr(watcher, "resolve_scope_id", unavailable)
    assert watcher.poll(outbox, settings, began=time() - 10) == "image_scope_unavailable"
    assert not requests
    monkeypatch.setattr(watcher, "resolve_scope_id", lambda *args, **kwargs: "recovered-scope")
    watcher.poll(outbox, settings, began=time() - 10)
    assert len(requests) == 1 and requests[0]["scope_id"] == "recovered-scope"


def test_manager_will_not_replace_foreign_registration(image_capture_module, monkeypatch, tmp_path):
    service = importlib.import_module("collector_service")
    path = tmp_path / "another-owner.service"
    path.write_bytes(b"another owner's service")
    monkeypatch.setattr(service, "_artifact", lambda: path)
    monkeypatch.setattr(service.sys, "platform", "linux")
    with pytest.raises(ValueError, match="image_collector_registration_foreign"):
        service.install()
    assert path.read_bytes() == b"another owner's service"


def test_invalid_native_plugin_table_disables_capture(image_capture_module, monkeypatch, tmp_path):
    config = importlib.import_module("image_capture_config")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(
        config, "__file__", str(tmp_path / "plugins/cache/local/powercontext/1.1.0/image_capture_config.py")
    )
    (tmp_path / "config.toml").write_text('plugins = ["invalid"]', encoding="utf-8")
    assert config.plugin_enabled() is False


def test_supervisor_recovers_when_watcher_ignores_graceful_stop(image_capture_module, monkeypatch, tmp_path):
    """A stalled watcher must not take down the Windows Startup supervisor."""
    manager = importlib.import_module("collector_service")
    watcher = importlib.import_module("image_desktop_watch")
    children = []
    clock = {"now": 1000.0}

    class Child:
        def __init__(self, stubborn):
            self.stubborn = stubborn
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            if not self.stubborn:
                self.running = False

        def wait(self, timeout):
            if self.running:
                raise subprocess.TimeoutExpired("watcher", timeout)
            return 0

        def kill(self):
            self.running = False

    def start(*args, **kwargs):
        child = Child(stubborn=not children)
        children.append(child)
        return child

    def advance(seconds):
        clock["now"] += max(100, seconds)

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["collector_service.py", "run", str(tmp_path), str(tmp_path)])
    monkeypatch.setattr(manager, "any_enabled", lambda: len(children) < 2)
    monkeypatch.setattr(manager, "plugin_enabled", lambda: True)
    monkeypatch.setattr(manager.subprocess, "Popen", start)
    monkeypatch.setattr(manager, "time", lambda: clock["now"])
    monkeypatch.setattr(manager, "sleep", advance)
    monkeypatch.setattr(watcher, "watch_status", lambda *args: {"updated_at": 0})
    manager.run()
    assert len(children) == 2 and all(not child.running for child in children)


def test_image_consent_does_not_override_ordinary_scope_settings(image_capture_module, monkeypatch, tmp_path):
    _setup(image_capture_module, monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    restored = image_capture_module.CodexPluginSettings()
    assert restored.capture_images and restored.scope_id is None
    monkeypatch.setenv("POWERCONTEXT_CODEX_SCOPE_ID", "explicit-session-scope")
    assert image_capture_module.CodexPluginSettings().scope_id == "explicit-session-scope"


@pytest.mark.parametrize("previously_enabled", [False, True])
def test_failed_setup_restores_existing_consent(image_capture_module, monkeypatch, tmp_path, previously_enabled):
    config, _, _, settings, _, _, _ = _setup(image_capture_module, monkeypatch, tmp_path)
    service = importlib.import_module("collector_service")
    scope = importlib.import_module("scope_binding")
    config.write_profile(tmp_path, server_url=settings.server_url, scope_id="original", enabled=previously_enabled)
    before = config.load_config()
    monkeypatch.setattr(scope, "bind_scope", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        scope,
        "_post_json",
        lambda *args, **kwargs: {
            "media": dict.fromkeys(("enabled", "available", "vision_configured", "processing_enabled"), True)
        },
    )

    def failed_install():
        raise ValueError("image_collector_manager_unavailable")

    monkeypatch.setattr(service, "install", failed_install)
    with pytest.raises(ValueError, match="image_collector_manager_unavailable"):
        config.configure_capture("setup", str(tmp_path), "replacement", settings)
    assert config.load_config() == before


@pytest.mark.parametrize("failure_at", ["consent", "settings", "spawn"])
def test_supervisor_recovers_after_transient_startup_failure(image_capture_module, monkeypatch, tmp_path, failure_at):
    manager = importlib.import_module("collector_service")
    settings_module = importlib.import_module("settings")
    settings_type = settings_module.CodexPluginSettings
    failed = False
    launched = False
    pauses = []

    def fail_once(boundary):
        nonlocal failed
        if boundary == failure_at and not failed:
            failed = True
            raise OSError("injected")

    def consent():
        fail_once("consent")
        return not launched

    def settings():
        fail_once("settings")
        return settings_type()

    class Child:
        def poll(self):
            return 0

    def spawn(*args, **kwargs):
        nonlocal launched
        fail_once("spawn")
        launched = True
        return Child()

    def pause(seconds):
        pauses.append(seconds)
        assert len(pauses) < 5, "supervisor did not recover"

    monkeypatch.setattr(sys, "argv", ["collector_service.py", "run", str(tmp_path), str(tmp_path)])
    monkeypatch.setattr(manager, "any_enabled", consent)
    monkeypatch.setattr(manager, "plugin_enabled", lambda: True)
    monkeypatch.setattr(settings_module, "CodexPluginSettings", settings)
    monkeypatch.setattr(manager.subprocess, "Popen", spawn)
    monkeypatch.setattr(manager, "sleep", pause)
    manager.run()
    assert failed and launched and pauses
