---
name: studiowright
description: Use when the user wants images or video clips made in Google Flow (Nano Banana images; Omni or Veo video), wants to reuse reference images or start/end frames, or wants existing Flow media downloaded. Explains the StudioWright MCP tools, their options, costs and pacing.
---

# StudioWright: Google Flow through the user's own Chrome

StudioWright drives Google Flow's web UI in the user's own signed-in Chrome. It spends the user's own Flow subscription and credits. It is unofficial and not affiliated with Google.

## Before the first call

- Run `flow_status` once to see the default project, recent jobs and any cooldown.
- If there is no default project, call `flow_create_project` with a short title, or ask the user for a project id from their Flow URL and call `flow_use_project`.
- If a tool says the profile is not signed in, tell the user to run `sw auth login` in a terminal and sign in by hand. Never ask for passwords.

## Making media

- Images: `flow_generate_image(prompt, model, aspect, count, refs)`.
  - The default is Nano Banana 2.1 at 16:9 and x1. Images cost no credits.
  - `refs` takes local file paths or media ids. Local files are uploaded once and reused by content afterwards.
- Video: `flow_generate_video(prompt, model, mode, start, end, refs, aspect, resolution, duration, count)`.
  - Video costs credits, so confirm with the user before making several clips or using Veo Quality.
  - `mode="frames"`: optional `start` and `end` frames (file or media id). With neither, it is text-to-video.
  - `mode="ingredients"`: `refs` are reference images.
  - `resolution` (360p/720p) and `duration` (4/6/8/10 s) apply to Omni only.
- `flow_options` lists every model and option.

## Results

Each result returns `files` (saved local paths), `media_ids` and `job_id`.

- Reuse a `media_id` as a later `ref`, `start` or `end` instead of re-uploading the file.
- To show an image to the user, read the saved file.

## Timing, pacing, failures

- **Timing:** an image takes about 20-30 s, a clip about 30-90 s. Calls are queued and paced per profile. Don't fire many calls in parallel; run them one after another.
- **Unusual activity:** if Flow reports it, the profile cools down for about 30 min. Tell the user and stop; do not retry during the cooldown.
- **Clip timeout:** if a clip times out, the error names its media id. Later, `flow_download(media_ids=[...])` fetches it without generating again.
- **Refusals:** a content refusal is final for that prompt. Rephrase it rather than resubmitting it unchanged.
