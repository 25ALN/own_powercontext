# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Image views composed from authorized common Source, Artifact and media reads."""

from powercontext.server.dashboard.api import segment
from powercontext.server.dashboard.errors import ReadError


async def original_record(api, scope, ref):
    return await api.read(
        f"/v1/scopes/{segment(scope)}/sources/{segment(ref['source_type'])}/{segment(ref['source_id'])}"
    )


async def image_request_view(api, scope, request_id):
    request = await api.read("/v1/media/requests/get", {"scope_id": scope, "request_id": request_id})
    original = await original_record(api, scope, request["original"]["source"])
    analysis = None
    if request["result"] is not None:
        ref = request["result"]["artifact"]
        analysis = (await api.artifact_revision(scope, ref["family"], ref["artifact_id"], ref["revision"]))["content"]
    return {
        **request,
        "task_id": request["request_id"],
        "analysis": request["publication"],
        "original_payload": original["content"],
        "analysis_payload": analysis,
    }


async def image_evidence_view(api, scope, record):
    value = record["content"]
    if record["source_type"] in {"image.interpretation", "experimental.image.analysis"}:
        original = await original_record(api, scope, value["original"]["source"])
        if record["source_type"] == "image.interpretation":
            ref = value["result"]["artifact"]
            analysis = (await api.artifact_revision(scope, ref["family"], ref["artifact_id"], ref["revision"]))[
                "content"
            ]
        else:
            analysis = value
        return {
            "task_id": "",
            "state": "succeeded",
            "error_code": None,
            "attempt_count": 0,
            "max_attempts": 0,
            "profile_digest": analysis["profile_digest"],
            "original": value["original"],
            "analysis": {"source": {"source_id": record["source_id"]}},
            "original_payload": original["content"],
            "analysis_payload": analysis,
        }
    if record["source_type"] != "image.original":
        raise ReadError(422, "image_original_required")
    return {
        "task_id": "",
        "state": "pending",
        "error_code": None,
        "attempt_count": 0,
        "max_attempts": 0,
        "profile_digest": None,
        "original": {"source": {"source_id": record["source_id"]}},
        "analysis": None,
        "original_payload": value,
        "analysis_payload": None,
    }


async def image_request_item(api, scope, request):
    original = (await original_record(api, scope, request["original"]["source"]))["content"]
    asset = original["asset"]
    return {
        "task": {**request, "task_id": request["request_id"]},
        "asset": {
            **asset,
            "width": asset["metadata"]["width"],
            "height": asset["metadata"]["height"],
            "byte_length": asset["byte_size"],
        },
        "origin": original["origin"],
        "created_at": request["created_at"],
    }
