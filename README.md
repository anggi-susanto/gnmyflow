# StudioWright

A fast CLI and MCP server for **Google Flow** (images and video) and **Google Flow Music**.

> **Unofficial; not affiliated with Google.** StudioWright drives a real, signed-in Chrome on your own Google account and spends your own credits. It automates web apps that can change at any time, and automation is subject to Google's Terms of Service.

Status: early development.

## How it works

StudioWright keeps one real Chrome window signed in to your account. Instead of clicking through the UI for every job, it calls the web app's own endpoints from inside that page:

- references are uploaded once and reused by id;
- several jobs can be in flight at once, under a pacing governor that backs off when Google asks it to.

## Install

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and Google Chrome.

```bash
uv tool install --python 3.13 -e .
sw auth login        # opens Chrome; sign in to Google and Flow by hand
```

StudioWright keeps its data in its own directory (`%LOCALAPPDATA%\studiowright` on Windows). Set `STUDIOWRIGHT_HOME` to move it.

## License

MIT
