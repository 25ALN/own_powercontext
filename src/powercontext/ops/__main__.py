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

"""One bounded JSON request per independent Ops process."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from powercontext.ops import protocol
from powercontext.ops.protocol import OpsError


def _emit(message: dict[str, Any]) -> None:
    payload = protocol.canonical(message)
    if len(payload) > protocol.MAX_MESSAGE_BYTES:
        raise OpsError("response_too_large")
    sys.stdout.buffer.write(payload + b"\n")
    sys.stdout.buffer.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicitly authorized local PowerContext service operations")
    parser.add_argument("--target-file", required=True, type=Path, help="Private operator-owned target binding")
    parser.add_argument(
        "--state-dir", required=True, type=Path, help="Stable private operation directory for this user"
    )
    args = parser.parse_args()
    request_id = None
    try:
        data = sys.stdin.buffer.readline(protocol.MAX_MESSAGE_BYTES + 1)
        if len(data) > protocol.MAX_MESSAGE_BYTES:
            raise OpsError("request_too_large")  # noqa: TRY301
        request = protocol.request(protocol.decode(data))
        request_id = request["request_id"]
        from powercontext.ops.runtime import Ops, TargetBinding

        target = TargetBinding.read(args.target_file)
        result = Ops(target, args.state_dir).handle(request, emit=_emit)
    except OpsError as error:
        result = {
            "protocol_version": 1,
            "request_id": request_id,
            "type": "result",
            "outcome": "rejected",
            "error": {"code": error.code},
        }
    except (OSError, ValueError):
        result = {
            "protocol_version": 1,
            "request_id": request_id,
            "type": "result",
            "outcome": "rejected",
            "error": {"code": "invalid_input_or_binding"},
        }
    _emit(result)
    return 0 if result.get("outcome") in {"succeeded", "pending"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
