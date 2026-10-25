# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Optional setup keeps image and ordinary inference roles independent."""

import json
from pathlib import Path

from typer.testing import CliRunner

from powercontext.cli.config import app
from powercontext.cli.env_file import parse_environment


def _existing(tmp_path) -> Path:
    output = tmp_path / "server.env"
    output.write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nPOWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:///{tmp_path / 'data.db'}\nPOWERCONTEXT_SERVER_MCP_ENABLED=false\nCUSTOM_FLAG=keep\n",
        encoding="utf-8",
    )
    return output


def test_existing_config_can_skip_experiment_without_model_fields(tmp_path):
    output = _existing(tmp_path)
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--experimental-images", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'data.db'}\nreuse\nn\ny\n",
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_SERVER_IMAGES_ENABLED"] == "false"
    assert values["POWERCONTEXT_CODEX_CAPTURE_IMAGES"] == "false" and values["CUSTOM_FLAG"] == "keep"
    assert not any("INFERENCE_VISION_" in name for name in values)


def test_opt_in_wizard_saves_separate_vision_connection_and_explicit_probe_instructions(tmp_path):
    output = _existing(tmp_path)
    secret = "local-test-secret"  # noqa: S105 - synthetic credential verifies redaction
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--experimental-images", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'data.db'}\nreuse\ny\nhttps://vision.example.invalid/v1\nprivate-minicpm\ndeploy-one\n{secret}\ny\ny\n",
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert (
        values["POWERCONTEXT_SERVER_IMAGES_ENABLED"] == "true" and values["POWERCONTEXT_CODEX_CAPTURE_IMAGES"] == "true"
    )
    assert values["POWERCONTEXT_SERVER_INFERENCE_VISION_MODEL"] == "private-minicpm"
    assert json.loads(values["POWERCONTEXT_SERVER_INFERENCE_VISION_HEADERS"])["Authorization"] == "Bearer " + secret
    assert not any("INFERENCE_GENERATION_" in name for name in values)
    assert secret not in result.output and not (tmp_path / "assets").exists()
    instructions = output.with_name(output.name + ".next-steps.md").read_text()
    assert "image probe --env-file" in instructions and "one billable request" in instructions
    assert "0.141.0" in instructions and "trust" in instructions


def test_ordinary_wizard_offers_image_opt_in_without_experiment_flag(tmp_path):
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'data.db'}\nlocal\nbase\nn\n\ncodex\ndefault\nnone\ny\n"
            f"https://vision.example.invalid/v1\nvision-test\nv1\nsynthetic-secret\ny\ny\n"
        ),
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_SERVER_IMAGES_ENABLED"] == "true"
    assert values["POWERCONTEXT_CODEX_CAPTURE_IMAGES"] == "true"
    assert "synthetic-secret" not in result.output
    assert not (tmp_path / "assets").exists()


def test_images_module_reuses_generation_without_extra_key_or_changing_processing(tmp_path):
    output = _existing(tmp_path)
    with output.open("a", encoding="utf-8") as file:
        file.write('POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS=\'{"Authorization":"Bearer synthetic-key"}\'\n')
        file.write(
            'POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-chat:multimodal\nPOWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL=https://model.example.invalid/v1\nPOWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES=\'["memory","topic-memory"]\'\n'
        )
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(f"sqlite\n{tmp_path / 'data.db'}\nedit\nimages\ny\nreuse\ny\ndone\ny\n"),
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_SERVER_INFERENCE_VISION_USE_GENERATION"] == "true"
    assert "POWERCONTEXT_SERVER_INFERENCE_VISION_HEADERS" not in values
    assert "synthetic-key" not in result.output
    assert values["POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES"] == '["memory","topic-memory"]'
