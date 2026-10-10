"""Spike: record what Flow's own page sends while a person starts one generation.

Usage:
    uv run python spikes/flow_capture.py [--profile NAME] [--minutes 10] [--send]

Chrome opens on the signed-in profile at flow.google.com. In that window, by hand:
open (or create) a project, upload ONE small reference image, and click generate once.

By default the generation request is recorded and then DROPPED before it leaves the
browser, so nothing is generated and no quota or credits are spent. Uploads go through,
because the media id they return is needed later. Pass ``--send`` to let the generation
through and record the reply as well.

The script records:

- every batchexecute request and response;
- reCAPTCHA Enterprise calls (site key, action, token length; never the token);
- the page's batchexecute session parameters.

The raw capture holds session material, so it is written only under the StudioWright
data directory, and the console shows a redacted summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import Counter
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Request, Response, Route, async_playwright

from studiowright import paths
from studiowright.browser import attach

FLOW_URL = "https://flow.google.com/"
IMAGE_RPC = "ogiZ0b"
#: Image generate, then video text, frames, references and start+end frames.
GENERATION_RPCS = frozenset({IMAGE_RPC, "YhhmEf", "eb1hJf", "MZZa6b", "nprQif"})

#: Wraps grecaptcha.enterprise.execute as soon as it exists, to learn the site key and
#: action names the page uses. Token values are never stored, only their length.
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

SESSION_PARAMS_JS = r"""
() => {
  const wiz = window.WIZ_global_data || {};
  return { wizKeys: Object.keys(wiz).length, hasAt: typeof wiz.SNlM0e === 'string',
           hasSid: typeof wiz.FdrFJe === 'string', hasBl: typeof wiz.cfb2h === 'string',
           url: location.href };
}
"""


def rpc_ids(url: str) -> list[str]:
    query = parse_qs(urlparse(url).query)
    return [rid for value in query.get("rpcids", []) for rid in value.split(",") if rid]


class Recorder:
    def __init__(self, *, drop_generation: bool) -> None:
        self.events: list[dict[str, Any]] = []
        self.drop_generation = drop_generation
        self.image_reply_at: float | None = None
        self.dropped_at: float | None = None
        self._pending: set[asyncio.Task[None]] = set()

    async def on_route(self, route: Route) -> None:
        request = route.request
        ids = rpc_ids(request.url)
        if self.drop_generation and GENERATION_RPCS.intersection(ids):
            self.events.append(
                {
                    "t": time.time(),
                    "kind": "dropped",
                    "url": request.url,
                    "rpcids": ids,
                    "headers": await request.all_headers(),
                    "post": request.post_data,
                }
            )
            self.dropped_at = time.time()
            await route.abort()
            return
        await route.continue_()

    def done(self) -> bool:
        finished = self.dropped_at or self.image_reply_at
        return bool(finished and time.time() - finished > 5)

    def on_request(self, request: Request) -> None:
        url = request.url
        if "batchexecute" in url or "recaptcha/enterprise" in url:
            self.events.append(
                {
                    "t": time.time(),
                    "kind": "request",
                    "url": url,
                    "rpcids": rpc_ids(url),
                    "method": request.method,
                    "post": request.post_data,
                }
            )

    def on_response(self, response: Response) -> None:
        if "batchexecute" in response.url:
            task = asyncio.ensure_future(self._read(response))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    async def _read(self, response: Response) -> None:
        try:
            body = await response.text()
        except Exception as exc:  # the page may navigate away mid-read
            body = f"<unreadable: {exc}>"
        ids = rpc_ids(response.url)
        self.events.append(
            {
                "t": time.time(),
                "kind": "response",
                "url": response.url,
                "rpcids": ids,
                "status": response.status,
                "body": body[:500_000],
            }
        )
        if IMAGE_RPC in ids and self.image_reply_at is None:
            self.image_reply_at = time.time()


def summarize(events: list[dict[str, Any]], hooks: list[dict[str, Any]]) -> str:
    requests = Counter(rid for e in events if e["kind"] == "request" for rid in e["rpcids"])
    lines = ["batchexecute requests by rpcid:"]
    lines += [f"  {rid}: {count}" for rid, count in requests.most_common()]
    for event in events:
        if event["kind"] == "request" and "recaptcha/enterprise" in event["url"]:
            key = parse_qs(urlparse(event["url"]).query).get("k", ["?"])[0]
            path = urlparse(event["url"]).path
            lines.append(f"recaptcha request: {path} site_key={key}")
    for hook in hooks:
        if hook.get("kind") == "recaptcha.execute":
            lines.append(f"recaptcha.execute: key={hook.get('key')} action={hook.get('action')}")
        elif hook.get("kind") == "recaptcha.token":
            lines.append(f"recaptcha token minted, length={hook.get('length')}")
    for event in events:
        if event["kind"] == "dropped":
            size = len(event["post"] or "")
            lines.append(f"dropped before sending: {event['rpcids']} (body {size} chars)")
        if event["kind"] == "response" and IMAGE_RPC in event["rpcids"]:
            urls = len(re.findall(r"flow-content\.google", event["body"]))
            lines.append(f"{IMAGE_RPC} reply: HTTP {event['status']}, image urls: {urls}")
    return "\n".join(lines)


async def run(profile: str, minutes: float, send: bool) -> None:
    out_dir = paths.captures_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(drop_generation=not send)
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        context = session.context
        await context.add_init_script(HOOK_JS)
        await context.route("**/batchexecute*", recorder.on_route)
        context.on("request", recorder.on_request)
        context.on("response", recorder.on_response)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(FLOW_URL, wait_until="domcontentloaded")
        mode = "it will be SENT" if send else "it will be recorded and dropped (nothing is spent)"
        print(f"Chrome is open. Upload ONE reference image and click generate once; {mode}.")
        deadline = time.time() + minutes * 60
        while time.time() < deadline and not recorder.done():
            await asyncio.sleep(1)
        hooks: list[dict[str, Any]] = []
        session: dict[str, Any] = {}
        for open_page in context.pages:
            try:
                hooks += await open_page.evaluate("window.__swLog || []")
                session = await open_page.evaluate(SESSION_PARAMS_JS)
            except Exception:  # a closed or navigating page has nothing to report
                continue
        await session.detach()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"flow-capture-{stamp}.json"
    payload = {"events": recorder.events, "hooks": hooks, "session": session}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(summarize(recorder.events, hooks))
    print(f"session params: {session}")
    print(f"raw capture: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--send", action="store_true", help="let the generation through")
    args = parser.parse_args()
    asyncio.run(run(args.profile, args.minutes, args.send))


if __name__ == "__main__":
    main()
