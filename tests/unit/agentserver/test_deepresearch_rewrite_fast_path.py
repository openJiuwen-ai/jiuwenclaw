import json

import pytest

from jiuwenclaw.agentserver.deep_agent.deepresearch_rewrite_fast_path import (
    RewriteFastPathError,
    parse_rewrite_envelope,
)


def _payload(**overrides) -> dict:
    payload = {
        "report_path": "/workspace/report.md",
        "action": "polish",
        "selection": {
            "protocol_version": 2,
            "start_byte": 0,
            "end_byte": 9,
            "selected_text": "原句。",
            "source_sha256": "0" * 64,
        },
        "instruction": "",
    }
    payload.update(overrides)
    return payload


def _query(**overrides) -> str:
    body = json.dumps(_payload(**overrides), ensure_ascii=False)
    return f"<deepresearch_rewrite_request>{body}</deepresearch_rewrite_request>"


def test_parse_rewrite_envelope_accepts_exact_request():
    request = parse_rewrite_envelope(_query(action="expand"))

    assert request is not None
    assert request.report_path == "/workspace/report.md"
    assert request.action == "expand"
    assert request.instruction == ""
    assert request.selection["protocol_version"] == 2


def test_parse_rewrite_envelope_accepts_outer_whitespace():
    request = parse_rewrite_envelope(f"\n  {_query()}  \n")

    assert request is not None
    assert request.action == "polish"


def test_parse_rewrite_envelope_ignores_non_exact_wrapper():
    assert parse_rewrite_envelope("please " + _query()) is None


def test_parse_rewrite_envelope_ignores_plain_message():
    assert parse_rewrite_envelope("请润色这段文字") is None


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        json.dumps({**_payload(), "extra": "not allowed"}, ensure_ascii=False),
        json.dumps(
            {key: value for key, value in _payload().items() if key != "selection"},
            ensure_ascii=False,
        ),
        json.dumps(_payload(action="delete"), ensure_ascii=False),
        json.dumps(_payload(report_path=123), ensure_ascii=False),
        json.dumps(_payload(selection="raw text"), ensure_ascii=False),
        json.dumps(_payload(instruction=None), ensure_ascii=False),
    ],
)
def test_parse_rewrite_envelope_rejects_recognized_invalid_request(body):
    query = (
        "<deepresearch_rewrite_request>"
        f"{body}"
        "</deepresearch_rewrite_request>"
    )

    with pytest.raises(RewriteFastPathError) as exc_info:
        parse_rewrite_envelope(query)

    assert exc_info.value.code == "BAD_REQUEST"
    assert str(exc_info.value) == "invalid rewrite request"
