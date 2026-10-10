"""Spike: learn Flow's wire templates at zero cost, unattended.

Usage:
    uv run python spikes/flow_probe.py [--profile NAME] [--skip-upload]

Steps, in one Chrome session on the signed-in profile:

1. Open flow.google.com. Find the batchexecute session values (``f.sid``, ``bl``, ``at``)
   and check whether ``WIZ_global_data`` exposes them.
2. Create a project with an in-page ``jHPbke`` call. This needs no reCAPTCHA token and
   costs nothing; it proves the page can carry our own RPCs.
3. Open the project in image mode, x1, type a prompt and press generate. The ``ogiZ0b``
   request is recorded and DROPPED, so nothing is generated.
4. Upload a tiny generated PNG through the toolbar (uploads are free) and record ``maseQ``.
   If Flow shows its one-time "rights to use this image" dialog, the script waits for a
   person to answer it, then gives up after 90 s.

Raw captures hold session material and stay in the StudioWright data directory. The
console shows a redacted summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import struct
import time
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import BrowserContext, Page, Route, async_playwright

from studiowright import paths
from studiowright.browser import attach
from studiowright.flow import batchexecute as be

FLOW_URL = "https://flow.google.com/"
GENERATION_RPCS = frozenset({"ogiZ0b", "YhhmEf", "eb1hJf", "MZZa6b", "nprQif"})
PROMPT = "a single red apple on a white table, flat textured 2D illustration"

READY = ".settings-trigger-button"
COOKIE_REJECT = "button.glue-cookie-notification-bar__reject"
DIALOG_CLOSE = "[role='dialog'] button:has(mat-icon:text-is('close'))"
OVERLAY = ".cdk-overlay-pane"
COMPOSER = "[contenteditable='true']"
TOOLBAR_ADD = "xpath=//button[.//mat-icon[normalize-space()='add']][not(ancestor::flow-prompt-box)]"
UPLOAD_ITEM = f"{OVERLAY} [role='menuitem']:has(mat-icon:text-is('upload'))"
CONSENT_DIALOG = "[role='dialog']:has(button.flow-button-medium)"

HOOK_JS = r"""
(() => {
  const log = (entry) => { (window.__swLog = window.__swLog || []).push({ t: Date.now(), ...entry }); };
  const wrap = () => {
    const ent = window.grecaptcha && window.grecaptcha.enterprise;
    if (!ent || typeof ent.execute !== 'function' || ent.__swWrapped) return !!(ent && ent.__swWrapped);
    const original = ent.execute.bind(ent);
    ent.execute = (key, options) => {
      log({ kind: 'recaptcha.execute', key, action: options && options.action });
      return original(key, options).then((token) => {
        log({ kind: 'recaptcha.token', length: token ? token.length : 0 });
        return token;
      });
    };
    ent.__swWrapped = true;
    return true;
  };
  const timer = setInterval(() => { if (wrap()) clearInterval(timer); }, 25);
})();
"""

WIZ_JS = r"""
() => {
  const wiz = window.WIZ_global_data || {};
  return { FdrFJe: wiz.FdrFJe ?? null, cfb2h: wiz.cfb2h ?? null, SNlM0e: wiz.SNlM0e ?? null };
}
"""

FETCH_JS = r"""
async ([url, body]) => {
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: { 'content-type': 'application/x-www-form-urlencoded;charset=UTF-8', 'x-same-domain': '1' },
    body,
  });
  return { status: response.status, text: await response.text() };
}
"""


def tiny_png(path: Path, rgb: tuple[int, int, int] = (200, 40, 40), size: int = 64) -> Path:
    """Write a solid-colour PNG without any imaging library."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    row = b"\x00" + bytes(rgb) * size
    raw = zlib.compress(row * size)
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
    )
    return path


def skeleton(value: Any, limit: int = 60) -> Any:
    """A payload with long strings (tokens, image bytes) replaced by their length."""
    if isinstance(value, str):
        return value if len(value) <= limit else f"<str len={len(value)}>"
    if isinstance(value, list):
        return [skeleton(item, limit) for item in value]
    if isinstance(value, dict):
        return {key: skeleton(item, limit) for key, item in value.items()}
    return value


