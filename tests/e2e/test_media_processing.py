# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Actual Supervisor spawn and HTTP Vision with exact Artifact/Source publication."""

import asyncio
import json
import threading
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic

from powercontext.builtin.images.identity import image_profile_digest
from powercontext.builtin.media.models import ImageAnalysisRequest
from powercontext.builtin.runtime import open_builtin_runtime

from .test_media_storage import capture_input, config_for, images_for, picture, profile_for, scope_for


@contextmanager
def vision_endpoint(*, hold_first=False):
    calls = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args):  # noqa: A002 - standard library override

            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(data)
            index = len(calls)
            if hold_first and index == 1:
                release.wait(20)
            output = {
                "readability": "readable",
                "ocr_text": f"Deployment limit is {100 + index}.",
                "visual_description": "A deployment note.",
                "entities": [],
                "relations": [],
                "uncertainties": [],
            }
            body = json.dumps({
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 12},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def wait_request(runtime, scope, request_id, state="succeeded", *, wait_seconds=45):
    last = "not observed"
    deadline = monotonic() + wait_seconds
    while monotonic() < deadline:
        value = await images_for(runtime).get_request(scope, request_id)
        last = value.model_dump_json()
        if value.state.value == state:
            return value
        if value.state.value == "failed":
            raise AssertionError(value.model_dump_json())
        await asyncio.sleep(0.05)
    raise AssertionError(last)


def test_actual_spawn_publishes_exact_artifact_and_reading_never_calls_vision(tmp_path):
    with vision_endpoint() as (endpoint, calls, _):

        async def scenario():
            config = config_for(tmp_path / "spawn.db")
            config.inference.vision_base_url = endpoint
            config.runtime.artifact_processing_families = ("image-analysis",)
            async with open_builtin_runtime(config) as runtime:
                scope = await scope_for(runtime)
                receipt = await images_for(runtime).capture(capture_input(scope.scope_id))
                first = await wait_request(runtime, scope.scope_id, receipt.request_id)
                artifact = await runtime.records.for_scope(scope.scope_id).get_artifact_revision(
                    "image-analysis", first.result.artifact.artifact_id, 1
                )
                assert artifact is not None
                assert first.result.artifact.revision == 1 and first.attempt_count == 1
                assert first.attempts[0].provider_requests == 1
                # The child made one real HTTP request containing an image input, without a generation model.
                assert len(calls) == 1 and any(
                    item.get("type") == "image_url" for item in calls[0]["messages"][0]["content"]
                )
                command = ImageAnalysisRequest(
                    scope_id=scope.scope_id,
                    operation="reparse",
                    idempotency_key="again",
                    original=receipt.original.source,
                    expected_artifact_revision=1,
                    expected_profile_digest=image_profile_digest(profile_for(runtime)),
                )
                second_request = await images_for(runtime).request_analysis(command)
                second = await wait_request(runtime, scope.scope_id, second_request.request_id)
                assert second.result.artifact.artifact_id == first.result.artifact.artifact_id
                assert second.result.artifact.revision == 2
                assert second.publication != first.publication
                assert (await images_for(runtime).get_asset(scope.scope_id, receipt.asset.asset_id))[1] == picture()
                assert (await images_for(runtime).get_request(scope.scope_id, first.request_id)).result == first.result
                assert len(calls) == 2

        asyncio.run(scenario())


def test_exact_replacement_invalidates_a_running_claim_before_its_response(tmp_path):
    with vision_endpoint(hold_first=True) as (endpoint, calls, release):

        async def scenario():
            config = config_for(tmp_path / "supersede.db")
            config.inference.vision_base_url = endpoint
            config.inference.vision_timeout_seconds = 30
            config.runtime.artifact_processing_families = ("image-analysis",)
            async with open_builtin_runtime(config) as runtime:
                scope = await scope_for(runtime)
                receipt = await images_for(runtime).capture(capture_input(scope.scope_id))
                deadline = monotonic() + 30
                while not calls and monotonic() < deadline:  # noqa: ASYNC110 - poll an external HTTP fixture
                    await asyncio.sleep(0.05)
                assert len(calls) == 1
                current = await images_for(runtime).get_request(scope.scope_id, receipt.request_id)
                replacement = await images_for(runtime).request_analysis(
                    ImageAnalysisRequest(
                        scope_id=scope.scope_id,
                        operation="reparse",
                        idempotency_key="replace-running",
                        original=receipt.original.source,
                        request_id=current.request_id,
                        expected_state_version=current.state_version,
                        expected_artifact_revision=0,
                        expected_profile_digest=image_profile_digest(profile_for(runtime)),
                    )
                )
                release.set()
                result = await wait_request(runtime, scope.scope_id, replacement.request_id)
                old = await images_for(runtime).get_request(scope.scope_id, current.request_id)
                assert old.state.value == "superseded" and old.attempt_count == 1
                assert old.result is None and old.publication is None
                assert result.result.artifact.revision == 1
                assert len(calls) == 2

        asyncio.run(scenario())


def write_worker_pid(path):
    import os

    path.write_text(str(os.getpid()), encoding="utf-8")


def test_actual_child_death_spends_the_unknown_attempt_and_recovers_exactly_once(tmp_path):
    import os
    import signal
    from functools import partial

    with vision_endpoint(hold_first=True) as (endpoint, calls, release):

        async def scenario():
            config = config_for(tmp_path / "crash.db")
            config.runtime.artifact_processing_families = ("image-analysis",)
            config.inference.vision_base_url = endpoint
            config.inference.vision_timeout_seconds = 5
            pid_file = tmp_path / "child.pid"
            async with open_builtin_runtime(config, worker_initializer=partial(write_worker_pid, pid_file)) as runtime:
                scope = (await scope_for(runtime)).scope_id
                receipt = await images_for(runtime).capture(capture_input(scope))
                async with asyncio.timeout(40):
                    while not calls:  # noqa: ASYNC110 - external child/HTTP fixture
                        await asyncio.sleep(0.05)
                pid = int(pid_file.read_text())
                assert pid > 0 and pid != os.getpid()
                os.kill(pid, signal.SIGTERM)
                release.set()
                result = await wait_request(runtime, scope, receipt.request_id, wait_seconds=90)
                assert result.attempt_count == 2 and result.result.artifact.revision == 1
                assert result.attempts[0].state == "interrupted"
                assert result.attempts[0].provider_requests is None
                assert result.attempts[1].provider_requests == 1 and len(calls) == 2

        asyncio.run(scenario())


def test_supervisor_retries_rate_limit_within_the_persisted_attempt_budget(tmp_path):
    from .media_support import Backend, _capture, _listen, _picture, _request, _running, _scope, _terminal

    async def scenario():
        backend = Backend({"status": 429}, {"status": 429}, {})
        async with _listen(backend.app) as endpoint, _running(tmp_path, endpoint + "/v1") as (_, http):
            scope = await _scope(http)
            receipt = await _capture(http, _request(scope, "rate-limited", _picture()))
            result = await _terminal(http, scope, receipt["request_id"], deadline=90)
            assert result["state"] == "succeeded" and result["attempt_count"] == 3
            assert len(backend.requests) == 3
            assert [attempt["provider_requests"] for attempt in result["attempts"]] == [1, 1, 1]

    asyncio.run(scenario())


def test_explicit_retry_keeps_the_failed_profile_and_operation_replay_receipt(tmp_path):
    from .media_support import Backend, _capture, _listen, _picture, _request, _running, _scope, _terminal

    async def scenario():
        backend = Backend({"status": 403}, {})
        async with _listen(backend.app) as endpoint, _running(tmp_path, endpoint + "/v1") as (_, http):
            scope = await _scope(http)
            receipt = await _capture(http, _request(scope, "explicit-retry", _picture()))
            failed = await _terminal(http, scope, receipt["request_id"])
            assert failed["state"] == "failed" and failed["attempt_count"] == 1
            command = {
                "scope_id": scope,
                "operation": "retry",
                "idempotency_key": "retry-once",
                "original": receipt["original"]["source"],
                "request_id": failed["request_id"],
                "expected_state_version": failed["state_version"],
                "expected_artifact_revision": 0,
                "expected_profile_digest": failed["profile_digest"],
            }
            response = await http.post("/v1/images/analysis/request", json=command)
            assert response.status_code == 202, response.text
            initial = response.json()
            succeeded = await _terminal(http, scope, initial["request_id"])
            assert succeeded["state"] == "succeeded" and succeeded["profile_digest"] == failed["profile_digest"]
            replay = await http.post("/v1/images/analysis/request", json=command)
            assert replay.status_code == 202 and replay.json() == initial
            assert (await _terminal(http, scope, failed["request_id"]))["state"] == "failed"
            assert len(backend.requests) == 2

    asyncio.run(scenario())
