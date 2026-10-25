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

"""Invalid durable consent produces a doctor finding instead of a traceback."""

import json
import os

import pytest

from powercontext.cli.codex_images import image_diagnostic
from powercontext.cli.system import DiagnosticStatus


@pytest.mark.parametrize(
    "value", [None, [], "invalid", 1, {"version": True, "workspaces": {}}, {"version": 1, "workspaces": []}]
)
def test_doctor_reports_invalid_image_consent(value, monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "powercontext/image-capture-config.json"
    path.parent.mkdir()
    path.write_text(json.dumps(value))
    result = image_diagnostic({})
    assert result.status == DiagnosticStatus.FAILED and "invalid" in result.detail


def test_doctor_reports_invalid_workspace_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "powercontext/image-capture-config.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"version": 1, "workspaces": {os.path.normcase(str(tmp_path.resolve())): []}}))
    result = image_diagnostic({})
    assert result.status == DiagnosticStatus.FAILED and "invalid" in result.detail
