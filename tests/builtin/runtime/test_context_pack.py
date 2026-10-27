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

from __future__ import annotations

import base64
import json

import pytest

from powercontext.builtin.runtime import ContextPack, ContextPackDelta, ContextPackError, apply_context_pack_delta


def test_pack_chunks_valid_utf8_within_byte_limit() -> None:
    content = "a海🙂b" * 300
    pack = ContextPack.from_content(content, chunk_bytes=7)
    assert b"".join(pack.items).decode("utf-8") == content
    assert all(0 < len(item) <= 7 for item in pack.items)
    assert all(item.decode("utf-8") for item in pack.items)
    assert ContextPack.from_bytes(pack.to_bytes()) == pack
    assert pack.byte_count == len(pack.to_bytes())


@pytest.mark.parametrize("chunk_bytes", [0, -1, True, 1.5])
def test_pack_rejects_invalid_chunk_limits(chunk_bytes: object) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content("text", chunk_bytes=chunk_bytes)  # type: ignore[arg-type]


def test_pack_rejects_duplicate_fields_and_noncanonical_base64() -> None:
    pack = ContextPack.build((b"a",))
    payload = json.loads(pack.to_bytes())
    payload["items"] = [base64.b64encode(b"a").decode("ascii").rstrip("=")]
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode())
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(pack.to_bytes()[:-1] + b',"schema":"duplicate"}')


def test_delta_round_trip_and_rejects_out_of_range_prefix() -> None:
    base = ContextPack.build((b"first", b"second"))
    target = ContextPack.build((b"first", b"second", b"third"))
    delta = ContextPackDelta.between(base, target)
    restored_delta = ContextPackDelta.from_bytes(delta.to_bytes())
    assert apply_context_pack_delta(ContextPack.from_bytes(base.to_bytes()), restored_delta) == target
    assert delta.byte_count == len(delta.to_bytes())

    malformed = json.loads(delta.to_bytes())
    malformed["prefix_items"] = len(base.items) + 1
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, ContextPackDelta.from_bytes(json.dumps(malformed).encode()))
