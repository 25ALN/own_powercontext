# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ruff: noqa: RUF001 -- Localized Chinese UI punctuation.

"""Optional image evidence configuration in the ordinary installation wizard."""

from __future__ import annotations

import json

from sqlalchemy.engine import make_url

from powercontext.builtin.images.config import validate_vision_endpoint
from powercontext.cli.config_wizard_ui import WizardUI

SERVER = "POWERCONTEXT_SERVER_"
VISION = SERVER + "INFERENCE_VISION_"


def collect_images(ui: WizardUI, values: dict[str, str]) -> dict[str, str | None]:
    """No inference, storage creation or host installation occurs here."""
    ui.section("Experimental image evidence", "实验性图片证据")
    ui.say(
        "PNG/JPEG originals are saved, then parsed in the background. Memory generation is a separate result. "
        "This optional feature supports file SQLite and verified Codex CLI/Desktop versions.",
        "PNG／JPEG 原图先保存，再后台解析；记忆生成是另一项结果。可选图片功能支持文件 SQLite 和已验证的 "
        "Codex CLI／Desktop 版本。",
    )
    url = make_url(values.get(SERVER + "DATABASE_URL", "sqlite+aiosqlite:///:memory:"))
    if values.get(SERVER + "DATABASE_KIND", "sqlite") != "sqlite":
        ui.say("Image evidence currently requires file SQLite.", "图片证据当前只支持文件 SQLite。")
        return {SERVER + "IMAGES_ENABLED": "false", "POWERCONTEXT_CODEX_CAPTURE_IMAGES": "false"}
    if not ui.confirm(
        "Enable the image experiment?", "启用图片实验？", default=values.get(SERVER + "IMAGES_ENABLED") == "true"
    ):
        return {SERVER + "IMAGES_ENABLED": "false", "POWERCONTEXT_CODEX_CAPTURE_IMAGES": "false"}
    if not url.database or url.database == ":memory:":
        raise ValueError("Image experiment requires a file SQLite database")  # noqa: TRY003
    ui.say(
        "Original bytes are retained in SQLite. Install powercontext[server,images]; an existing database needs the media migration.",
        "原图字节保存在 SQLite。请安装 powercontext[server,images]；已有数据库需先执行 media 迁移。",
    )
    reuse = False
    if values.get(SERVER + "INFERENCE_GENERATION_MODEL", "").startswith("openai-chat:") and values.get(
        SERVER + "INFERENCE_GENERATION_BASE_URL"
    ):
        reuse = (
            ui.choose(
                "Image recognition connection",
                "图片识别模型连接",
                [
                    ("reuse", "Reuse generation (must support image input)", "复用普通生成模型（必须支持图片输入）"),
                    ("dedicated", "Configure a separate Vision model", "单独配置 Vision 模型"),
                ],
                default="reuse" if values.get(VISION + "USE_GENERATION") == "true" else "dedicated",
            )
            == "reuse"
        )
    if reuse:
        ui.say(
            "Endpoint, model and credentials are reused. The Codex chat model is separate. "
            "Use image probe after saving to verify real image input; model names do not establish capability.",
            "复用 PowerContext 普通生成模型的地址、模型与凭据，Codex 聊天模型仍是另一套配置。保存后用 image probe "
            "验证真实图片输入；不会根据模型名猜测能力。",
        )
        endpoint = values[SERVER + "INFERENCE_GENERATION_BASE_URL"]
        model = values[SERVER + "INFERENCE_GENERATION_MODEL"].removeprefix("openai-chat:")
        connection = {VISION + "USE_GENERATION": "true"}
    else:
        connection, endpoint, model = _dedicated(ui, values)
    automatic = ui.confirm(
        "Authorize image capture for this workspace during Codex setup?",
        "在 Codex setup 时授权采集当前项目的图片？",
        default=values.get("POWERCONTEXT_CODEX_CAPTURE_IMAGES") == "true",
    )
    ui.say(
        f"Originals are stored in SQLite. Vision: {model} at {endpoint}. No model request has been made. "
        "Codex setup manages the collector; review and trust new hooks once. Unknown host versions are rejected.",
        f"原图保存在 SQLite。Vision：{model}，地址 {endpoint}。尚未调用模型。Codex setup 会管理采集器，首次需审阅信任 "
        "新的 Hooks；未知宿主版本明确拒绝。",
    )
    return {
        SERVER + "IMAGES_ENABLED": "true",
        SERVER + "IMAGES_ASSET_DIRECTORY": None,
        **connection,
        VISION + "TIMEOUT_SECONDS": values.get(VISION + "TIMEOUT_SECONDS", "120"),
        VISION + "MAX_ATTEMPTS": values.get(VISION + "MAX_ATTEMPTS", "3"),
        VISION + "MAX_OUTPUT_TOKENS": values.get(VISION + "MAX_OUTPUT_TOKENS", "4096"),
        "POWERCONTEXT_CODEX_CAPTURE_IMAGES": str(automatic).lower(),
    }


def _dedicated(ui: WizardUI, values: dict[str, str]) -> tuple[dict[str, str], str, str]:
    while True:
        endpoint = ui.ask(
            "Vision base URL",
            "Vision 服务地址",
            default=values.get(VISION + "BASE_URL", ""),
            required=True,
        )
        try:
            validate_vision_endpoint(endpoint)
        except ValueError:
            ui.say(
                "Use HTTPS, or HTTP on loopback; omit credentials, query and fragment.",
                "使用 HTTPS 或本机 HTTP，不要在 URL 中放凭据、查询和片段。",
            )
        else:
            break
    model = ui.ask(
        "Exact Vision model ID",
        "精确 Vision 模型 ID",
        default=values.get(VISION + "MODEL", ""),
        required=True,
    )
    deployment = ui.ask(
        "Deployment version label",
        "部署版本标识",
        default=values.get(VISION + "DEPLOYMENT_ID", "vision:" + model),
        required=True,
    )
    headers = values.get(VISION + "HEADERS", "")
    if not headers or ui.confirm("Replace the stored Vision headers?", "替换已有 Vision 鉴权？", default=False):
        key = ui.ask(
            "Vision API key (hidden; blank for a local service)",
            "Vision API Key（隐藏输入；本机服务可留空）",
            secret=True,
        )
        headers = json.dumps({"Authorization": "Bearer " + key} if key else {})
    return (
        {
            VISION + "USE_GENERATION": "false",
            VISION + "BASE_URL": endpoint,
            VISION + "MODEL": model,
            VISION + "DEPLOYMENT_ID": deployment,
            VISION + "HEADERS": headers,
        },
        endpoint,
        model,
    )
