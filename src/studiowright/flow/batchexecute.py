"""Google's batchexecute RPC framing, as used by flow.google.com.

A call is a form-encoded POST: ``f.req`` holds an envelope whose inner payload is itself a
JSON string, and ``at`` is the page's CSRF value. The reply starts with ``)]}'`` and then
interleaves length lines with JSON rows; each RPC answer is a
``["wrb.fr", rpc_id, payload_json, ...]`` row. Refusals arrive with HTTP 200, a null payload
and the error in slot 5.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

ENDPOINT = "https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute"
XSSI_PREFIX = ")]}'"


@dataclass(frozen=True)
class Session:
    """Per-page values every call needs: session id, build label, CSRF token, locale."""

    sid: str
    build: str
    csrf: str
    locale: str = "en"


class RpcError(Exception):
    """The server answered the call with an error instead of a payload."""

    def __init__(self, rpc_id: str, code: int | None, reasons: list[str]) -> None:
        self.rpc_id = rpc_id
        self.code = code
        self.reasons = reasons
        detail = ", ".join(reasons) if reasons else "no reason given"
        super().__init__(f"{rpc_id} failed with code {code}: {detail}")


class MissingReply(Exception):
    """The reply holds no row for the requested RPC (e.g. a login page came back)."""


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def request_url(rpc_id: str, session: Session, *, source_path: str, request_id: int) -> str:
    query = {
        "rpcids": rpc_id,
        "source-path": source_path,
        "f.sid": session.sid,
        "bl": session.build,
        "hl": session.locale,
        "_reqid": str(request_id),
        "rt": "c",
    }
    return f"{ENDPOINT}?{urlencode(query)}"


def request_body(rpc_id: str, payload: Any, csrf: str) -> str:
    envelope = [[[rpc_id, _compact(payload), None, "generic"]]]
    return urlencode({"f.req": _compact(envelope), "at": csrf}) + "&"


def decode_envelope(body: str) -> list[list[Any]]:
    """Every ``[rpc_id, payload_json, ...]`` call in a form-encoded request body."""
    from urllib.parse import parse_qs

    raw = parse_qs(body, keep_blank_values=True).get("f.req", [""])[0]
    envelope = json.loads(raw)
    return [call for group in envelope for call in group]


def rows(text: str) -> list[list[Any]]:
    """All JSON rows of a framed reply. Lines that are not JSON arrays are skipped."""
    if text.startswith(XSSI_PREFIX):
        text = text[len(XSSI_PREFIX) :]
    found: list[list[Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            found.extend(item for item in value if isinstance(item, list))
    return found


def _error_reasons(detail: Any) -> tuple[int | None, list[str]]:
    if not isinstance(detail, list) or not detail:
        return None, []
    code = detail[0] if isinstance(detail[0], int) else None
    reasons: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str) and node.isupper() and "_" in node:
            reasons.append(node)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(detail[1:])
    return code, reasons


def parse_reply(text: str, rpc_id: str) -> Any:
    """The decoded payload for ``rpc_id``; raises :class:`RpcError` or :class:`MissingReply`."""
    for row in rows(text):
        if len(row) >= 3 and row[0] == "wrb.fr" and row[1] == rpc_id:
            payload = row[2]
            if payload is None:
                code, reasons = _error_reasons(row[5] if len(row) > 5 else None)
                raise RpcError(rpc_id, code, reasons)
            return json.loads(payload)
    raise MissingReply(f"no {rpc_id} row in the reply")
