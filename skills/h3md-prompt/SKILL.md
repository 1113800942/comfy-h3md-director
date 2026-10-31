---
name: h3md-prompt
description: "Write shot prompts for the H3MD Director ComfyUI nodes (H3MD Director / Director + RefMod / H3MD Loop Sampler): official MiniMax H3 prompt format plus H3MD rules - 5+17n lengths, 22/39-frame overlaps, autosplit, RefMod, depth/SAM3 motion copy, locked audio, long-video stability. For plain MiniMax H3 without H3MD, use the official h3-prompt-writing skill instead."
---

# H3MD shot prompts

中文版：[SKILL.zh-CN.md](SKILL.zh-CN.md)

## Scope
- **Use this skill only for H3MD**: prompts that go into the shots of H3MD Director (or Director + RefMod) and run
  through the H3MD Loop Sampler.
- **Plain H3** (official API, MiniMax Hub, ComfyUI's built-in H3 nodes, single clips): use the official
  [`h3-prompt-writing`](https://github.com/MiniMax-AI/MiniMax-H3) skill instead
  (`npx skills add https://github.com/MiniMax-AI/MiniMax-H3 --skill h3-prompt-writing`). Do not carry the H3MD-only
  rules below (automatic Continuity sentence, autosplit, RefMod, depth/SAM3, tag numbering, globalPrompt) over to
  plain H3.
- Not sure which one? Ask first: "Is this for H3MD or plain H3?"

Every shot in H3MD Director is one independent H3 generation (a long shot is split automatically into continued
segments, see section 2). So every shot prompt is a complete, valid official H3 prompt (same format as
`h3-prompt-writing`), plus H3MD's own timeline, tag and seam rules. Install `h3-prompt-writing` alongside this skill:
it has the full format details; below are the essentials.

## 0. Check before writing
- Total length and number of shots follow the scene; do not split evenly.
- What each shot has attached: pictures (up to 9), videos (up to 3), audio (up to 3), RefMods; which video has depth on,
  which audio is locked.
- **Look at every reference photo first**: plain / grey studio background? what expression? is a floor visible? The
  model copies all of these (section 9).
- For every seam: hard cut (overlapIn 0) or continuation (22 or 39 frames).
- **Don't make life hard**: the simpler the set, the steadier the video. No mirrors, glass reflections, wooden floors,
  tiles, stripes, grids or water reflections - textures like these come out different in every segment. Describe one
  solid, untextured floor (e.g. "seamless polished cream marble in one solid color").

## 1. Choose the format (official spec)
### Any reference media → full-reference format, six fields in this order
```
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
```
- `subject_definitions`: one line per tracked item; definitions and traits only, no timing, no camera. A picture that
  only defines a person, outfit or location goes inside `<Subject N>` ("`<Subject 1>` is the woman in `<Picture 1>` ..."),
  not on its own line. `<Picture N>` gets its own line only as a first frame, keyframe, last frame or composition
  anchor. `<Video N>` means the source of the whole structure (motion, camera, cutting, edit); people or objects reused
  from it are still `<Subject N>`. Voice reference: "`<Audio 1>` is the voice-timbre reference for `<Subject 1>` (S1)."
- `summary`: one short English paragraph starting with the task type, combinable with ` + `: `keyframe completion`,
  `reference generation`, `video editing`, `video continuation`, `audio reuse`, `audio reference`. No new tags here.
  A video that only gives motion or camera is `reference generation`, not `video continuation`.
- `retention_analysis`: one line per tag. Visual: `fully_preserved`, `partially_preserved`, `attribute_transfer`,
  `weak_reference`; audio: `fully_copy`, `partially_copy`, `reference`, `weak_reference`. Format:
  `<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - ...`. No (Sx) here.
- `detailed_description`: one or two sentences of style first, then `[Shot 1] ...`. For every shot: composition, the
  people's look and position, location and light, action and changes, camera (type, size, speed), the sound at that
  moment, and where each reference applies. Usually 350-500 English words.
- `overall_soundscape`: ambience and physical sounds for the whole clip. `non_diegetic_music`: score only the audience
  hears (instruments, tempo, dynamics), or `N/A`. Don't repeat dialogue or lyrics in these two.

### No reference media at all → base format (T2VA / I2VA / FL2VA / L2VA)
- Fields: `integrated_multimodal_description`, `overall_soundscape`, `non_diegetic_music`.
- I2VA starts with: `For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`,
  a blank line, then the fields. FL2VA / L2VA use the alignment sentences from `h3-prompt-writing` (seconds with two
  decimals).

### General
- Body in English. Dialogue, lyrics and on-screen text stay in their language: `<d>[Mandarin] ...</d>`. Inaudible:
  `[unclear]`.
- Speakers keep stable labels (S1), (S2)... in order of first speech; a reference subject speaking is `<Subject N> (S1)`.
- Concrete visual and sound detail; no empty words like cinematic or beautiful.
- Few lighting adjectives (glowing, radiant, dramatic light, shimmering) and no "the light grows brighter": a long video
  amplifies them segment by segment. Write the light as a fixed state ("the lighting stays the same from start to end").

## 2. Timeline and length
- Lengths are **5+17n frames** at 24 fps (Director snaps them): 124 f ≈ 5.17 s, 226 f ≈ 9.42 s, 294 f ≈ 12.25 s,
  362 f ≈ 15.08 s, 396 f = 16.5 s, 719 f ≈ 29.96 s. Pick the nearest 5+17n for the scene.
- `[Shot 1]` has no time; later ones are `[Shot N] At MM:SS.mmm, ...`, in the shot's own time, never source timecode.
  The last one must be inside the shot length.
- A continued shot's length includes the overlap, which is trimmed in the final video, so **new content = length −
  overlapIn** (22 f ≈ 0.92 s, 39 f ≈ 1.63 s). Plan the total with the new content.
- With a reference video, the shot length follows it (trimStart to the end, rounded up to 5+17n, plus the overlap when
  continued). **The storyboard total must not exceed the reference video's frame count**: extra frames have no motion to
  follow (take the largest 5+17n that fits).
- Few shots; no choppy cutting.

### Autosplit (on by default)
- A shot over the VRAM token budget is split automatically into equal continued segments (each 5+17n, 39-frame
  overlaps), with cut points moved to quiet moments of the reference video. No manual splitting.
- **Every part gets the whole shot prompt** (continued parts get the Continuity sentence in front). So:
  - Long shot **with** a reference video (motion copy): the video gives the motion part by part; write the prompt as
    one continuous take in a steady state. Stable over 16 segments.
  - Long shot **without** a reference video: don't write events in order ("walks to the sea, then sits, finally looks
    back") or `[Shot 2] At 00:20` cuts - later parts would replay the start. Write **steady, loopable states** ("she
    walks slowly along the water's edge, the camera tracks beside her at walking pace"). Need a story order? Make
    several shots, each with its own prompt.
  - One continuous camera take per shot (`[Shot 1]` only), no sub-shots.

## 3. Seams: hard cut or continuation
- `overlapIn = 0`, hard cut: generated independently, joined end to end. Use it when the scene or background changes.
- `overlapIn = 22 / 39`, continuation: the new shot starts with the last 22/39 frames of the previous shot's final
  latent. Use it when one camera take or one action continues.
- H3MD always prepends `Continuity: the first X seconds are carried over unchanged ...` to a continued shot. So don't
  write "continuing from the previous shot", and don't point at the previous segment with `<Video N>` or
  `video continuation` - it is not a reference asset.
- But in a manually continued shot, `[Shot 1]` must describe **the exact end state of the previous shot** in plain
  English: position, pose, facing, outfit, light, camera and the ongoing action. The overlap seconds keep that state;
  the new action starts after the overlap.
- No visual crossfade; crossfade is for audio only.

## 4. Reference tags (numbered from the shot's media order)
- Order: all pictures `<Picture 1..>`; then each video - a video with its own sound on gets its `<Audio j>` before its
  `<Video k>`; then separate audio files.
- `<Video N>` and `<Audio N>` are numbered separately; different numbers can come from the same video.
- **A video with depth on gets no `<Video N>` number** (section 6); later videos move up.
- Dropping media on a shot only attaches it. Insert tags with the tag buttons under the prompt or by typing @, so the
  numbers match the backend.
- **Check what each `<Picture N>` actually shows in this shot before writing its role** (the tag buttons show
  thumbnails; ask if unsure). Map person, outfit and location to the picture that really contains them; don't use a
  fixed template like "`<Picture 1>` is the person, `<Picture 2>` is the face". A location picture: say it is the set of
  the whole shot, unchanged from the first frame to the last.
- Person swap: the reference video gives motion and position, the person becomes `<Subject N>`, `attribute_transfer`.

## 5. RefMod (H3MD Director + RefMod)
- Write `<RefMod j>` (`RefMod j` works too, any case). At run time it becomes its real `<Picture k>` / `<Video k>` /
  `<Audio k>`. Only the word "RefMod" is replaced; `Ref 1` or a bare `<Video 2>` is not.
- j is its order in the shot's RefMod row (drag to reorder), not the "Ref N" on the media strip.
- Without a set position, RefMods go after the shot's own pictures or videos; dropped onto a picture or video, a RefMod
  takes that slot and the rest move down.
- Unused RefMods are ignored. Example: `<Subject 1> is the woman in the navy satin gown from <RefMod 1>, an identity reference. Her facial identity, hairstyle, and gown are preserved throughout.`

## 6. Motion copy: depth and SAM3
- At most one depth video per shot. **A video with depth on only drives the depth ControlNet (low-res pass only); it is
  not sent to the model as a `<Video N>` reference and takes no `<Video N>` number** (automatic). Reason, measured: as a
  reference, the white-on-black SAM3 silhouette made the model copy a black background or a grey studio into the
  picture. Exception: if that video's own sound is also used as `<Audio>`, it keeps its `<Video N>` (with a warning).
- So **don't mention `<Video N>` and don't write attribute_transfer**. Describe the shot itself instead:
  `One continuous take: the dancer's whole body stays in view from head to heels and a smooth camera follows her; no cuts, zooms or reframing are added.`
- Describe location, floor and light fully - the background has no reference. Add:
  `The <location> stays fully visible around her in every frame, and the background never turns black, grey or plain.`
- ControlNet gives the motion; describe only its character (`dances the samba with relaxed, rhythmic energy`), not
  beat by beat.
- The SAM3 prompt (e.g. `person`) goes only in the video's sam3Prompt field, **not into the H3 prompt**.
- A depth video on the Loop without `H3MD Depth Control` connected stops before the run with an error.

## 7. Audio
- Audio modes: reference (an `<Audio N>` reference), locked (hard-copied as this shot's soundtrack), none. At most one
  locked per shot.
- A locked **audio file** still has its `<Audio N>` tag: use `fully_copy` or `partially_copy` and say in the shot where
  it plays. One song across several hard-cut shots: each shot's offset = its start second in the final video
  (autosplit parts handle themselves).
- A locked **video sound** gets no `<Audio>` tag (locking and sound reference are exclusive). Describe the sound in
  plain words, matching the locked track (sung lyrics copied into `<d>` so the lips match).
- Lyrics unknown, or no lip sync wanted: say she does not sing - `she does not sing; her lips stay softly closed`.
  Never invent lyrics.
- Timbre-only reference: don't bring the source audio's lines in.

## 8. Global prompt
- `globalPrompt` is added in front of **every** shot prompt (continued shots also get the Continuity sentence first).
  It lands above `subject_definitions:`, so leave it empty, or use it only for LoRA trigger words and one tag-free style
  sentence. Style is steadier at the start of each shot's `detailed_description`.

## 9. Reference photos (what gets copied, measured)
- **Background**: a plain or grey studio background pulls the video into a grey studio (even without depth). Use
  photos with a real location, or cut the person out onto a blurred version of the target location (keep the photo's
  pixel size).
- **Floor**: full-body photos preferably without a visible floor; describe the floor only in text, one kind.
- **Expression**: a wide toothy grin in the photo becomes a frozen grin in every frame. Use a softer photo, or say
  "identity from the photo, expression not":
  `Her face keeps the exact facial identity of <Picture 2> from every angle, but with a soft, relaxed expression instead of the photo's wide smile`,
  plus `a gentle, relaxed expression` in the subject definition and
  `only a soft closed-lip smile now and then; she never laughs or grins widely` in the action.
- **Color**: saturated, over-sharp photos get more saturated over a long video. Prefer slightly muted photos.
- Re-rendering some segments with a new reference photo: keep the same pixel size and the same storyboard total, or
  autosplit changes and the cache no longer matches.

## 10. Long videos of real people
- Few turns and spins; small, slow movements that follow the music or singing. With many turns, identity drifts after
  about a minute.
- A scenic location, not an empty studio; simple, bright, one floor.
- Attach her photo to every shot (picture or RefMod) to define `<Subject 1>`, retention `fully_preserved`; describe the
  subject fully in every shot, not relying on the previous one.
- One-take single-scene long videos (motion copy, long takes): set Loop Options `lowres_anchor` to 1 (needs
  `continue_lowres_prefix = native_lowres_carry`). Each segment's low-res result is pulled back to the color statistics
  of the shot's first segment, so colors don't deepen and faces don't age. Leave it 0 for stories whose colors should
  change; after a hard cut the new shot's first segment becomes the reference.
- 1080p medium shots are sharp enough straight out; face refine is usually not needed except for small, distant faces.

## 11. Before running
- Run only segment 1 first (Loop `stop_after = 1`); when happy set it back to 0 - segment 1 comes from the cache.
- Bad segments don't need a full rerun: `rerun_segments` = `8-10, 13-14` (counted from 1). Their heads continue the
  segment before, their tails lock to the next segment's old head, the rest comes from the cache (`resume_from_cache`
  on).

## 12. Output format
One block per shot, in order:
1. A label line above the block (**not pasted into the prompt box**):
   `Shot N — length f / s · overlapIn X (new content Y s) · hard cut / continued · tags: <Picture 1>=… · locked audio offset …`
2. Below it, the complete prompt ready to paste (full-reference or base format).

End with one line: number of shots, total generated frames, total new content length.

## 13. Self-check before output
- [ ] It really is an H3MD task (otherwise use `h3-prompt-writing`).
- [ ] Format and field order right; field names exact.
- [ ] Times start at 0; the last shot time is inside the length; in continued shots new action starts after the overlap.
- [ ] Lengths are 5+17n; total counted as length − overlap; motion copy total within the reference video.
- [ ] Long shots that will autosplit describe a steady state, no ordered events or sub-shots.
- [ ] Tag numbers match the shot's media order; depth videos have no `<Video N>`; RefMods written `<RefMod j>`; no
      undefined tags.
- [ ] Each `<Picture N>`'s role (person / outfit / location) matches the actual picture; no plain / grey photo
      backgrounds, or replaced with a blurred location.
- [ ] Expression stated (not the photo's grin); no pile of lighting adjectives; light written as fixed.
- [ ] Simple set: no mirrors, wooden floors, tiles, stripes; one solid floor; "background never turns black or grey".
- [ ] Every shot has composition, positions, location and light, action, camera and sound; dialogue and lyrics in
      `<d>` in their own language; unknown lyrics → she does not sing.
