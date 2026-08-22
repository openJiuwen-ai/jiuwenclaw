# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import patch

from jiuwenclaw import interface_resp as mod


def test_rid_skips_context_lookup_when_header_or_fallback_present() -> None:
    with patch.object(mod, "get_interface_request_id") as lookup:
        assert mod._rid("hdr-rid", "fb-rid") == "hdr-rid"
        assert mod._rid(None, "fb-rid") == "fb-rid"
        lookup.assert_not_called()


def test_rid_uses_context_lookup_only_when_both_empty() -> None:
    with patch.object(mod, "get_interface_request_id", return_value="ctx-rid") as lookup:
        assert mod._rid(None, None) == "ctx-rid"
        lookup.assert_called_once()
