"""Smoke test: talk to `sw mcp` over stdio like an agent would.

Usage:
    uv run python spikes/mcp_smoke.py [--generate]

Lists the tools, calls flow_options and flow_status (free). With --generate it also calls
flow_generate_image once (one image from the daily quota); with --music it calls
music_generate once (5 Flow Music credits).
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def text_of(result: object) -> str:
    parts = getattr(result, "content", []) or []
    return "\n".join(getattr(p, "text", "") for p in parts)


async def main(generate: bool, music: bool) -> None:
    sw = shutil.which("sw") or "sw"
    params = (
        StdioServerParameters(command="uv", args=["run", "sw", "mcp"])
        if sw == "sw"
        else StdioServerParameters(command=sw, args=["mcp"])
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        print("tools:", [t.name for t in tools.tools])
        options = await session.call_tool("flow_options", {})
        print("flow_options:", text_of(options)[:300].replace("\n", " "))
        status = await session.call_tool("flow_status", {"limit": 2})
        print("flow_status:", text_of(status)[:300].replace("\n", " "))
        if generate:
            started = time.time()
            result = await session.call_tool(
                "flow_generate_image",
                {"prompt": "a small orange fox sitting, flat 2D illustration", "aspect": "1:1"},
            )
            print(f"flow_generate_image ({time.time() - started:.1f} s):", text_of(result)[:600])
            print("is_error:", getattr(result, "is_error", getattr(result, "isError", None)))
        if music:
            started = time.time()
            result = await session.call_tool(
                "music_generate",
                {"sound": "short calm lo-fi bed, soft keys and vinyl crackle", "length": "1:00"},
            )
            print(f"music_generate ({time.time() - started:.1f} s):", text_of(result)[:600])
            print("is_error:", getattr(result, "is_error", getattr(result, "isError", None)))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--music", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.generate, args.music))