class Probe:
    def __init__(self, context: BrowserContext, out_dir: Path) -> None:
        self.context = context
        self.out_dir = out_dir
        self.calls: list[dict[str, Any]] = []
        self.report: dict[str, Any] = {}

    async def on_route(self, route: Route) -> None:
        request = route.request
        ids = parse_qs(urlparse(request.url).query).get("rpcids", [""])[0].split(",")
        entry = {"t": time.time(), "url": request.url, "rpcids": ids, "post": request.post_data}
        if GENERATION_RPCS.intersection(ids):
            entry["dropped"] = True
            self.calls.append(entry)
            await route.abort()
            return
        self.calls.append(entry)
        response = await route.fetch()
        entry["status"] = response.status
        entry["reply"] = (await response.text())[:300_000]
        await route.fulfill(response=response)

    def first_call(self, rpc_id: str) -> dict[str, Any] | None:
        return next((c for c in self.calls if rpc_id in c["rpcids"]), None)

    async def wait_for_call(self, rpc_id: str, timeout_s: float) -> dict[str, Any] | None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            call = self.first_call(rpc_id)
            if call and ("reply" in call or call.get("dropped")):
                return call
            await asyncio.sleep(0.25)
        return None

    # --- step 1 ---------------------------------------------------------------------------
    async def session(self, page: Page) -> be.Session:
        await page.goto(FLOW_URL, wait_until="domcontentloaded")
        deadline = time.time() + 30
        while time.time() < deadline and not any("post" in c and c["post"] for c in self.calls):
            await asyncio.sleep(0.25)
        self.report["landing_url"] = page.url
        first = next((c for c in self.calls if c.get("post")), None)
        if first is None:
            raise RuntimeError(f"no batchexecute call after load; page is {page.url}")
        query = parse_qs(urlparse(first["url"]).query)
        wire = {
            "sid": query.get("f.sid", [""])[0],
            "build": query.get("bl", [""])[0],
            "locale": query.get("hl", ["en"])[0],
            "csrf": parse_qs(first["post"]).get("at", [""])[0],
        }
        wiz = await page.evaluate(WIZ_JS)
        self.report["session_source"] = {
            "wire_has": {key: bool(value) for key, value in wire.items()},
            "wiz_FdrFJe_is_sid": wiz["FdrFJe"] == wire["sid"],
            "wiz_cfb2h_is_bl": wiz["cfb2h"] == wire["build"],
            "wiz_SNlM0e_is_at": wiz["SNlM0e"] == wire["csrf"],
            "first_rpc": first["rpcids"],
        }
        return be.Session(**wire)

    # --- step 2 ---------------------------------------------------------------------------
    async def create_project(self, page: Page, session: be.Session) -> str:
        title = f"StudioWright probe {time.strftime('%H:%M')}"
        payload = ["projects/*", [None, [title]], [None, 22]]
        url = be.request_url(
            "jHPbke", session, source_path="/", request_id=random.randint(10_000, 99_999)
        )
        result = await page.evaluate(
            FETCH_JS, [url, be.request_body("jHPbke", payload, session.csrf)]
        )
        self.report["create_project_http"] = result["status"]
        reply = be.parse_reply(result["text"], "jHPbke")
        self.report["create_project_reply"] = skeleton(reply)
        project_id = reply[0]
        if not isinstance(project_id, str):
            raise RuntimeError(f"unexpected jHPbke reply shape: {skeleton(reply)}")
        return project_id

    # --- step 3 ---------------------------------------------------------------------------
    async def clear_blockers(self, page: Page) -> None:
        for selector in (COOKIE_REJECT, DIALOG_CLOSE):
            button = page.locator(selector).first
            if await button.count() and await button.is_visible():
                await button.click()
                await page.wait_for_timeout(500)

    async def capture_image_submit(self, page: Page, project_id: str) -> None:
        await page.goto(f"{FLOW_URL}project/{project_id}", wait_until="domcontentloaded")
        try:
            await page.locator(READY).first.wait_for(state="visible", timeout=30_000)
        except Exception:
            self.report["composer"] = "settings trigger never became visible (agent-only?)"
            raise
        await self.clear_blockers(page)
        await page.locator(READY).first.click()
        pane = page.locator(OVERLAY).last
        await pane.locator("[role='radio']:has(mat-icon:text-is('image'))").first.click()
        await pane.locator("[role='radio']").filter(has_text="x1").first.click()
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
        composer = page.locator(COMPOSER).first
        await composer.click()
        await page.keyboard.insert_text(PROMPT)
        await page.wait_for_timeout(400)
        submit = page.locator("button:has(mat-icon:text-is('arrow_forward'))").first
        await submit.click()
        call = await self.wait_for_call("ogiZ0b", 20)
        if call is None:
            self.report["image_submit"] = "no ogiZ0b request within 20 s"
            return
        (envelope_call,) = be.decode_envelope(call["post"])
        self.report["image_submit"] = {
            "url_query_keys": sorted(parse_qs(urlparse(call["url"]).query)),
            "payload": skeleton(json.loads(envelope_call[1])),
        }

    # --- step 4 ---------------------------------------------------------------------------
    async def capture_upload(self, page: Page) -> None:
        image = tiny_png(self.out_dir / "probe-red.png")
        await page.locator(TOOLBAR_ADD).first.click()
        item = page.locator(UPLOAD_ITEM).first
        await item.wait_for(state="visible", timeout=8_000)
        async with page.expect_file_chooser(timeout=8_000) as chooser_info:
            await item.click()
        chooser = await chooser_info.value
        await chooser.set_files(str(image))
        deadline = time.time() + 90
        consent_seen = False
        while time.time() < deadline:
            if self.first_call("maseQ") and "reply" in self.first_call("maseQ"):
                break
            if not consent_seen and await page.locator(CONSENT_DIALOG).count():
                consent_seen = True
                print("Flow is asking for upload consent; answer it in the Chrome window.")
            await asyncio.sleep(0.5)
        self.report["upload_consent_dialog"] = consent_seen
        call = self.first_call("maseQ")
        if not call or "reply" not in call:
            self.report["upload"] = "no maseQ reply"
            return
        (envelope_call,) = be.decode_envelope(call["post"])
        try:
            reply = be.parse_reply(call["reply"], "maseQ")
        except (be.RpcError, be.MissingReply) as exc:
            reply = f"<error: {exc}>"
        self.report["upload"] = {
            "http": call.get("status"),
            "payload": skeleton(json.loads(envelope_call[1])),
            "reply": skeleton(reply),
        }


