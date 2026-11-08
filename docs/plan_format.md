# H3MD plan files (v0.2.0)

The Director's top row has **Import plan…** (left) and **Export plan + media** / **Export plan (no media)** (right).

## The .zip (`plan_version: 1`)

| File | Always | What |
|---|---|---|
| `manifest.json` | yes | `format: "h3md-plan"`, `plan_version`, `name`, `created`, `h3md_version`, `width`, `height`, `fps`, `shots`, `frames`, `seconds`, `seed` (H3MD Loop seed), `with_media`, `thumbnail`, `media` (every picture / song / video: `kind`, `id`, `file`, `bytes`, `included`, `zip`, `missing`, `skipped`), `refmods` (RefMod files the plan uses; never inside the zip), `models` (model files named by active loaders and the workflow's own `models` list) |
| `plan.json` | yes | the Director plan, exactly as in `plan_json` (file paths as on the exporting computer) |
| `workflow.json` | when exported from the editor | the whole ComfyUI workflow: every node setting (LoRAs and strengths, seed, Loop Options, Save settings, ...) |
| `settings.json` | with `workflow.json` | a readable list of every node's widget values (`plan_json` shown as `<plan.json>`) |
| `thumbnail.jpg` | when a finished video exists | first frame (360 px wide) of the newest video saved under this workflow's filename prefix |
| `media/<file>` | **Export plan + media** only | pictures and songs; reference videos over 200 MB only with "incl. big ref video" ticked |

Never in the zip: finished videos, caches, RefMod files, model weights.

## Import

* `.zip`: media go to `input/H3MD_plans/<name>/` (an existing folder is never overwritten: `<name>_2`, ...), and the plan's file paths are rewritten to point there.
  * **Plan only** replaces this Director's plan; the rest of the workflow is kept.
  * **Plan + all settings** opens the saved workflow (with the rewritten plan) through ComfyUI's normal loader in a new tab, so missing node packs show red and missing models open the usual download dialog.
* `.json`: a bare plan, or `{"plan": {...}}`, e.g. written by an agent with the H3MD prompt skill. Only "Plan only".

Problems are listed and nothing is changed for you: a length that is not 5 + 17n frames, a first shot with an overlap, an overlap other than 39 / 22 / 0, a `<Picture N>` / `<Video N>` / `<Audio N>` tag with no such reference in the shot, a shot that names a media id missing from `media`, a file that is not in `input/`.

## Minimal plan .json (what an agent writes)

```json
{
  "version": 1, "fps": 24, "width": 704, "height": 1280,
  "globalPrompt": "",
  "media": {
    "images": [{"id": "eve", "file": "eve_home_ref.png"}],
    "videos": [],
    "audios": [{"id": "song", "file": "above_the_clouds_3min.mp3"}]
  },
  "segments": [
    {"length": 294, "overlapIn": 0,  "images": ["eve"],
     "audios": [{"id": "song", "mode": "locked", "offset": 0.0}], "prompt": "Duration: 12.25 s (294 frames). ..."},
    {"length": 362, "overlapIn": 39, "images": ["eve"],
     "audios": [{"id": "song", "mode": "locked", "offset": 10.625}], "prompt": "Duration: 15.08 s (362 frames). ..."}
  ]
}
```

* `length` = 5 + 17n frames at 24 fps (294 = 12.25 s, 345, 362 = 15.08 s, 396 = 16.5 s, ...).
* `overlapIn`: 0 for the first shot and for a hard cut, 39 to continue the previous shot.
* A locked song: `{"id", "mode": "locked", "offset"}` where `offset` (seconds) = where this shot starts in the song = the previous shot's offset + (its length − this shot's overlapIn) / 24.
* Each shot lists the media **ids** it uses; tags are numbered per shot in order (pictures, then videos — a video's own sound takes the next `<Audio>` number — then audios).
* File names are looked up in ComfyUI's `input/` folder; upload the files there (or with the library's Upload…) after importing.
* Window mode (0.3.9-beta, the Director's "Seamless long video" switch): add `"windowMode": true` (same as
  `{"length": 243, "overlap": 22, "highLength": 175, "highOverlap": 22}`: low-res windows chained, 接续窗口). Continued
  shots are rendered on one timeline, hard cuts stay cuts; inside a long shot time each action from the shot's start
  (`[8s] ...` or `At 00:07.9 ...`). `"preview": true` = low-res only. Details: [technical reference](technical-reference.md).
