# StudioWright

Generate **Google Flow** images and videos from the terminal, from Claude Code, or from any MCP client, using your own Flow subscription.

> **Unofficial; not affiliated with Google.** StudioWright drives Flow's web UI in your own signed-in Chrome and spends your own credits. Flow can change at any time, and automation is subject to Google's Terms of Service.

## How it works

StudioWright starts your installed Chrome on a dedicated profile and attaches to it. Chrome keeps running between commands, so every command or tool call finds a warm, signed-in Flow tab.

- **Inputs:** typing, clicking and the file chooser, the same actions a person takes in the UI.
- **Requests are never intercepted or altered.** StudioWright only reads Flow's replies, to learn the new media's id and its download URL.
- **References are uploaded once per project** and then reused by content. Local files and Flow media ids both work.
- **Submits are paced per profile, across processes.** If Flow reports unusual activity, the profile cools down.

Measured on a warm tab:

| Job | Time |
| --- | --- |
| Image with one reference (upload, attach, generate, download) | ~18 s |
| 4 s clip with start and end frames | ~30 s |

## Install

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and Google Chrome.

```bash
git clone https://github.com/umutkocak/studiowright.git
cd studiowright
uv tool install --python 3.13 -e .
sw auth login        # Chrome opens: sign in to Google and open Flow once, then close it
```

## Command line

```bash
sw project create "My film"            # or: sw project use <id from the Flow URL>
sw image "a lighthouse at dawn, flat 2D" --aspect 16:9 --count 2
sw image "the same keeper, side view" --ref keeper.png --model nano-banana-pro --size 2K
sw video "slow push-in" --start frame1.png --end frame2.png --duration 6 --resolution 720p --size 1080p
sw video "a stormy sea" --model veo-fast --aspect 9:16 --dry-run      # shows Flow's price, submits nothing
sw video "they meet" --mode ingredients --ref a.png --ref b.png --max-credits 12
sw run shots.jsonl                      # a batch, resumable; later jobs can reuse earlier results
sw download <media-id> --size 1080p     # fetch existing media again, nothing is generated
sw jobs                                 # recent jobs
sw pace                                 # pacing and cooldown
sw window hide                          # keep Chrome working off-screen (never minimize it)
sw -v ...                               # log every step
```

Every generating command saves files to `Downloads/studiowright` (`--out` changes it) and prints the paths. `--json` prints the full result: files, media ids, the job id and, for video, the credits Flow stated.

| Option | Values |
| --- | --- |
| Image models | `nano-banana-2.1` (default), `nano-banana-pro`, `nano-banana-2-lite` |
| Image aspects | `16:9`, `4:3`, `1:1`, `3:4`, `9:16`; count 1-4 |
| Video models | `omni-flash` (default), `veo-lite`, `veo-fast`, `veo-quality` |
| Video | modes `frames` (start/end frames or text only) / `ingredients` (reference images); aspect `16:9` / `9:16`; Omni only: `360p` / `720p` and `4`, `6`, `8`, `10` s |
| `--size` | Flow's own download sizes, e.g. images `1K` (original) / `2K` (upscaled); clips `720p` / `1080p` (upscaled), `270p` (GIF); `4K` where the plan allows |

Prices Flow showed on a Pro account (October 2026):

| Model | Clip | Credits |
| --- | --- | --- |
| Omni 1.1 Flash | 360p, 4 s | 4 |
| Veo 3.1 Lite | 8 s | 10 |
| Veo 3.1 Fast | 8 s | 20 |
| Veo 3.1 Quality | 8 s | 100 |

Images cost no credits.

### Batch files

JSON Lines, one job per line, or a JSON list:

```json
{"id": "keeper", "kind": "image", "prompt": "a lighthouse keeper, flat 2D", "refs": ["style.png"]}
{"id": "close", "kind": "image", "prompt": "close-up of the same keeper", "refs": ["keeper"]}
{"id": "clip", "kind": "video", "prompt": "he turns to the sea", "start": "keeper", "duration": 6, "max_credits": 12}
```

- `refs`, `start` and `end` take file paths (relative to the batch file), media ids, or the `id` of an earlier job.
- Results go to `shots.jsonl.results.jsonl`. Running the batch again skips finished jobs.
- `--keep-going` continues past a failure.

## Claude Code plugin

The repository is also a Claude Code plugin marketplace. The plugin adds the MCP tools and a skill that explains them.

```text
/plugin install studiowright --marketplace umutkocak/studiowright
```

To try a local checkout: `claude --plugin-dir ./plugins/studiowright`. The plugin runs `sw mcp`, so `sw` must be on the PATH. Otherwise set `STUDIOWRIGHT_BIN` to its full path.

MCP tools:

- `flow_generate_image`, `flow_generate_video`: video supports `max_credits` and `dry_run`
- `flow_upload`, `flow_download`
- `flow_create_project`, `flow_use_project`
- `flow_status`, `flow_options`

## Configuration

| Variable | Meaning | Default |
| --- | --- | --- |
| `STUDIOWRIGHT_PROFILE` | Chrome profile to use | `default` |
| `STUDIOWRIGHT_HOME` | data directory: profiles, database | `%LOCALAPPDATA%\studiowright` |
| `STUDIOWRIGHT_OUTPUT` | where media is saved | `Downloads/studiowright` |
| `STUDIOWRIGHT_GAP_S`, `STUDIOWRIGHT_JITTER_S` | pacing between submits | 20 s + 0-6 s |
| `STUDIOWRIGHT_COOLDOWN_S` | pause after an unusual-activity refusal | 1800 s |

## License

MIT
