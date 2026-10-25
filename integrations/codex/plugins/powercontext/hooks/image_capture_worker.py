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

"""Client-side companion: poll registered live turns and deliver retained bytes."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from time import monotonic, sleep
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from diagnostics import _locked  # noqa: E402
from image_attachments import scan  # noqa: E402
from image_capture_config import enabled_profile, workspace_disabled  # noqa: E402
from image_outbox import Outbox, outbox_path  # noqa: E402
from scope_binding import ScopeBindingStatusError, _post_json  # noqa: E402
from settings import CodexPluginSettings  # noqa: E402


def _permitted(job: dict[str, Any], settings: CodexPluginSettings, *, reading: bool = False) -> bool:
    if workspace_disabled(job["workspace"]):
        return False
    if os.environ.get("POWERCONTEXT_CODEX_CAPTURE_IMAGES", "").lower() in {"false", "0", "no", "off"}:
        return False
    if settings.server_url != job["server_url"]:
        return False
    profile = enabled_profile(job["workspace"], settings.server_url)
    if job["consent"] == "profile":
        return profile is not None and (not reading or profile.get("epoch") == job.get("epoch"))
    # A saved profile's explicit disable takes precedence over inherited opt-in.
    return settings.capture_images


def _read_jobs(outbox: Outbox, settings: CodexPluginSettings) -> bool:
    progressed = False
    for row in outbox.jobs():
        try:
            job = json.loads(row["payload"])
            if _permitted(job, settings, reading=True):
                progressed |= scan(row, outbox)
            elif job["consent"] == "profile" and _permitted(job, settings):
                # Keep already staged deliveries, but retire the old live cursor
                # when consent was renewed; off-period messages are not backfilled.
                outbox.finish(row["id"], state="done", error="image_consent_changed")
        except (ValueError, TypeError, KeyError, OSError, AttributeError, RecursionError) as error:
            code = str(error) if str(error).startswith("image_") and len(str(error)) < 80 else "image_read_unavailable"
            if code == "image_outbox_full":
                # advance() rolled back the unread record and its cursor. Yield
                # to delivery before trying to stage any more retained bytes.
                outbox.finish(row["id"], state="watching", error=code)
                break
            outbox.finish(row["id"], state="error", error=code)
    return progressed


def _upload(row: dict[str, Any], settings: CodexPluginSettings) -> str:
    request = json.loads(row["request"])
    request["bytes_base64"] = base64.b64encode(row["bytes"]).decode("ascii")
    response = _post_json("/v1/images/capture", request, settings=settings, deadline=monotonic() + 6)
    task = response.get("request_id")
    if response.get("status") != "accepted" or not isinstance(task, str) or not task.startswith("mr_"):
        raise ValueError("image_acceptance_invalid")
    return task


def _deliver(outbox: Outbox, settings: CodexPluginSettings) -> bool:
    progressed = False
    for row in outbox.pending(permitted=lambda job: _permitted(job, settings)):
        try:
            if not _permitted(json.loads(row["job"]), settings):
                continue
            outbox.delivered(row["id"], _upload(row, settings))
            progressed = True
        except ScopeBindingStatusError as error:
            outbox.retry(
                row["id"],
                error="image_upload_http_" + str(error.status),
                permanent=error.status in {400, 409, 413, 415, 422},
            )
        except Exception:
            outbox.retry(row["id"], error="image_upload_unavailable")
    return progressed


def drain(settings: CodexPluginSettings, *, wait_seconds: float = 10) -> None:
    outbox = Outbox(outbox_path())
    # The OS lock is released on death. A queued launch waits for the current
    # companion, covering registration concurrent with its final scan.
    with ExitStack() as stack:
        lock_deadline = monotonic() + 30
        while True:
            try:
                stack.enter_context(_locked(outbox.path.with_suffix(".worker.lock")))
                break
            except OSError:
                if monotonic() >= lock_deadline:
                    return
                sleep(0.25)
        deadline = monotonic() + wait_seconds
        hard_deadline = monotonic() + 60
        while True:
            progressed = _read_jobs(outbox, settings)
            progressed |= _deliver(outbox, settings)
            if monotonic() >= min(deadline, hard_deadline):
                return
            if progressed:
                deadline = max(deadline, monotonic() + 2)
            sleep(0.25)


def launch() -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(  # noqa: S603 - fixed packaged worker and interpreter, never payload shell text.
        [sys.executable, str(Path(__file__).resolve())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
        start_new_session=os.name != "nt",
    )


if __name__ == "__main__":
    try:
        drain(CodexPluginSettings())
    except Exception:
        raise SystemExit(0) from None
