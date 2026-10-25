# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Scoped HTTP image adapters and bounded pre-JSON transport."""

import base64
import json
from functools import partial

from fastapi import Request
from pydantic import ValidationError
from starlette._utils import get_route_path
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from powercontext.builtin.images.application import ImageApplication
from powercontext.builtin.images.validation import MAX_ENCODED_IMAGE_BYTES
from powercontext.builtin.media.errors import MediaError
from powercontext.builtin.media.models import ImageAnalysisRequest, ImageCaptureInput
from powercontext.http._generated.models import (
    CaptureImageRequest,
    GetMediaAssetRequest,
    GetMediaAssetResponse,
    GetMediaRequestRequest,
    ListMediaRequestsRequest,
    MediaCaptureReceipt,
    MediaProcessingRequest,
    MediaRequestPage,
    RequestImageAnalysisRequest,
)
from powercontext.server.authz import AccessAction, ResourceRef, access_control_for_mode
from powercontext.server.context import access_audit_context, current_principal

MAX_IMAGE_BODY_BYTES = 12 * 1024 * 1024
CAPTURE_PATH = "/v1/images/capture"


def _encoded_budget_exceeded(body: bytearray) -> bool:
    if len(body) <= MAX_ENCODED_IMAGE_BYTES:
        return False
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return False
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("bytes_base64"), str)
        and len(payload["bytes_base64"]) > MAX_ENCODED_IMAGE_BYTES
    )


class ImageBodyLimitMiddleware:
    """Reject an oversized capture body before authorization or JSON decoding."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or get_route_path(scope) != CAPTURE_PATH:
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_IMAGE_BODY_BYTES:
                response = JSONResponse(
                    {
                        "error": {
                            "code": "image_too_large",
                            "message": "Image request body exceeds 12 MiB.",
                            "details": None,
                        }
                    },
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        # Only a body near the transport ceiling can contain an overlong
        # Base64 field. Normalize that byte-budget failure before DTO/Auth
        # validators would classify it as an ordinary shape error.
        if _encoded_budget_exceeded(body):
            response = JSONResponse(
                {"error": {"code": "image_too_large", "message": "Image exceeds 8 MiB.", "details": None}},
                status_code=413,
            )
            await response(scope, receive, send)
            return
        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, bounded_receive, send)


def _images(request: Request) -> ImageApplication:
    service = getattr(request.app.state.application, "images", None)
    if not isinstance(service, ImageApplication):
        raise MediaError(503, "media_storage_unavailable")
    return service


async def _authorize_write(connection, *, request, scope_id, original=None):
    access = access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode)
    if access is None:
        return
    bound = access.with_connection(connection)
    principal = current_principal()
    context = access_audit_context("image_write")
    resource = ResourceRef.scope(scope_id)
    await bound.require(principal, AccessAction.SCOPE_CONTRIBUTE, resource, context=context)
    await bound.require(principal, AccessAction.SCOPE_READ, resource, context=context)
    if original is not None:
        head = await _images(request).analysis_head(connection, scope_id, original)
        if head is not None:
            await bound.require(
                principal,
                AccessAction.ARTIFACT_WRITE,
                ResourceRef.artifact(scope_id, family="image-analysis", artifact_id=head.artifact_id),
                context=context,
            )


async def capture_image(request: CaptureImageRequest, http_request: Request):
    try:
        command = ImageCaptureInput.model_validate(request.model_dump(mode="json"))
    except ValidationError as error:
        raise MediaError(422, "image_invalid_input") from error
    value = await _images(http_request).capture(
        command, authorize_transaction=partial(_authorize_write, request=http_request, scope_id=command.scope_id)
    )
    return MediaCaptureReceipt.model_validate(value.model_dump(mode="json", by_alias=True))


async def get_media_asset(request: GetMediaAssetRequest, http_request: Request) -> GetMediaAssetResponse:
    asset, data = await _images(http_request).get_asset(request.scope_id, request.asset_id)
    return GetMediaAssetResponse.model_validate({
        "asset": asset.model_dump(mode="json", by_alias=True),
        "bytes_base64": base64.b64encode(data).decode("ascii"),
    })


async def get_media_request(request: GetMediaRequestRequest, http_request: Request):
    value = await _images(http_request).get_request(request.scope_id, request.request_id)
    return MediaProcessingRequest.model_validate(value.model_dump(mode="json", by_alias=True))


async def list_media_requests(request: ListMediaRequestsRequest, http_request: Request):
    value = await _images(http_request).list_requests(
        request.scope_id,
        processing_kind=request.processing_kind,
        states=request.states or (),
        limit=request.limit,
        cursor=request.cursor,
    )
    return MediaRequestPage.model_validate(value.model_dump(mode="json", by_alias=True))


async def request_image_analysis(request: RequestImageAnalysisRequest, http_request: Request):
    try:
        command = ImageAnalysisRequest.model_validate(request.model_dump(mode="json"))
    except ValidationError as error:
        raise MediaError(422, "image_invalid_preconditions") from error
    value = await _images(http_request).request_analysis(
        command,
        authorize_transaction=partial(
            _authorize_write, request=http_request, scope_id=command.scope_id, original=command.original
        ),
    )
    return MediaProcessingRequest.model_validate(value.model_dump(mode="json", by_alias=True))
