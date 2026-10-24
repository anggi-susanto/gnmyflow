# Google Flow Music: how GnmyFlow drives it

Notes inherited from StudioWright by Umut Koçak, with fork updates; based on probes on flowmusic.app (October 2026). The spikes in
`spikes/music_*.py` reproduce each finding without spending credits unless stated.

## Shape of the app

Flow Music is a chat app with a compose panel. The **Producer** agent does most things
through tools (`audio__create_song`, `audio__render_edit`, `audio__apply_effect`,
`audio__split_stems`, `lyrics__register`, …); the compose panel and the song menus are
shortcuts that call the same tools. Sign-in is separate from Flow's (`sw auth login --url
https://www.flowmusic.app/`), and so is the credit balance.

| Feature | Where in the UI | GnmyFlow |
| --- | --- | --- |
| New song / instrumental | compose panel → Generate | `sw music create`, `music_generate` |
| Extend, Cover, Replace | song menu → Remix → … (panel in edit mode) | `sw music extend/cover/replace`, `music_edit` |
| Variation | song menu → Remix → Variation | `sw music variation`, `music_edit(mode="variation")` |
| Trim | song menu → Remix → Trim (dialog) | `sw music trim`, `music_edit(mode="trim")` |
| Split stems | song menu → Split stems | `sw music stems`, `music_stems` |
| Download WAV / M4A / MP3 | song menu → Download | `sw music download`, `music_download` |
| Video templates (Modern, Player, Simple) | song menu → Download → Download video | `sw music video`, `music_video` |
| AI music video | Producer conversation (song + subject image + style image) | `sw music chat`, `music_chat` |
| Producer chat (lyrics, ideas, remix my music, …) | chat box | `sw music chat`, `music_chat` |
| Upload your audio (max 40 MB) | chat "+" → Audio | `sw music upload`, `music_upload` |
| Cover art (upload or prompt) | panel → Details | `--cover`, `--cover-prompt` |
| Library, credits | Songs, account menu | `sw music list/credits`, `music_library` |

Not covered on purpose: playlists, spaces, projects, profile, publishing, sharing,
reporting, deleting, Turntable voting, likes, voice mode and microphone recording. They
organise or socialise songs rather than make them.

## Upload wait limits

Audio upload checks can take much longer than a chat turn. `sw music upload FILE
--confirm-rights --timeout 2400` sets the upload-check wait; `sw music chat "remix this"
--audio FILE --confirm-rights --upload-timeout 2400` sets the same wait before sending
the message. Both default to 1800 seconds and accept positive integer seconds. Chat's
existing `--timeout` still controls only the Producer turn (default 900 seconds). These
are not whole-command deadlines: browser setup, file selection, pacing and downloads
are separate.

Python uses `Studio.music_upload(..., timeout_s=1800)` and
`ChatRequest.build(..., upload_timeout_s=1800)` (also a `ChatRequest` field).
MCP's `music_upload` exposes `timeout_s=1800`; `music_chat` exposes
`upload_timeout_s=1800`, separately from its `timeout_s=900`. Upload waits must be finite
and strictly positive; Python accepts fractional seconds as well. Rights confirmation
and rejection reasons are unchanged. Only the documented `complete` upload status is
accepted; unknown statuses fail rather than returning a usable song id.

## Wire facts GnmyFlow reads (passively)

- `GET /__api/billing/credits` → `data.credits_remaining`; `GET /__api/billing/subscription`
  → `data.subscription_tier`. Both are fetched by the page on load. An in-page fetch of
  these fails because the app adds its own auth header, so GnmyFlow only reads them.
- `POST /__api/producer/tool-call` (panel and menu actions) and `POST /__api/conversation`
  (chat messages) return `{"job_id"}`.
- `GET /__api/messages/<job>/stream?last_id=0` is a server-sent-event stream:
  `begin`, `conversation_id {id}`, `part {index, status: start|delta|final, part}`,
  `message {parts}`, `complete`, `suggestion {parts}`, `final`. Parts have `part_kind`
  `user-prompt`, `text`, `tool-call` (`tool_name`, `args`) or `tool-return`. The
  suggestions are a `synthetic__suggest_actions` tool call.
- `GET /__api/audio-create-song-status/<op>` → `status` (`complete`), `clip_id`.
- `POST /__api/clips` → `{"clips": {id: record}}`; `GET /__api/clips/auth-user?…&filter=generations`
  → `{"clips": [record]}`. A record has `op_type` (e.g. `audio__render_edit`), `duration
  {status, value}`, `title`, `lyrics`, `wav_url`, `audio_url` (M4A), `image_url`,
  `video_url`, `created_at`.
- `wav_url` of edited songs can 404 for a while; the Download menu
  (`GET /__api/download/audio/<id>?format=wav|mp3|m4a`) converts on demand.
- Video templates: `POST /__api/video/clip` → `{"job_id": "<clip>:<hash>"}`, then
  `GET /__api/video/clip/<job>` → `{"status": "complete", "video_url"}` or
  `{"status": "failed"}`.
- Uploads: `POST /__api/producer/upload-audio` → `{"id"}` (the new song's id), then
  `…/upload-check-status` polls `status` (`pending` → `complete`) with `decision`/`reason`
  from a rights check.
- Cover prompts: `POST /__api/generate/image` → `{"image_id"}` after ~14 s.

## UI facts

- The song page's own "…" menu is `main button[aria-haspopup='menu']`; the player bar's
  "More options for …" button sits outside `<main>` and belongs to whatever is playing.
- Radix submenus close when the pointer travels diagonally, so menus are walked with the
  keyboard (focus, ArrowRight, Enter).
- Edit mode: mode button (EXTEND / COVER / REPLACE), a SETTINGS popover with time fields
  (Extend from / until; Replace start / end), `textarea[aria-label='Instruction']`,
  Edit Lyrics dialog (full lyrics sheet, Done), Advanced → Seed, Details → title and cover.
- Variation and Split stems start at once from the menu; they do not open a panel.
- Uploading audio or images shows a one-time rights notice ("I agree"). GnmyFlow clicks
  it only when the user passes `--confirm-rights` / `confirm_rights`.
- A cover prompt closes its popover at once and draws in the background; the song must
  not be submitted until the preview image has loaded.
- Once, Generate opened a new session but sent nothing (no tool call, nothing spent).
  GnmyFlow now watches for the work-starting POST; without one within 45 s it
  presses Generate (or Send) once more, and other actions fail at once.
- Templates sometimes fail on Flow Music's side (seen for Player and Simple); another
  template or a retry usually works.

## Measured (warm tab, plan "plus")

| Action | Time | Credits |
| --- | --- | --- |
| New song (60 s) | 32-46 s | 5 |
| Extend / cover / replace | 38-49 s | 5 |
| Variation | 29 s | 5 |
| Trim | 20 s | 0 |
| Split stems (4 stems) | 51-64 s | 0 |
| Video template | 11-60 s | 0 |
| Chat turn (text only) | 14 s | 0 |
| Song made through chat | 37 s | 5 |
| Upload (30 s WAV) | 20 s | 0 |
| MP3 / converted WAV download | 11-13 s | 0 |
| Cover from a prompt | ~15 s | about 0.5 |

Audio is 48 kHz 16-bit stereo WAV. Lengths are 1:00-3:00 for new songs; an extension can
reach past that. The Producer sometimes returns two takes and asks which is better
("No credits used"); GnmyFlow saves both.