async def run(profile: str, skip_upload: bool) -> None:
    out_dir = paths.captures_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        context = session.context
        probe = Probe(context, out_dir)
        await context.add_init_script(HOOK_JS)
        await context.route("**/batchexecute*", probe.on_route)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            started = time.time()
            session = await probe.session(page)
            probe.report["t_session_s"] = round(time.time() - started, 1)
            started = time.time()
            project_id = await probe.create_project(page, session)
            probe.report["project_id"] = project_id
            probe.report["t_create_project_s"] = round(time.time() - started, 1)
            await probe.capture_image_submit(page, project_id)
            if not skip_upload:
                await probe.capture_upload(page)
        except Exception as exc:
            probe.report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                probe.report["recaptcha"] = await page.evaluate("window.__swLog || []")
            except Exception:
                probe.report["recaptcha"] = "unavailable"
            stamp = time.strftime("%Y%m%d-%H%M%S")
            raw = out_dir / f"flow-probe-{stamp}.json"
            raw.write_text(
                json.dumps({"calls": probe.calls, "report": probe.report}, indent=1),
                encoding="utf-8",
            )
            await session.detach()
    print(json.dumps(probe.report, indent=1, ensure_ascii=False))
    print(f"raw capture: {raw}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    parser.add_argument("--skip-upload", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.profile, args.skip_upload))


if __name__ == "__main__":
    main()
