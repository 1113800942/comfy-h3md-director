# H3MD Director

**English** | [中文](README.zh-CN.md)

Long, multi-shot MiniMax H3 video in ComfyUI — **2+ minutes at 1080p in one run**. Plan shots in a visual Director,
generate them segment by segment with a bundled two-pass sampler, keep every finished segment on disk, and write the
final video chunk by chunk.

Copyright (C) 2026 YJ · License: AGPL-3.0-or-later + attribution term (see [License](#license))

> H3MD does **not** include any MiniMax H3 weights. The MiniMax H3 model has its own license — read [NOTICE.md](NOTICE.md)
> before you download or use the model.

## Highlights
- **Long video in one run**: 2–3 minutes, 1088×1920, automatic split into continued segments; latent continuation, no pixel round trips at the joins.
- **Repair instead of re-render**: `rerun_segments` re-renders only the bad segments, locked to their neighbours at both ends.
- **Motion copy**: a reference video drives the motion through depth + SAM3 + Fun ControlNet; the look comes from your photos.
- **No long-video drift**: `lowres_anchor` keeps color and faces stable across a whole single-scene take.
- **Fits your GPU**: automatic splitting by VRAM budget, high-res strip tiling with OOM retry, disk cache and resume, slow-step guard; 4B + ClipProj encoder option saves 10+ GB.
- **Example workflow** with model links, node links, usage notes and the ideas behind it: [`example_workflows/H3MD Director.json`](example_workflows/H3MD%20Director.json).

Contents: [What it does](#what-it-does) · [Install](#install) · [Quick start](#quick-start) ·
[Low VRAM](#low-vram) · [Models](#models-manifest) · [Caveats](#caveats) · [How H3MD works](#how-h3md-works-ideas-you-can-reuse) ·
[License](#license) · [Technical reference](#technical-reference-english)

---


### What it does
* **Multi-segment continuation**: `H3MD Loop Sampler` runs every shot/segment of the Director plan. Continued shots
  start from the tail of the previous segment's final latent (`overlapIn` 22 or 39 frames, drift: lightly re-noised
  while sampling, restored exactly at the end); hard cuts (`overlapIn` 0) are generated independently. Shots over the
  token budget are auto-split into sub-segments.
* **Two-pass sampling, bundled**: every segment runs through `H3MD Sampler`, built on the bundled Luna-H3-Sampler
  engine (GPL-3.0): the first steps at low resolution, a learned H3 latent upscaler lifts the video, the rest at full
  resolution. Audio is one trajectory across both passes.
* **Automatic high-res tiling**: with `highres_tiling` (default on) each segment measures the free VRAM and runs the
  full frame when it fits, otherwise the fewest strips that fit. On a CUDA out-of-memory error the high-res pass is
  retried with more strips (the low-res result is kept) and the machine remembers it for next time.
* **Disk cache + resume**: with `cache_mode = disk` (default) each segment is decoded and written to disk right away,
  so RAM holds one segment. With `resume_from_cache` on, re-queueing skips finished segments and reuses the base
  seed of the cached run.
* **Streamed save**: `long video` → `H3MD Save Video Interp` reads, (optionally) RIFE-interpolates and encodes the
  frames chunk by chunk, with bounded RAM.
* **Director console**: `H3MD Director` has a built-in visual editor (asset bin, timeline, shot tabs, @-mentions,
  thumbnails on picture tags).
* **Motion transfer**: `H3MD Depth Control` turns a reference video into depth / SAM3 person silhouettes for the
  Fun ControlNet and `<Video N>`.

### Install
1. Put the folder at `custom_nodes/ComfyUI-H3MD-Director`
   (`git clone https://github.com/1113800942/comfy-h3md-director custom_nodes/ComfyUI-H3MD-Director`, or copy it).
2. Install the node packs below, then restart ComfyUI.
3. No extra `pip install`: H3MD only uses torch, numpy, PyAV, Pillow and einops, which ship with ComfyUI.
4. Recommended: a learned latent upscaler in `models/latent_upscale_models/`, e.g.
   `minimax_h3_latent_upscaler_3d_fp16.safetensors` or a fine-tune such as
   `h3_upscaler_sharpness_2000steps_v0.1_fp32.safetensors`. Without one, set `upscaler_model = none` (nearest, softer).

Dependencies:
* **ComfyUI** with the core MiniMax H3 nodes (`comfy_extras.nodes_minimax_h3`), 0.43 or newer recommended.
* **selflift-Avatar is no longer needed**: the two-pass engine is bundled (`h3md_luna/`).
* **ComfyUI-ClipProj** (<https://github.com/nicolab28/ComfyUI-ClipProj>) — clip projection model used by the example.
* **ComfyUI-VFI** (<https://github.com/GACLove/ComfyUI-VFI>) — only for RIFE interpolation in `H3MD Save Video Interp`.
* The example workflow also uses ComfyUI-KJNodes, rgthree-comfy, MiniMax H3 Audio T8, CRT-Nodes and
  ComfyUI-YCNodes-MiniMax-H3; ComfyUI lists any missing pack when the workflow opens.

### Quick start
ComfyUI **Templates** → `ComfyUI-H3MD-Director` → `H3MD Director` (the 133 s motion-copy sample, with notes on models,
nodes and usage) or `H3MD alpha example` (files in `example_workflows/`). The templates
ships without media: upload your own images/videos and write your prompts. Missing models are listed in the
missing-models popup with download buttons.

```
H3MD Director --plan--> H3MD Loop Sampler (+ H3MD Loop Options --options-->)
H3MD Loop Sampler --long video--> H3MD Save Video Interp
```

Run only the first segment first (`stop_after = 1`) to check the look and the speed, then set it back to 0 (segment 1
comes from the cache).

### Re-render only the bad segments (`rerun_segments`)
When a few segments of a finished video are bad (jumps, glitches), do not re-render the whole video:
1. Keep `resume_from_cache` on and the other settings unchanged; reference pictures and prompts may change (**keep the
   total length and the reference pictures' pixel size**, otherwise the automatic split changes).
2. In `rerun_segments` (top of the Loop node) list the segments, 1-based as in the log ("segment 8/16"): `3`, `4-6`,
   `8-10, 13-14`.
3. Each block starts from the end of the segment before it; its last 39 frames are locked to the start of the cached
   segment after it (high-res, low-res and audio), so that segment and everything after it stay untouched. No tail lock
   for the last segment or before a hard cut. Select neighbouring bad segments together (e.g. 8-10); the locked end
   must be a good one.
4. All other segments come from the cache and the output is still the whole video. Old files of re-rendered segments
   are moved to `_replaced_<time>` inside the cache folder.
5. `rerun_new_seed` (default on): re-rendered segments get a new random seed.
The log lists every segment as "cache" or "RERUN (head/tail)" first; a missing cached segment stops the run with a list.

### Long video getting more saturated / sharper / older-looking?
This is common to every chained H3 continuation; it can be held down, not removed. Measured over 16 segments:
saturation 0.32 -> 0.46 and rising sharpness. The colour drift is born in the **low-res pass** (the model continuing its
own result) as a slow shift of the 24 latent channels' mean and spread.
* One continuous scene (motion copy, a long take): set `lowres_anchor` (last widget of `H3MD Loop Options`) to 1. After
  each continued segment's low-res pass, before the upscale, the new frames' channel statistics are pulled back to the
  shot's first segment, and the low-res carry is corrected too, so the error does not reach the next segment. Needs
  `continue_lowres_prefix = native_lowres_carry`. Not for a story whose colours should change (a hard cut starts a new
  reference).
* Also: give every segment the reference photos; avoid over-saturated / over-sharp reference photos; avoid lighting
  adjectives (glowing, radiant ...) in prompts; restart very long pieces from the reference photos at natural cuts.

### Motion copy (depth) rules
* A video with depth ticked **drives only the depth ControlNet and is not a `<Video N>` reference** (automatic, no
  switch); the Director gives it no `<Video N>` number. Reason: a black-background silhouette as `<Video N>` was copied
  into the picture (black / grey studio). A depth video with its soundtrack on stays a `<Video N>` (warning).
* Depth ticked without `H3MD Depth Control` connected: error before the run.
* **No plain / grey studio backgrounds in reference photos** (measured: with no `<Video>` reference a grey-backdrop
  photo still pulled the video grey). Use real-location photos or cut the person onto a blurred target location; keep
  the floor out of full-body photos and describe one plain floor in the prompt.

### Using the main features
**`H3MD Director`**
* `plan_json` is the single source of truth: the editor reads it and writes every edit back. Editor / JSON toggle top right.
* Asset bin: upload or pick files from `input/`; drag an item onto a shot to assign it.
* Shot lengths are 5+17n frames at 24 fps; a shot with a reference video follows the clip. Width and height snap to
  multiples of 64, so the low-res pass is exactly half size.
* Type `@` or click a tag chip to insert a media tag (`<Picture N>` / `<Video N>` / `<Audio N>`), in backend order.
* **Check what each `<Picture N>` shows before writing its role** (the tag chips show a thumbnail): the person, the
  outfit and the location must each point to the picture that really contains it. Calling an empty scene picture
  "the person" makes the video jump to another background or outfit midway. See `skills/` (EN / 中文).

**`H3MD Loop Sampler` + `H3MD Loop Options`**
* Main node: models, sampler, sigmas, seed, cfg, `transition_step` (low-res steps), `lowres_scale`, `rho` / `w_min` /
  `w_max`, `upscaler_model`, `seed_mode` (default increment: segment k uses seed + k), `highres_tiling`,
  `rerun_segments` / `stop_after` / `rerun_new_seed` (top of the node), `cache_mode`, `resume_from_cache`.
* Advanced switches live in `H3MD Loop Options`, linked to `options`; not linked = defaults. The node shows the common
  options; tick "advanced" to see all.
* Outputs: `latent`, `images` (64x64 placeholder), `audio`, `frame_count`, `summary` (per segment: seed, tiling plan,
  step times), `long video`.
* **Connect `long video` to `H3MD Save Video Interp`** — the only save path designed for bounded VRAM/RAM.

**Disk cache**
* Folders: `resume_<hash>` (stable with `resume_from_cache` on) or `run_<time>_<pid>`.
* Location: with `cache_dir` empty it is chosen automatically (env `H3MD_CACHE_DEFAULT` → an existing `H3MD_cache` →
  the local drive with the most free space).
* The Loop never deletes its cache before the video is saved; use `delete_cache_after_save` in
  `H3MD Save Video Interp`. A 243-frame 1088x1920 segment takes about 3 GB (fp16).

**`H3MD Save Video Interp`**
* H3 is fixed at 24 fps. With `interpolate` on, RIFE goes from 24 to `target_fps`; with it off and `target_fps` ≠ 24
  the clip plays faster/slower and the audio no longer matches (logged).
* `format`: `video/h264-mp4` (default), `video/h265-mp4`, `image/gif`, `image/webp`, `image/png-sequence` (the last
  three have no audio).
* Chunk size follows free VRAM/RAM; on CUDA OOM the chunk is halved and retried, finally on the CPU.

## Low VRAM

* **High-res pass**: keep `highres_tiling` on. It picks the strip count from the free VRAM (up to 12) and runs the full
  frame whenever it fits, so it costs nothing on large cards. On CUDA OOM it retries with more strips, then with
  feed-forward chunking, and only then stops with a readable error.
* **Low-res pass**: not tiled. On OOM the error suggests `ff_chunk_rows = 8192` in H3MD Loop Options (feed-forward in
  row chunks: same result, slightly slower).
* **Reference media do not shrink with more strips**: reference images / videos, audio and text are whole in every
  strip. Measured at 1088x1920 with 2 pictures + 1 reference video: ~37k fixed rows per strip. On **cards under 16 GB
  use a lower resolution** (e.g. 768x1344 or smaller), shorter segments and fewer / smaller reference media.
* **Model weights** are left to ComfyUI's memory management (DynamicVRAM, `--lowvram`, `--reserve-vram N`);
  H3MD LowVRAM LoRA Loader loads LoRAs in token chunks.
* `unload_idle_models` (default on) unloads text encoder / VAEs between the passes and between segments;
  `vram_release = deep` also clears caches between segments.
* The estimate is calibrated on an RTX 5090 32 GB; after the first OOM on your machine it is recorded in
  `user/h3md/tiling_calibration.json` and later runs plan more strips automatically.

## Models manifest

the example workflow has a top-level `models` array (ComfyUI workflow spec,
<https://docs.comfy.org/specs/workflow_json>): `name`, `url`, `directory` (required), `hash` / `hash_type` (optional).
When a listed file is missing from `models/<directory>/`, the frontend's missing-models popup offers a download button.
Limits: Hugging Face / Civitai URLs only; extensions `.safetensors` / `.sft` / `.ckpt` / `.pth` / `.pt` only;
`directory` must be a registered models folder; **`name` must match the node's widget value exactly**, otherwise that
entry is not shown.

| name | directory | source |
|---|---|---|
| `minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors` | `diffusion_models` | huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models |
| `Huihui-Qwen3-VL-4B-Instruct-abliterated-int8_convrot.safetensors` | `text_encoders` | huggingface.co/ahmed22xa/Huihui-Qwen3-VL-4B-Instruct-abliterated-comfy |
| `minimax_h3_video_vae_fp16.safetensors` | `vae` | huggingface.co/Comfy-Org/MiniMax-H3 |
| `minimax_h3_audio_vae_fp32.safetensors` | `vae` | huggingface.co/Comfy-Org/MiniMax-H3 |
| `mmh3-4b-ClipProj-v3.1-mlp.safetensors` | `clip_projections` | huggingface.co/NicoLab28/ClipProj-MiniMax-H3 |
| `h3_upscaler_sharpness_2000steps_v0.1_fp32.safetensors` | `latent_upscale_models` | huggingface.co/Alissonerdx/Minimax-H3-ComfyUI |
| `H3_Combat_V2.safetensors` | `loras` | huggingface.co/JOKER141/MiniMax-H3-Combat-Base-V2 |
| `minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors` | `loras` | huggingface.co/Kijai/MiniMax-H3-experimental |
| `minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors` | `loras` | huggingface.co/lightx2v/Minimax-h3-Turbo |
| `minimax_h3_lms_v1.0_r64_r.safetensors` | `loras` | huggingface.co/RunningHubAI/rh-minimax-h3-lms-v1.0-r64-lora |

Each model has its own license; check its model page before use. The `ClipProjApply` entry only shows when ComfyUI-ClipProj is installed.

## RIFE model (manual download)

the missing-models popup only downloads into registered `models/` folders and does not allow `.pkl`, so RIFE `flownet.pkl` is not in the manifest. Download it manually: node pack ComfyUI-VFI (<https://github.com/GACLove/ComfyUI-VFI>); model <https://huggingface.co/hzwer/RIFE/resolve/main/RIFEv4.26_0921.zip> — extract `flownet.pkl` to `custom_nodes/ComfyUI-VFI/rife/train_log/flownet.pkl` (relative to the ComfyUI root). ComfyUI-VFI normally downloads it itself on first use.

## Caveats

* For large / long videos the `latent` / externally decoded images / `audio` paths do **not** guarantee against
  VRAM/RAM blowups; a one-pass external decode can also differ slightly at the seams. Only `long video` →
  `H3MD Save Video Interp` is designed for bounded VRAM/RAM.
* The `images` output is a 64x64 placeholder (except with `cache_mode = ram`).
* Old workflows: removed options (`merge_images`; Save Video Interp's `preset` / `cache_folder` / `pingpong` / `fps`
  output, etc.) are re-aligned by name and dropped on load, nothing else shifts; `H3MD Loop Sampler (legacy)` is kept
  so old saves load.
* `video/h265-mp4` and 10-bit (`yuv420p10le`) are **untested**.
* The `resume_from_cache` key does not cover models, LoRAs or the upscaler; after changing them turn resume off or
  clear the cache folder.
* This version changed the sampling engine: older caches are not resumed, and an old workflow's `H3MD Loop Options`
  node should be deleted and re-added (its options changed).
* **Text encoder**: 32B (`qwen3vl_32b_minimax_h3_int8_convrot`) follows prompts best. Short on VRAM? Use 4B + ClipProj:
  it saves 10+ GB with no loss in image quality, only slightly weaker prompt understanding (details can differ a little
  from 32B). Fine for generating from scratch. **Keep one encoder for a whole piece**; do not switch when re-rendering
  segments with `rerun_segments`.
* **Fast-motion ghosting**: a fast arm swing can briefly show ghost hands (4 frames are packed into 1 latent step, plus
  few sampling steps). Re-render that segment with a new seed via `rerun_segments`.
* A motion-copy plan is aligned to the reference video (rounded up to 5+17n); trim the frames past the reference video
  after export.

## How H3MD works (ideas you can reuse)

MiniMax H3 makes at most ten-odd seconds per run. For a 2–3 minute video the hard part is not "can the clips join" but
keeping a dozen joined segments **seamless, drift-free, within VRAM, and repairable**. These are the problems we hit and
how we solved them — feel free to build on them.

### 1. Continue in latent space, not pixels
- **Problem**: decoding the previous clip and re-encoding it as the next reference softens and shifts color a little at
  every join.
- **Approach**: the next segment starts with the **last 39 frames of the previous segment's final latent** (held by
  noise mask 0); only the first few frames are lightly re-noised during sampling and restored exactly at the end.
  Overlaps are merged from the later segment.

### 2. Two-pass sampling with a native low-res carry
- Half resolution for 6 steps, a learned latent upscaler, then 3 steps at full resolution (engine from
  Luna-H3-Sampler).
- **Native low-res carry**: the next segment's low-res head is the previous segment's **own low-res result**, not a
  downscale of the high-res output, so both chains stay continuous.

### 3. Automatic splitting and tiling by VRAM
- Every shot is estimated in tokens (canvas + reference videos + pictures); shots over the budget are split into equal
  continuation parts, with cut points moved to quiet moments of the reference video.
- The high-res pass runs the whole frame when it fits and the fewest strips when it does not; on OOM it retries with
  more strips and remembers the machine.
- Slow-step guard: a step 4× slower than usual means VRAM ran out (weights streaming), so the run stops with the cache
  kept.

### 4. Disk cache, resume, and re-rendering only the bad segments
- Each segment is decoded and written to disk right away; only one segment is in RAM. A re-queue skips finished
  segments.
- **Re-render selected segments**: a selected segment's head continues the segment before it and its **tail is locked
  to the next segment's old head** (high-res latent, low-res result and audio), so the replacement is seamless at both
  ends and nothing else changes. No 5-hour rerun for one glitch.

### 5. Motion copy: depth drives only the ControlNet
- Reference video → Depth Anything V2 depth → SAM3 keeps only the person → Fun ControlNet, **on the low-res pass
  only**.
- **Lesson from testing**: feeding the white-on-black silhouette as a `<Video N>` reference as well made the model copy
  the black background or a grey studio into the picture. Depth video now drives only the ControlNet.

### 6. What reference photos leak into the video (measured)
- Grey / plain studio backgrounds → the video drifts into a grey studio. Fix: cut the person out onto a blurred version
  of the target location.
- A wide toothy grin in the photo → a frozen grin in every frame. Fix: say "identity from the photo, not the
  expression".
- Wooden floors, tiles, mirrors → a different pattern in every segment. Fix: describe one plain floor.

### 7. Long-video drift: pull the low-res pass back per channel (`lowres_anchor`)
- **Problem**: segment after segment, colors deepen, sharpness grows and faces look older. Every chained H3 continuation
  method in the community shows this.
- **Diagnosis**: measuring the low-res and final latents of a 16-segment cache separately showed the color drift is
  **born in the low-res pass** (saturation 0.39 → 0.58); the high-res pass only adds a constant offset. The drift is a
  slow shift of the 24 latent channels' mean and spread.
- **Fix**: after each low-res pass, before the upscale, the new frames' per-channel mean and spread are pulled back to
  the **first segment of the shot**, and the low-res carry is corrected too, so the error never reaches the next segment.
- **Result**: over a 133 s take saturation stays at 0.33–0.36 (0.34 → 0.48 without it); sharpness stops climbing and
  faces stop aging.
- **Dead end we took first**: grading new frames to the previous segment's held frames did nothing — those frames had
  already drifted. **The anchor must be a source that has not drifted.**

### 8. Text encoder: 32B vs 4B + ClipProj
- 4B + ClipProj saves 10+ GB of VRAM with no loss in image quality, only slightly weaker prompt understanding. Fine for
  generating from scratch; don't switch encoders in the middle of one piece.

### 9. Method
- **Search the community before testing**: a GPU runs only a few tests a day.
- **Measure**: per-segment saturation and Laplacian sharpness plus face crops, not "it feels like".
- **Analyze the cache offline**: it holds every segment's low-res and final latent, so you can find the guilty stage
  without using the GPU.

### Thanks 👍
H3MD stands on many shoulders. Their code, models or openly shared findings gave us direct ideas:

**Engine, models and nodes**
- 👍 [MiniMax](https://huggingface.co/MiniMaxAI) for open-sourcing H3, and [Comfy-Org](https://huggingface.co/Comfy-Org/MiniMax-H3) for native ComfyUI support
- 👍 [Luna-H3-Sampler](https://github.com/lunaaispace-eng/Luna-H3-Sampler): two-pass sampling engine (bundled, GPL-3.0)
- 👍 [LBH-123-AI](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler): learned latent upscaler (MIT)
- 👍 [Alissonerdx](https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI): sharpness upscaler and lms LoRA
- 👍 [nkxx188/ComfyUI-MiniMaxH3-Easy](https://github.com/nkxx188/ComfyUI-MiniMaxH3-Easy): high-res strip tiling idea (MIT)
- 👍 [Kijai](https://github.com/kijai/ComfyUI-KJNodes): SageAttention / feed-forward VRAM patches, DMAD conversion
- 👍 [NicoLab28/ClipProj](https://github.com/nicolab28/ComfyUI-ClipProj): 4B encoder projection for small GPUs
- 👍 [alibaba-pai Fun ControlNet](https://huggingface.co/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union), [Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2), [SAM3](https://huggingface.co/Comfy-Org/sam3.1): the three pillars of motion copy
- 👍 [smhfacct](https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models) (fl2va × ref2va hybrid), [t8star](https://huggingface.co/t8star) (curveproj-compatible LoRAs), [lightx2v](https://huggingface.co/lightx2v/Minimax-h3-Turbo) (Turbo LoRA), [DMAD](https://huggingface.co/ZhengmingYu/DMAD), [fal](https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA) (realism people), [JOKER141](https://huggingface.co/JOKER141/MiniMax-H3-Combat-Base-V2) (Combat LoRA)

**Long-video drift: people who shared their findings**
- 👍 [tritant/ComfyUI_MiniMax_H3_Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender): documented "contrast rises at every extension" and that reference images carry color in
- 👍 [NikoDemon80/ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context): latent continuation instead of pixel round trips, and "less sharpening in the last step"
- 👍 [mark9009/ComfyUI-H3-LongTake](https://github.com/mark9009/ComfyUI-H3-LongTake): measured keyframe anchoring and seam color matching
- 👍 [EchoHeadache/comfyui-aex-drift](https://github.com/EchoHeadache/comfyui-aex-drift): handoff matching and seam anchoring. We moved the anchor to the first segment, but it is what made us see that the anchor must not drift
- 👍 [roycho87/clown_context_suite](https://github.com/roycho87/clown_context_suite): periodic clean anchors
- 👍 The author of the [10-link chaining measurement](https://dev.to/nefayran/chaining-image-to-video-clips-from-the-last-frame-what-10-links-did-to-a-face-minimax-h3-46e0) on dev.to, who put numbers on aging faces and sharpness overshoot

### Special thanks 👍
t8star, JOKER141 (Bunny), TE MAN and many other creators, for their long-running free tutorials for the community — outstanding work and generously shared ideas and experience. Hats off to them!

Thank you for sharing what you learned. H3MD will keep sharing its findings back with the community.


## License

ComfyUI-H3MD is Copyright (C) 2026 YJ and licensed under the GNU Affero General Public License v3.0 or
later (SPDX `AGPL-3.0-or-later`) with one additional term: keep a visible attribution "Based on H3MD Director (ComfyUI-H3MD-Director) by
YJ" when you distribute it or offer it as an online service. Commercial use is allowed; a modified version offered over
a network must publish its source. Full text in [LICENSE](LICENSE), details in [NOTICE.md](NOTICE.md); the bundled
Luna-H3-Sampler code in `h3md_luna/` is GPL-3.0 ([LICENSES/](LICENSES/)).

---

## Technical reference (English)

Detailed behaviour of every node and option (kept from the development notes).

Multi-segment MiniMax H3 pipeline nodes (category `MiniMax H3/H3MD`):

| Class | Display name | Purpose |
|---|---|---|
| H3MDDirector | H3MD Director | plan_json -> validated `H3MD_PLAN` (5+17n lengths, overlaps, ref windows, label order); built-in visual editor, see below |
| H3MDSegmentPick | H3MD Segment Pick | raw media of one segment (native-res ref video, single <=15 s window) |
| H3MDSegmentEncode | H3MD Segment Encode | calls ComfyUI core `MiniMaxH3ReferenceToVideo.execute` (official label order) |
| H3MDSegmentContinue | H3MD Segment Continue | final-latent-only handoff, nested noise_mask, H3MD drift (strength 0 = prefix kept unchanged), locked audio, VRAM release between segments |
| H3MDSegmentJoin | H3MD Segment Join | trims overlap frames/audio of the new segment and appends (optional crossfade) |
| H3MDSampler | H3MD Sampler (two-pass) | bundled Luna-H3-Sampler two-pass engine + drift, low-res head, VRAM release between the passes, high-res tiling, per-step log; outputs only the final latent |
| H3MDLoopSampler2 | H3MD Loop Sampler | main loop node: runs ALL segments (Encode -> Continue -> H3MD Sampler -> VRAM release) and joins them; advanced switches via `options` |
| H3MDLoopOptions | H3MD Loop Options | advanced Loop switches (Alpha/Beta prefix, handoff, seams, ref windows, tiling, cache details) -> `options` |
| H3MDLoopSampler | H3MD Loop Sampler (legacy) | the original single node, inputs unchanged, kept so saved workflows load exactly; same computation |
| H3MDSemanticBridge | H3MD Semantic Bridge | chainable Semantic Bridge (six-tensor MLP / trainer MLP / T8 transformer) -> Loop Sampler `semantic_bridge` |
| H3MDLowVRAMLoraLoader | H3MD LowVRAM LoRA Loader | token-sliced bypass LoRA loader (same behaviour as H3LowVRAMBypassLoraLoader) |

### Director editor (Phase 2 UI)
`H3MD Director` has a built-in visual editor (`web/js/h3md_director_ui.js`, logic in `web/js/h3md_plan.js`).
The `plan_json` widget is still the single source of truth: the editor reads it, and every edit writes
formatted JSON back into it. Workflows save as `widgets_values = [plan_json, width, height, continuity_prompt]` (older 3-value workflows
load with continuity_prompt = True),
and the editor widget itself is not serialized and not sent to the backend. Opening a workflow does not
rewrite `plan_json`; the text only changes when you edit something.

* **Editor / JSON toggle** (top right): `JSON` shows the original plain text box (fallback, hand editing).
  `Editor` re-reads the text. Invalid JSON keeps the text untouched and stays in JSON mode with the parse error.
  The mode is stored in the node property `h3md_ui_mode`.
* **Global**: plan width/height (the node's width/height widgets override them when > 0), globalPrompt,
  globalMode (fallback | prepend). fps is fixed at 24.
* **Media bins** (Images / Videos / Audios):
  * `Upload…` (or drop files from the OS onto a bin) stores the file through ComfyUI's `/upload/image` endpoint
    (type `input`, any file type), which puts it in the ComfyUI input folder.
  * The `file in input/` box lists existing input-folder files (type-ahead). `Add` or Enter adds the typed file.
  * Image thumbnails and video first frames come from `/view`. Video duration, size and audio track, and audio
    duration, come from `/h3md/probe`. For a new video, `duration` and `hasAudio` are filled in automatically.
  * The id field renames the item and updates all segment refs and `locked.source`. `✕` removes it from the
    plan and from the segments that use it; the file stays on disk. `♪` toggles `hasAudio`.
  * Drag a bin item onto a segment card to assign it.
* **Timeline bar**: every segment drawn to scale, with overlaps hatched. Click a block to jump to its card.
* **Shot tabs**: shots are a row of tabs (`Shot N`, its seconds and frames; `▶` = length from its video,
  `⇢` continuation, `✂` cut) plus `+`. One settings panel below edits the selected tab: move ◂/▸,
  duplicate ⧉, insert after +, delete ✕. Dropping a bin item on a tab assigns it to that shot. The saved
  plan format is unchanged (a plain `segments` list).
  * **Shot length follows its first reference video** (`<Video 1>`): the clip's duration in seconds from
    `trimStart` to its end (video-stream frames / its own fps, so 30 fps clips are fine; the Director
    re-probes the file), times 24, rounded UP to the next 5+17n: `5 + 17*ceil((ceil(sec*24) - 5) / 17)`.
    Example: a 3.3 s clip = 79.2 f -> 90 f (3.75 s); 3.88 s -> 107 f; 15.14 s -> 379 f. The length field
    is then read-only. Longer than `maxSegmentLength` -> auto-split as before (`ref_window_mode=advance`
    moves the <=15 s window through the clip). A shot without a video keeps its own length (rounded up).
  * **Continued shots (overlapIn > 0) get overlap + content**: the first `overlapIn` frames of a continued
    shot are the previous shot's tail and are trimmed at the join, so a video-driven continued shot is
    generated as `aligned_up(overlapIn + clip frames)` and its **new content is never shorter than the clip**
    (the timeline / total count only the new part once). If that is over the max length (396, or the shot's
    own `maxSegmentLength` with auto-split), the overlap is first shrunk through 5+17k values (39 -> 22, never
    below 22); still over -> auto-split splits the shot (keeping the requested overlap) **only if that is worth a
    split** (see "no sliver splits" below); with auto-split off
    the shot stays over 396 at overlap 22 and the editor warns ("turn on auto-split or trim the reference
    video"). The editor stores a shrunk overlap in `overlapIn` and the user's choice in `overlapRequest`
    (picking/dragging an overlap clears it). The reference window still starts at `trimStart`, i.e. it lines
    up with the first NEW frame (frame `overlapIn` of the shot). Hard cuts and shots without a video ref
    (manual length, which already includes the overlap) are unchanged. Example, six 15 s clips continued with
    overlap 39: shot 1 = 362 f; shots 2-6 = 396 f at overlap 22 (39 + 360 = 413 > 396) = 374 f / 15.58 s new
    each; total 2232 f = 93.0 s. Tabs show `+15.58s new · 396f`; the card shows generated and new length.
    Old saved plans load; their continued video shots are re-lengthened (a note says so) and re-render.
  * **No sliver splits** (auto-split on, video-driven shots, 2026-10-17e): a shot that does not fit the max even
    at overlap 22 (0 for a first shot / hard cut) is auto-split only when the clip runs at least **48 frames
    (2 s)** past what one capped shot covers (max - 22 new frames), and every split piece then carries at least
    48 new frames. Otherwise the shot is **capped at the max** (396 at overlap 22, or 396 for a cut) and the last
    frames of the clip are dropped, with a note ("capped at 396 frames (overlap 22), no auto-split: the clip is
    only 10 frames over ... dropped"); the editor shows it on the length line. Why 2 s: it is the shortest
    reference window H3 accepts (MIN_REF_SECONDS), and a split costs a whole extra generation pass plus a seam,
    which is not worth less motion than that. Examples (max 396): 16.07 s clip continued -> 396 f / 22, 15.58 s new,
    0.48 s dropped; 17.5 s -> capped (46 f over); 17.6 s -> split 2 x at 39 (48.4 f over); 20 s -> split.
    Manual lengths, auto-split off and per-shot max 0 are unchanged.
  * `length` in frames rounds up to 5+17n. `sec` converts seconds, rounded up to 5+17n.
  * `overlapIn` choices are the same fixed steps for **every** continued shot: **0 (hard cut), 22 or 39 frames**
    (only steps shorter than both lengths are offered). Timeline drag snaps to the nearest step. When 39 + the
    clip would exceed the max length the overlap is shrunk to 22 (shown as `39f → 22f (fits 396f)`; the choice
    stays 39). Older plans with other 5+17k overlaps (5, 56, 107 ...) still load and run as saved, with a
    warning; choosing a step replaces them. The Director accepts the same set (others: WARNING note).
    A click on a timeline block (or 1-2 px pointer jitter) never edits the plan, and dropping a shot where it
    already is keeps its choice (before 2026-10-17d a click on a shot whose 39 had been shrunk to 22 stored an
    explicit 22). The editor notes when a chosen 22 does not fit either (the shot is split anyway) and when a
    clip runs longer than its <=15 s window (the shot follows the whole clip).
  * `startFrame` is computed (previous start + length − overlapIn). It is only written back if the plan
    already had the key.
  * Images and audios are chips; the selected order is the label number (◂ ▸ reorders).
  * Each video has trimStart/dur (clamped to 2–15 s and to the source length) and a soundtrack checkbox.
  * audioMode is reference | locked | none. locked adds a source (video:id / audio:id) and an offset.
* **Label preview**: the exact `<Picture N>` / `<Audio N>` / `<Video N>` tags the backend will produce:
  images first, then each video's soundtrack `<Audio>` right before its `<Video>` (only if audioMode ≠ none,
  useSoundtrack and hasAudio), then standalone audios. Click a tag to insert it at the prompt cursor.
* **Validation**: the same checks as `parse_plan`, with the same message text and order, plus editor hints:
  * missing files
  * hasAudio/duration that disagree with the file (one-click `fix`)
  * a ref window longer than the segment
  * more refs than `H3MD Segment Pick` outputs
  * prompt tags that do not exist in the segment
  * refs not mentioned in the prompt (info only)

`h3md_plan.js` ports `parse_plan` / `compute_labels`. If you change the backend rules, change both files.
`tests/js/parity.py` checks them against each other.

#### Editor helper routes (read-only, `h3md_routes.py`)
* `GET /h3md/files?kind=image|video|audio`: media files under the input folder (recursive, relative paths).
  If this route is missing, the editor falls back to the LoadImage/LoadVideo/LoadAudio file lists.
* `GET /h3md/probe?file=<plan file>`: `exists`, `duration`, `hasAudio`, `width`, `height`, `fps`. Relative
  paths must stay inside the input folder.

### Shots, automatic sub-segments and the timeline (phase 3)
* `plan_json["segments"]` entries are **shots**. Shot-to-shot seams follow the overlap rule:
  `overlapIn > 0` = **continuation** (the new shot starts with the tail of the previous shot's FINAL
  two-stage latent, copied overlap is trimmed on join); `overlapIn == 0` = **hard cut** (generated
  independently, no previous latent, no trimming, joined end to end). No switch needed.
* Optional plan field `"maxSegmentLength"` (5+17n, e.g. 396) turns on **auto-split**: a shot longer than it is
  expanded by the Director into near-equal continuation sub-segments (all 5+17n, each <= max) joined by
  `"subOverlap"` frames (default 39), covering the shot length exactly (`1433f -> 396+396+379+379`).
  Per shot: `"maxSegmentLength": 0` never splits that shot; `"subPrompts": ["", "part 2 text"]` overrides the
  prompt of individual parts (empty = shot prompt). Sub-segments inherit the shot refs; locked audio offsets
  advance with each part. Without `maxSegmentLength` a plan parses exactly as before (tested against the
  phase 2 parser on 6000+ plans).
* Director `continuity_prompt` (default on): every continued segment (shot seam or auto part) gets a short
  continuity sentence prepended (`continuity_note()`); cuts and the first segment are untouched.
* Editor timeline: shots are draggable blocks on a ruler (24 fps). Drag a block onto the previous one =
  continuation (hatched overlap, label `continue Xf`, snapped to 5+17k); drag it to just touch = `cut`; a gap
  snaps back to touching. Drag the right edge = length (5+17n). Dashed lines/hatches inside a block = the
  automatic sub-segments. `+ Shot` appends a 10 s hard cut. `Split shot…` (manual option) replaces a shot by
  separate shots using the same split math, e.g. "60 s, max 396, overlap 39" -> 4 shots.

### Two-node layout (`h3md_loop_split.py`): H3MD Loop Sampler + H3MD Loop Options
* **H3MD Loop Sampler** (`H3MDLoopSampler2`): rerun_segments, stop_after, rerun_new_seed (top), plan, clip, vae, audio_vae, low_res_model, sampler, sigmas, seed, the
  sampler widgets (cfg, transition_step, lowres_scale, rho, w_min, w_max, upscaler_model), seed_mode, decode_output;
  optional high_res_model, highres_tiling, cache_mode, delete_cache,
  resume_from_cache, `semantic_bridge` (link), `options` (link, `H3MD_LOOP_OPTIONS`), `depth_control` (link).
* **H3MD Loop Options** (`H3MDLoopOptions`): ref_image_size, continue_audio, drift_strength, drift_lock_tokens,
  audio_crossfade_ms, release_vram_between_segments, tiling_axis, tiling_overlap, ff_chunk_rows, release_upscaler,
  unload_idle_models, cache_dtype, cache_dir, unload_models_before_merge, continue_lowres_prefix,
  merge_overlap_source, seam options, seam_decode_context, vram_release, slow_step_guard (+ any future non-main widget).
* `options` not connected = every advanced input at its widget default; the run (frames, latent, summary, cache
  key) is identical to the legacy node with the same values.
* **Legacy**: class id `H3MDLoopSampler`, labelled "H3MD Loop Sampler (legacy)". Since 2026-10-30 (start_index /
  end_index / previous_latent removed, rerun inputs added at the top) workflows saved before that date must be
  re-created or migrated (`widgets_values` are positional).
  Example: `example_workflows/H3MD alpha example.json`.

### H3MD Loop Sampler (`h3md_loop.py`)
Runs every segment of the plan (or segments 1..`stop_after`; `rerun_segments` re-renders only the listed ones with a
head + tail lock and takes the rest from the cache): Encode (official encoder, native-res
refs) -> Continue for continuation seams and locked audio (handoff = previous FINAL latent `samples` only, H3MD drift)
-> H3MD Sampler -> VRAM release between segments. Then joins everything (`latent`, and with `decode_output` the
frames / audio through the disk cache). `summary` is JSON with per-segment seam / trim / seed / tiling plan / step
times. `seed_mode` increment (default, seed + segment index) | same; with `resume_from_cache` the base seed stored in
the resume folder (`run.json`) is reused. Interrupt is checked between segments and decodes.

#### Loop outputs: what to connect / Loop 输出怎么接 (2026-10-19)

**中文**
* `latent`（拼好的音视频 latent，接缝处的重叠已去掉）可以接到外部节点做放大或自定义处理，例如
  `Separate AV Latent` → Hyper-VAE 或 VAE 解码器 → VHS Video Combine 或其他节点。
* `audio`（整段拼好的音频，和 H3MD Save Video Interp 写进文件的是同一段）也可以接到外部节点自定义处理。
* `images` 只是一张 64x64 占位图（`merge_images` 已删除，Loop 不再把整段视频拼进内存）。要拿到画面帧，
  请在外部解码 `latent`。（`cache_mode=ram` 例外：`images` 仍是内存里的整段视频。）
* **警告**：大/长视频走 `latent` / 解码出来的图片 / `audio` 这些路径，**不保证**显存/内存不爆——在外部把整段
  `latent` 解码，会把整段视频放进显存/内存。外部一次解码整段 latent，接缝处也可能和 `long video` 出的片子略有不同
  （`long video` 用的是逐段解码 + 接缝处理后的帧）。
* 只有 **`long video` → H3MD Save Video Interp** 是按有限显存/内存设计的（从磁盘缓存一段一段流式处理）。

**English**
* `latent` (the joined audio-video latent, seam overlaps removed) can be connected externally for upscaling or
  custom processing, e.g. `Separate AV Latent` -> Hyper-VAE or a VAE decoder -> VHS Video Combine or other nodes.
* `audio` (the joined audio of the whole clip - the same audio H3MD Save Video Interp writes) can be connected
  externally for custom processing.
* `images` is only a 64x64 placeholder (`merge_images` was removed; the Loop no longer joins the clip in RAM).
  To get frames, decode `latent` externally. (`cache_mode=ram` is the exception: `images` is still the whole clip
  in RAM.)
* **Warning:** for large / long videos the latent / decoded-image / audio paths do **not** guarantee against
  VRAM/RAM blowups - decoding the full latent externally holds the whole clip in memory. A one-pass external decode
  of the joined latent can also differ slightly at the seams from the `long video` result (which uses the
  per-segment decoded frames with the seam handling).
* Only **`long video` -> H3MD Save Video Interp** is designed to stay within bounded VRAM/RAM (streamed chunk by
  chunk from the disk cache).
* The same text is in the Loop nodes' DESCRIPTION and output tooltips (`OUTPUT_TOOLTIPS`, hover an output dot), and
  as a Note node in `example_workflows/H3MD alpha example.json`.

**RIFE 模型（手动下载） / RIFE model (manual download)**
* 中文：ComfyUI 的“缺少模型”下载弹窗只能下载到 `models/` 下已注册的文件夹，而且不允许 `.pkl` 文件，所以 RIFE 的
  `flownet.pkl` 不放进工作流的 `models` 清单，需要手动下载：节点包 ComfyUI-VFI
  (<https://github.com/GACLove/ComfyUI-VFI>)；模型 <https://huggingface.co/hzwer/RIFE/resolve/main/RIFEv4.26_0921.zip>，
  解压后把 `flownet.pkl` 放到 `custom_nodes/ComfyUI-VFI/rife/train_log/flownet.pkl`（相对 ComfyUI 根目录）。
  ComfyUI-VFI 第一次运行时通常也会自己下载这个 zip。
* English: the frontend's missing-models popup only downloads into registered `models/` folder keys and only
  `.safetensors/.sft/.ckpt/.pth/.pt` files, so RIFE `flownet.pkl` is not in the workflow `models` manifest. Download
  it manually: node pack ComfyUI-VFI (<https://github.com/GACLove/ComfyUI-VFI>); model
  <https://huggingface.co/hzwer/RIFE/resolve/main/RIFEv4.26_0921.zip> - extract `flownet.pkl` to
  `custom_nodes/ComfyUI-VFI/rife/train_log/flownet.pkl` (relative to the ComfyUI root). ComfyUI-VFI normally
  downloads this zip itself on first use.

#### Per-segment disk cache (`h3md_cache.py`, default `cache_mode = disk`)

With `decode_output` on, each segment is decoded **right after it is sampled** and written to
`<cache root>/run_<time>_<pid>_<n>/`. The cache root is `cache_dir` when set; when empty it is picked automatically
(no hard-coded drive): (1) the `H3MD_CACHE_DEFAULT` environment variable if set; (2) an existing `<drive>/H3MD_cache`
that already holds `resume_*` folders (newest wins), so `resume_from_cache` finds its folder after a ComfyUI restart;
(3) otherwise `<drive>/H3MD_cache` on the local drive with the most free space (Windows: fixed drives only, removable /
network / optical skipped; Linux/macOS: home and real local mounts such as `/mnt/*`, `/media/*`, `/Volumes/*`), created on
demand. The chosen path and its free GB are logged. If nothing is writable it falls back to `<ComfyUI temp>/h3md_cache`
with a warning (temp may be moved or deleted on restart, so resume may not find it there). Files written:
`seg_###_frames.npy` (fp16, or uint8 with `cache_dtype`, written chunk-wise via a memmap),
`seg_###_audio.pt`, `seg_###_latent.pt` (the final two-stage latent), and `seg_###.json` (written last; it marks
the segment as complete and logs the sizes). The decoded tensors are freed (del + gc + soft_empty_cache) before the next
segment, so RAM holds one decoded segment at a time. Between segments the base model stays loaded (VRAM policy unchanged).

After the last segment, `unload_models_before_merge` (default on) calls `unload_all_models()` + `soft_empty_cache()`
(sampling is done, base UNet included). Since 2026-10-18 (late) the frames are **not** read back into an IMAGE any more:
`images` is a 64x64 placeholder and the `long video` output hands the cache + merge plan to H3MD Save Video Interp,
which reads them memory-mapped chunk by chunk (see below). The merge rules are unchanged: continuations drop their
overlap frames, cuts drop nothing, and the first segment in range is never trimmed (as in RAM mode).
Audio is joined with `join_audio()` (byte-identical to the audio part of `join_segments`, crossfade included) and is
still output on `audio`.

* `delete_cache`: hidden and ignored since 2026-10-18 (the Loop never deletes its cache). The run folder is removed
  only by H3MD Save Video Interp `delete_cache_after_save`, after the video file is complete. On any error or
  interrupt the cache is **kept** and its path is logged.
* `resume_from_cache` (default off, opt-in) uses a stable folder `resume_<settings hash>` and skips every segment whose
  key matches. The key covers the segment (prompt, refs, windows, length/overlap, media file size+mtime), its seed, the
  scalar sampler/continue settings, sigmas, and, for continuations, the key of the previous segment. Models, LoRAs and
  the upscaler are **not** part of the key, so after changing them turn resume off or clear the folder. To resume after
  a crash, the earlier run must have had `resume_from_cache` on (crash = cache kept). Delete the folder by hand when done,
  or let H3MD Save Video Interp with `delete_cache_after_save` on remove it.
  * **Per-segment keys**: the folder name and the keys of first / hard-cut segments ignore the options that only
    change continued segments (audio/drift, `continue_lowres_prefix`,
    `merge_overlap_source`/`ref_window_mode`/`seam_tone_*`), so A/B tests of those options resume the unchanged first
    segment. Continued segments still use the full settings + the previous segment's key. A hard cut with locked
    audio uses the full settings too. Beta
    (`native_lowres_carry`) re-renders a cached cut segment that was written without its low-res carry. Caches
    written by older versions use the old folder name and are not resumed.
* Disk use per 396-frame segment at 1472x1088: about 3.8 GB fp16 or 1.9 GB uint8. `cache_mode = ram` restores the old
  behaviour (decode + join in RAM after all segments).

#### Continued segments: low-res overlap prefix (`continue_lowres_prefix`, default `native_lowres_carry`)

**Default since 2026-10-18: `native_lowres_carry`** (best in the T01 speckle tests; `pixel_reencode` = previous
default, still available).  Saved workflows keep the value stored in their widgets - switch old nodes by hand.

Without a low-res head the engine builds the low-resolution pass's kept prefix by resizing the full-res final latent in latent
space; for H3 (16x VAE) that is not a valid low-res latent, decodes washed-out/ghosted, and the low-res stage
continued that look for the whole new segment.  With `pixel_reencode` the loop decodes the overlap frames of
the previous FINAL latent (disk mode reuses the already-decoded frames), downscales them in pixel space and
VAE-encodes them; only the low-res stage's kept anchor is replaced, the high-res anchor and the output prefix
stay the exact previous latent.  Log: `[H3MD Loop] ... low-res overlap prefix re-encoded ...` and
`[H3MD Sampler] low-res pass: kept prefix ...`.  `latent_resize` = the engine's own resize (A/B).

**`native_lowres_carry` (default; was BETA).**  The loop also captures each segment's low-res pass endpoint (the engine's `low_res_latent`,
VAE-native space, native low-res grid).  A continued segment pins its low-res overlap prefix from the tail
of that carry (no resize; tail must sit on the 5-token grid, 22 f = 7 tokens) and its high-res prefix from the
previous final latent as before.  Stored in the disk cache as `seg_###_lowres.pt` (~18 MB per 396 f) and reused by
resume; the mode is part of the cache key.  No carry / grid mismatch -> warning and
fallback to `pixel_reencode`.  Log: `[H3MD Loop] segment N: low-res overlap prefix = native low-res carry (1, 24, 7, h, w)`.

#### Seams and reference windows (`h3md_seam.py`, `h3md_refwindow.py`; last five Loop Sampler inputs)
* **`merge_overlap_source`** (default `next`; `prev` = old behaviour).  At a continuation join both segments contain
  the overlap frames.  The H3 video VAE decodes in 7-token windows (5-token stride) and blends each window with the
  next, so a clip's last ~5 frames are decoded without following context (xmarre, CONTINUUM_DECODE_CONTEXT.md -
  idea only).  `next` keeps the NEW segment's decoded copy (it has the following frames as context) and drops the
  previous segment's last `overlap` frames instead of the new segment's first.  Frame count, audio and the joined
  latent are identical in both modes; hard cuts are joined end to end as before (a cut segment that is followed by
  a continuation gives up its tail instead).  RAM and disk-cache merges behave the same; only the merge changes, so
  it is not part of the cache key.
* **`ref_window_mode`** (default `static` = old behaviour).  `advance`: for automatic sub-segments of a long shot,
  each sub-segment's reference video window starts at `trimStart + (sub.startFrame - shot.startFrame - shot.overlapIn)/24`
  (>= 0), computed from the shot's first new frame and snapped to the 24 fps grid; the video's soundtrack follows the same window, standalone
  reference audios start at the same offset (Director hook `audioStarts`).  Still one window per reference, <= 15 s,
  loaded at native resolution (never resized to the canvas).  If the source is too short for the advanced window
  (or its duration cannot be read) that reference stays static with a warning.  Log:
  `[H3MD RefWindow] segment 2 (shot 1.2/2): video 'v1' window 0.000+15.000s -> 4.958+15.000s ...`; the summary
  carries `ref_window` per sub-segment.
* **`seam_tone_match`** `off` (default) | `luma` | `color`, `seam_tone_strength` (0-1, default 1),
  `seam_tone_global` (0-1, default 0).  After decode, at continuation joins only: the two copies of the overlap are
  compared content-aligned over the <= 12 frames nearest the visible seam on a heavily blurred 16x16 grid (per-cell
  median -> robust to motion).  `strength x delta`, clamped to +-0.04 per channel, is added to the new segment at the
  seam and eases out (raised cosine) over 18 frames, to 0 or to an optional whole-segment luma offset
  (`global x delta_luma`, clamped to +-0.015).  Pixels stay in [0,1]; latents and audio are untouched.  Disk mode
  writes the corrected frames (the low-res prefix still uses the uncorrected decode) and resume re-reads the cached
  tail.  Log: `[H3MD Seam] segment N: seam_tone_match luma window 12f ... measured dRGB [...] dY ... -> offset ...;
  residual ...`; the summary carries `tone` per corrected segment.
  * The overlap prefix is restored exactly (drift), so the overlap copies are the same latent and that comparison is always ~0.
    There the visible step is where the kept frames end and the generated frames begin (frame `overlapIn` of the new
    segment): the last 6 kept frames are compared with the first 6 generated frames of the same decode (same grid,
    per-cell median), minus the change the kept window's own trend predicts (bounded), and the offset is applied to
    the generated frames only (full at the first generated frame, easing out over 18 frames to the optional global
    offset). Log: `seam_tone_match luma (static_lock boundary, frame 22, window 6f) ...`.
* Defaults leave the cache key unchanged, so caches written before these options still resume.

#### Semantic Bridge (`h3md_semantic_bridge.py`; node `H3MD Semantic Bridge` + Loop Sampler input `semantic_bridge`)
External, chainable like LoRA loaders:
`H3MD Semantic Bridge (A) -> bridge_chain -> H3MD Semantic Bridge (B) -> ... -> H3MD Loop Sampler.semantic_bridge`.
Not connected = off: behaviour and segment-cache key unchanged.
* Node inputs: **`bridge`** (any supported file in `models/semantic_bridge`, recursive, incl. `t8_compat/`),
  **`strength_mode`** `auto` (default) | `manual`, **`strength`** (alpha, default 0.10, used in manual),
  optional **`bridge_chain`** (`H3MD_SEMANTIC_BRIDGE`). Output: the ordered chain (max 8; the same file content
  twice is refused).
* `auto` strength: the file's fixed `application_contract` (T8 comic-combat: 1.0), the T8 author preset pinned to
  wushu v1's SHA-256 (0.12), 0.10 for six-tensor MLP bridges; a transformer bridge with neither needs `manual`.
  `manual` on a file with a fixed contract must equal the contract, else the node errors.
* What it does: right after Segment Encode, before Continue, on EVERY segment (first, continuation, hard cut),
  the positive text conditioning `h [B,T,5120]` goes through the chain in order (earlier node first). Each stage:
  `pred = net(h/rms(h))`, rescaled per token to `rms(h)`, `h = h + alpha*(pred-h)`, cast back to the conditioning
  dtype; the next stage gets that output. Serial, order matters, not an additive LoRA merge. Negative, latents,
  audio untouched. Conditioning already carrying a bridge tag is refused.
* Layouts (strict key/shape check; anything else is not listed / errors):
  `legacy_mlp` six-tensor fc1/fc2/fc3 5120-512-512-5120 (Original v1, BUNNY; the original formula, eps 1e-8),
  `trainer_mlp` (net.fc*, arch=mlp, optional residual), `trans` (net.in_proj + pre-norm GELU Transformer encoder
  + LayerNorm + out_proj, sinusoidal positions, optional residual; whole sequence = cross-token; T8 comic-combat,
  wushu v1).
* Parity: `trans` single and chained = bit-identical to T8 1.97.0 (`apply_bridge` / `BridgeStack`) with the real
  comic-combat and wushu files; six-tensor MLP = bit-identical to the original six-tensor package, and differs from T8's
  implementation only by bf16 rounding (T8 uses eps 1e-6 and 256-token chunks; mean |diff| ~5e-8).
* CPU fp32 only, no VRAM: about 10-60 ms per stage per segment (200-1200 tokens). All stages are loaded and
  checked on the node; the Loop re-checks the files' SHA-256 before the first segment and stops if one changed.
* Log: `[H3MD SemanticBridge] chain: a (trans, alpha 1) -> b (legacy_mlp, alpha 0.1)` on the node; on the Loop
  `[H3MD SemanticBridge] ON (2 bridges, CPU): ...` once and per segment
  `[H3MD SemanticBridge] segment N/M: 2 bridge(s) applied to 1 tensor(s), T token(s); cos(native, bridged) 0.97..`;
  summary `semantic_bridge` per segment; `... NOT applied ...` = no 5120-dim tensor found.

### H3MD drift (own implementation)
Per sampler step, for overlap-prefix tokens that the static mask keeps (mask 0):
`mask = ramp_weight(k) * sigma_next / sigma_current`, ramp_weight = drift_strength * (n-k)/n for
k < n = prefix_tokens - drift_lock_tokens, 0 otherwise. The `drift_lock_tokens` tokens nearest the seam stay
0; audio masks stay static; generated tokens are never touched. At the last step sigma_next = 0, so the
prefix ends fully kept (and the samplers restore it exactly).

### High-res tiling (`h3md_tiled_hires.py`) and feed-forward chunking (`h3md_ffchunk.py`)
With `highres_tiling` on (default) the high-res pass of every segment runs through an OUTER_SAMPLE wrapper on the
high-res model. `plan_tiles` measures the workspace (free + reclaimable VRAM - weights - ComfyUI's reserve) and picks
the fewest strips (1..12, along `tiling_axis`, overlap `tiling_overlap` latent px) whose calibrated estimate fits;
1 strip = the plain full-frame pass, untouched. N > 1 strips = the **joint** method: one full-frame sampling run, every
model evaluation split into the strips (DIFFUSION_MODEL wrapper) and the video predictions feather-blended over the
overlaps before the sampler step; the continuation mask stays the sampler's own full-frame denoise mask; keyframe
latents are cropped, refs / text / audio stay whole, RoPE positions are the full frame's; the audio part of the mask
is set to 0, so audio stays at the clean audio handed over from the low-res pass. ComfyUI reserves memory for the
largest strip (PREPARE_SAMPLING wrapper). Log tag `[H3MD Tiling]`.

Out-of-memory ladder: CUDA OOM in the high-res pass -> free memory -> re-plan with more strips (the low-res result is
kept) -> ... -> the most strips -> + feed-forward chunking (`OOM_RETRY_ROWS` 8192) -> `HighResOutOfMemory` with a
readable message. Every run is recorded in `<user dir>/h3md/tiling_calibration.json` (per GPU name and DynamicVRAM
on/off); a recorded OOM caps that machine's limit at 0.65 x its ratio.

Calibration (`MEASURED_RUNS`, RTX 5090 32 GB, DynamicVRAM, sol_attn): need = 0.55 x ComfyUI's memory_required formula;
accepted when need / workspace <= the largest ratio that really ran (+0.5%) and < 65% of the smallest that failed;
strips with more rows than any verified run get 0.9 x that limit. Without DynamicVRAM: raw formula within 110%.

`ff_chunk_rows` (H3MD Sampler / Loop Options, default 0 = off): the H3 DiT feed-forward runs on that many sequence rows
at a time (same result, lower peak memory), both passes. Technique credit for the strip layout:
nkxx188/ComfyUI-MiniMaxH3-Easy (MIT), notice in the module header.

### H3MD Save Video Interp (`h3md_stream_save.py`, class `H3MDSaveVideoInterp`, 2026-10-18)

**中文（简要）** 长片子不再整片放进内存：从 H3MD 的分段缓存一段一段读帧，（可选）用 ComfyUI-VFI 的 RIFE 模型插帧，
插完立刻写进 mp4（H.264 + AAC），再释放这一小段。
接法：`H3MD Loop Sampler` 的输出 `long video`（原名 `segments`，同一个位置）接到 `H3MD Save Video Interp`；
这一个节点代替原来的 RIFE Frame Interpolation + Create Video + Save Video。
**2026-10-18 晚起 Loop 不再把整段视频拼进内存**：`merge_images` 开关已删除，`images` 输出只是一张 64x64 的占位图；
视频只从 `long video` 出。旧工作流照常打开（存过 `merge_images` 的、没存过的都不错位），但 `images` 接着 RIFE /
Save Video 的话出来的不是视频，运行时日志会警告一次，请改接 `long video` → H3MD Save Video Interp。
（`cache_mode=ram` 例外：仍在内存里拼好从 `images` 出，`long video` 为空。）`crf` 新建节点默认 19（和 Video Combine 一样）。
* 第一个开关 `interpolate`：关 = 不插帧；开 = 用 RIFE 插帧。插帧选项（`scale`、`model_name`、`batch_size`、
  `use_fp16`）在关的时候变灰。`source_fps` 只是一行只读显示“24 (fixed)”（H3MD 永远是 24 fps，不是输入）。
* `target_fps`（默认 24）永远可改，**就是写出文件的帧率**。注意：`interpolate` 关、`target_fps` 又不是 24 时，
  帧不变只是播放快/慢了，**声音会对不上**（日志警告，写明快/慢多少倍）。
* `format`：`video/h264-mp4`（默认）、`video/h265-mp4`、`image/gif`、`image/webp`、`image/png-sequence`。
  不适用的选项会变灰：`crf`/`pix_fmt`（yuv420p / yuv420p10le）只给 h264/h265；`loop_count`（0 = 无限循环）只给
  gif/webp；`trim_to_audio`（片长截到音频长度）只给 mp4（只有 mp4 有声音）；`save_output` 关时 `output_folder` 变灰。
* 最上面两行是 `filename_prefix`、`output_folder`（文件名和保存位置放在一起），下面才是 `interpolate` 等选项；
  `delete_cache_after_save` 和缓存路径行仍在最下面（播放器上方）。
* `save_metadata`：把 prompt/workflow 写进文件（mp4 标签；png 序列第一帧；gif/webp 另存同名 `.png`）。
* `save_output` 关 = 写到 ComfyUI temp 文件夹（节点里照样能预览）。
* 节点最下面有自己的预览播放器（固定高度，播放/暂停、拖动进度、静音/音量，黑边等比显示）。
* 输出：`video_path`（STRING）、`frame_count`（INT）、`video`（VIDEO，可接 ComfyUI 核心的 Get Video Components /
  Save Video；webp 和 png 序列时为空）。原来的 `fps` 输出已删除，旧工作流里接在 `fps` 上的线加载时会被断开。
* 每次处理多少帧（chunk）全自动：每个 chunk 之前查一次空闲显存，最多用 90%
  （按分辨率、scale、fp16、batch 保守估算 RIFE 显存），最少 8 帧，另有内存上限。
  显存不够（CUDA OOM）时：清缓存，chunk 减半重试（最少 8）→ 8 帧还不够：让 ComfyUI 卸载模型、清显存，
  `batch_size` 改 1 再试 → 还不够：这一段改用 CPU 插帧（很慢，日志里写明）。成功后之后的 chunk 不再变大。
* `delete_cache_after_save` 默认关（保留缓存）；打开时只在视频文件完整写好之后才删缓存。
  它下面一行只显示缓存文件夹的绝对路径（没有标签，太长截断，鼠标悬停看全路径和状态 kept / deleted after save）。
* 已删除：`preset`（用编码器默认）、`source_fps` 输入、`cache_folder` + `folder_overlap_source` +
  `folder_audio_crossfade_ms`（不再支持不接 Loop 直接从文件夹出片）；`long video` 现在必须接。
  `pingpong`（v2 里加过，同日又删除）。以前所有布局存的旧工作流（2026-10-18 原版、带 pingpong 的 17 值 v2、
  去掉 pingpong 的 16 值 v3）照常打开、按名字对齐，删掉的选项值丢弃；2026-10-18 原版里“插帧关”的存档仍按 24 fps 出片。
* 失败/中断：删掉未完成的 `.part` 文件（png 序列删掉未完成的文件夹），缓存保留。Loop 自己永远不在出片前删缓存。

**English.** Saves the H3MD Loop result straight from the per-segment disk cache, chunk by chunk:
read merged source frames (memory-mapped, exactly the frames/spans `SegmentCache.merge` would put into the IMAGE
output, incl. `merge_overlap_source` and trims), optionally interpolate them with ComfyUI-VFI's RIFE model, encode
them into the open mp4 and free them. Peak RAM ~ one chunk (+ x264 look-ahead), not the whole clip.

Wiring (replaces `RIFE Frame Interpolation` + `Create Video` + `Save Video`):

```
H3MD Loop Sampler --long video--> H3MD Save Video Interp
```

* **The Loop never concatenates the clip in RAM** (since 2026-10-18 late; both Loop nodes, `cache_mode=disk`):
  `images` (slot 1, IMAGE) is a 64x64 placeholder, `audio` is still the joined audio, `frame_count` the real count.
  The 6th output **`long video`** (slot 5, type `H3MD_SEGMENTS`; was named `segments` - same slot and type, so saved
  links still connect; `None` without a disk cache) carries the cache folder + merge plan.
  When `images` is linked to anything in the queued prompt, the Loop logs one warning per run:
  `'images' no longer carries the video (64x64 placeholder): connect 'long video' to H3MD Save Video Interp`.
  `cache_mode=ram` keeps the old in-RAM join (`images` = clip, `long video` = None, logged).
* `merge_images` (a main-node widget for a few hours on 2026-10-18) was **removed**. It was the last widget, so a
  workflow saved with it has one extra trailing `widgets_values` entry that the positional load ignores; workflows
  saved before it existed have the current length. Nothing else shifts (hidden `delete_cache` keeps its slot). A
  stale unlinked `merge_images` input slot from such a workflow is dropped on load, and an API prompt that still sends
  `merge_images` is accepted and ignored (logged). `web/js/h3md_long_video*.js` also relabels the saved output name
  `segments` -> `long video` on load.
* The save node's input id stays **`segments`** (saved links and API-format prompts address inputs by this name;
  renaming it would break them); only its label shows `long video` (`web/js/h3md_long_video*.js`).
* Inputs, in order (2026-10-19 layout): required `segments` (label `long video`), then the widgets
  `filename_prefix`, `output_folder` (the two top rows), `interpolate` (default off), `target_fps` (default 24,
  1-240), `scale`, `model_name`, `batch_size`, `use_fp16`, `format`, `pix_fmt`, `crf` (default 19, 0-51),
  `loop_count` (0-100), `trim_to_audio`, `save_metadata` (on), `save_output` (on), `delete_cache_after_save`. UI-only (not inputs, not in `widgets_values`):
  `source_fps` display (read-only "24 (fixed)", right after `interpolate`), the unlabelled cache path line and the
  preview player (last). Standard one-widget-per-row layout; the player is a normal DOM widget below the options.
* **`target_fps` is always the frame rate of the written file** and is always editable. `interpolate` on: RIFE
  from 24 fps (H3MD's fixed rate) to `target_fps`. `interpolate` off: the frames are written unchanged at
  `target_fps`; with anything but 24 the clip plays faster/slower and **the audio no longer matches** - a warning
  says so (`interpolate is OFF but target_fps ... audio will NOT match ... x faster/slower`). (Could be labelled
  `frame_rate` like VHS; kept as `target_fps` so saved values/links stay valid.)
* Interpolation: `scale` 0.25-4 (flow scale, output size unchanged), `model_name` (ComfyUI-VFI's list),
  `batch_size` 1-32, `use_fp16`; greyed out while `interpolate` is off (`widget.disabled`; values kept and saved).
  The model is loaded through ComfyUI-VFI's own
  `RIFEInterpolation._get_or_load_model` (same files, auto download, model cache). Output frame count and the
  (frame, frame, timestep) of every output frame equal a one-pass RIFE run (`int(duration * target_fps)`, the
  wrapper's own `_calculate_target_frame_positions`); each chunk carries its last source frame into the next.
  `target_fps` 24 = no interpolation (like RIFE).
* `format` (all streamed chunk by chunk; options that do not apply are greyed out):

  | format | codec / container | audio | `crf`, `pix_fmt` | `loop_count` | `trim_to_audio` | metadata |
  |---|---|---|---|---|---|---|
  | `video/h264-mp4` (default) | libx264 mp4 | AAC | yes | - | yes | mp4 tags `prompt`/`workflow` |
  | `video/h265-mp4` | libx265 mp4 (`hvc1`) | AAC | yes | - | yes | mp4 tags |
  | `image/gif` | palette per frame | none | - | yes (0 = forever) | - | sidecar `<file>.png` |
  | `image/webp` | libwebp_anim, quality 90 | none | - | yes (0 = forever) | - | sidecar `<file>.png` |
  | `image/png-sequence` | folder `frame_000001.png ...` | none | - | - | - | in the first PNG |

  `pix_fmt`: `yuv420p` (default, plays everywhere) or `yuv420p10le` (10-bit, encoded from 16-bit RGB). GIF delays
  are 1/100 s steps (24 fps plays at 25). `loop_count` is not applied to mp4 (it would need the whole clip).
* `trim_to_audio` (mp4 only): the video is cut to the audio length (nothing happens without audio or when the
  video is already shorter).
* `save_metadata` (on): prompt + workflow as above; off, or ComfyUI `--disable-metadata`, = none.
* `save_output` off: written to ComfyUI's temp folder (`output_folder` greyed out), still previewed in the node.
* **Preview player** (own code, `web/js/h3md_save_video_interp.js`): HTML5 `<video controls>` with play/pause,
  seek bar, mute/volume, no autoplay, fixed height 270 px, letterboxed (`object-fit: contain`); gif/webp show the
  animation, png-sequence its first frame. Fed by the node's own `h3md_video` UI message (not `images`, so the node
  size stays fixed; the core images preview / queue thumbnail is not used).
* Outputs: `video_path` (STRING; a folder for png-sequence), `frame_count` (INT), `video` (VIDEO, lazy
  `comfy_api.latest.InputImpl.VideoFromFile` on the written file - connects to core Get Video Components / Save
  Video etc.; `None` for webp and png-sequence). The old `fps` (FLOAT) output was removed: links from it in saved
  workflows are dropped on load (logged in the browser console). (VHS Video Combine outputs `VHS_FILENAMES`
  instead.)
* **Removed** (2026-10-19): `preset` (encoder default), the `source_fps` input, `cache_folder`,
  `folder_overlap_source`, `folder_audio_crossfade_ms` (saving a kept cache folder without the Loop is gone),
  `pingpong` (briefly in the v2 layout); `long video` is required. Workflows saved with any earlier layout load
  aligned by name (`widgets_values_named` when its keys are in an older order or hold removed keys, else the
  positional layout: V1 2026-10-18 = 15 values, V2 = 17 with `pingpong`, V3 = 16 starting with `interpolate`;
  the current one starts with `filename_prefix`) - removed values are dropped, a V1 save with `interpolate` off
  is set to `target_fps` 24 (what it wrote); stale unlinked input slots are removed. API prompts
  that still send the removed keys are accepted and ignored (logged).
* **Automatic chunk size** (no `chunk_frames` widget): before every chunk the free VRAM is read
  (`comfy.model_management.get_free_memory`, else `torch.cuda.mem_get_info`). Chunk = (90 % of free - RIFE working
  set) / per-source-frame bytes, with a conservative estimate from padded resolution, `scale`, fp16 and `batch_size`;
  clamped to 8..240 source frames and to 25 % of free RAM (source + interpolated frames). Without interpolation only
  the RAM cap applies. Chunk size and reason are logged; the report lists `chunk_sizes` and `oom_events`.
* **CUDA OOM ladder**, per chunk: empty cache, halve the chunk (>= 8), retry -> still OOM at 8: ComfyUI memory
  management (`unload_all_models` + `soft_empty_cache`), `batch_size` 1, retry -> still OOM: this chunk on the CPU
  (float32; ComfyUI-VFI's module-level `device` is switched in memory for the call and restored, no file changed;
  slow, may differ slightly from GPU fp16), logged as a warning. After an OOM the chunk never grows back above the
  size that fitted. Non-OOM errors are raised unchanged.
* mp4 encoding like ComfyUI's SaveVideo / VHS: `crf` default **19** for new nodes (saved workflows keep their
  value), BT.709/sRGB tags, AAC at the source sample rate, `<filename_prefix>_00001_.<ext>` in the output folder
  (`output_folder` empty = ComfyUI output). Written as `<file>.part` (png-sequence: `<folder>.part`), renamed when
  complete; removed on failure.
* `delete_cache_after_save` (default off): cache folder removed only after a complete save, and only if it looks
  like an H3MD run folder (`resume_*` / `run_*` holding only `seg_*` files).
* Cache path line (right below `delete_cache_after_save`, widget `cache_folder_path`): **no label**, only the
  absolute cache folder (truncated when long, full path + `kept` / `deleted after save` / `kept (save failed)` on
  hover); read-only, not an input, not in `widgets_values`; filled at start (`h3md.save_video_interp` message) and
  at the end (`onExecuted`).
* Frontend files: `web/js/h3md_save_video_interp_ui.js` (pure helpers, node-tested) +
  `web/js/h3md_save_video_interp.js` (extension); `web/js/h3md_hide_widgets*.js` (Loop `delete_cache`);
  `web/js/h3md_long_video.js` (pure, node-tested) + `web/js/h3md_long_video_ext.js` (`long video` labels).

`delete_cache` (Loop): **hidden in the UI** since 2026-10-18 (`web/js/h3md_hide_widgets*.js` sets `widget.hidden`;
the input, its widget slot and `widgets_values` position are unchanged, so old workflows load as before) and **ignored
by the backend**: the Loop never deletes its cache before the video is saved, whatever the saved value is (logged).
Cache deletion = H3MD Save Video Interp `delete_cache_after_save`, after the file is complete. Without the save node,
kept caches must be removed by hand (`<drive>/H3MD_cache/run_*` / `resume_*`).
Loop 的 `delete_cache` 已在界面隐藏（输入和位置都保留，旧工作流照常加载），后端不再在出片前删缓存；删缓存只由
H3MD Save Video Interp 的 `delete_cache_after_save` 在视频完整写好之后做。

### VRAM policy
Between the two passes (H3MD Sampler, inside the lift) the depth ControlNet patch is released, idle models that are
not the diffusion models are unloaded (`unload_idle_models`) and the learned upscaler is dropped from RAM after use
(`release_upscaler`). Between segments: `vram_release` deep (idle models, ComfyUI buffers, CUDA cache, full unload
when needed) or basic. `slow_step_guard` watches the per-step log (`[H3MD timing] low_res|high_res step i/n`) and
stops the run (cache kept) when a step takes >= 4x the usual time (VRAM exhausted).

### Licensing / provenance
See [NOTICE.md](NOTICE.md) (third-party sources, what was adapted vs. used as an idea only, runtime dependencies) and [LICENSE](LICENSE) (AGPL-3.0-or-later, with an attribution term).

### Requirements
ComfyUI with core MiniMax H3 nodes (`comfy_extras.nodes_minimax_h3`), PyAV, torch, numpy, Pillow, einops (all in a
standard ComfyUI install). The two-pass engine is bundled in `h3md_luna/` (Luna-H3-Sampler, GPL-3.0).

