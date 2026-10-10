from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from studiowright.flow import batchexecute as be

SESSION = be.Session(sid="123", build="boq_x_20261010", csrf="AT:1", locale="tr")


def framed(*rows: list) -> str:
    """Build a reply the way the server frames it: prefix, then length + row pairs."""
    parts = [be.XSSI_PREFIX, ""]
    for row in rows:
        line = json.dumps([row])
        parts += [str(len(line)), line]
    return "\n".join(parts)


def test_request_url_carries_session_and_rpc() -> None:
    url = be.request_url("jHPbke", SESSION, source_path="/project/p1", request_id=4242)
    query = parse_qs(urlparse(url).query)
    assert url.startswith(be.ENDPOINT)
    assert query["rpcids"] == ["jHPbke"]
    assert query["source-path"] == ["/project/p1"]
    assert query["f.sid"] == ["123"]
    assert query["bl"] == ["boq_x_20261010"]
    assert query["hl"] == ["tr"]
    assert query["_reqid"] == ["4242"]
    assert query["rt"] == ["c"]


def test_request_body_double_encodes_payload_and_round_trips() -> None:
    payload = ["projects/*", [None, ["Çay saati"]], [None, 22]]
    body = be.request_body("jHPbke", payload, "AT:1")
    assert parse_qs(body)["at"] == ["AT:1"]
    (call,) = be.decode_envelope(body)
    assert call[0] == "jHPbke"
    assert isinstance(call[1], str)
    assert json.loads(call[1]) == payload
    assert call[2:] == [None, "generic"]


def test_parse_reply_returns_decoded_payload() -> None:
    text = framed(
        ["wrb.fr", "jHPbke", json.dumps(["pid-1", ["title"]]), None, None, None, "generic"]
    )
    assert be.parse_reply(text, "jHPbke") == ["pid-1", ["title"]]


def test_parse_reply_raises_on_refusal_with_reason() -> None:
    error = [
        7,
        None,
        [["type.googleapis.com/google.rpc.ErrorInfo", ["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]],
    ]
    text = framed(["wrb.fr", "ogiZ0b", None, None, None, error, "generic"])
    with pytest.raises(be.RpcError) as caught:
        be.parse_reply(text, "ogiZ0b")
    assert caught.value.code == 7
    assert caught.value.reasons == ["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]


def test_parse_reply_reports_missing_row_for_login_html() -> None:
    with pytest.raises(be.MissingReply):
        be.parse_reply("<html>Sign in</html>", "ogiZ0b")


def test_rows_skips_noise_lines() -> None:
    text = framed(["di", 42], ["wrb.fr", "x", "[]"]) + "\nnot json\n[broken"
    assert [row[0] for row in be.rows(text)] == ["di", "wrb.fr"]
