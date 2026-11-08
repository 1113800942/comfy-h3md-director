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

### Window mode (0.3.7 beta: the Director's "Seamless long video" switch)
- Write the plan as usual: shots, continued shots (`overlapIn` 22/39) and hard cuts (`overlapIn` 0). With the switch on
  (plan `"windowMode": {"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22}`) H3MD joins continued
  shots into one timeline sampled in one run (no seams) and keeps hard cuts as clean cuts. A group runs up to about
  60 s (30 s with less than 40 GB RAM) and ends at the last hard cut that fits; a continuous take longer than that
  gets a note saying where to add a hard cut.
- Switched off automatically in window mode: autosplit, depth / pose control (the video stays a normal reference video;
  also off in motion copy) and RefMods (identity comes from the reference pictures).
- Timing inside a long shot (recommended): give each action or sung line its time from the shot's start, in the usual
  form `At 00:07.9 she sings: <d>[English] ...</d>.` or as `[8s] she raises one hand, then lowers it.` H3MD gives each
  one to exactly one window (about 8–10 s) and tells that window when it happens, so nothing is played or sung twice;
  windows without one are told to carry on without new gestures. Only the sentence right after the time belongs to it;
  the rest of the text is shared by the whole shot. Keep actions about 8 s apart. Without times every window gets the
  whole shot text, so write continuous states there.
- Continued shots: start with "The same ... continues without a cut", repeat the state the previous shot ends in, and
  give every new action and every camera change a time (`[3s] she gradually slows down ...`), described as gradual.
  Tested 11-08: an untimed "she slows down, stops at the railing and turns toward the sea, the camera settling into a
  medium shot" came out as a hard cut, and the next window played it again (double image).
- Place and light: write them in full, in the same words, in every shot of a take, continued shots included. Each
  window knows only its own shot's text: a continued shot that said only "the same boardwalk" got a different pier
  (weathered wood, string lights), dissolving into it over about a second (tested 11-08).
- Summary sentences with "then / after / finally" are left out of the window prompts automatically; better still,
  write the summary as what stays the same.
- Reference pictures follow the windows automatically: a window carries only the pictures its own text names.
- Dialogue and lyrics always in `<d>[English] ...</d>` (tested: plain text gave unintelligible speech).
- Outfit not shown in any reference picture: write it in full (cut, neckline, sleeves, length, fabric, colour, and
  "opaque, not see-through") in the same words in every shot's subject definition. Across a hard cut nothing but the
  text and pictures carries over (tested 11-08: "a flowing light-blue summer dress" became a strappy dress with a sheer
  lace waist before the cut and a puff-sleeve dress after it). For a strict outfit, add a picture of it.
- The `Duration:` line is rewritten by H3MD (each window gets its own length).
- `"preview": true` renders the low-res pass only (motion and framing; a later full run reuses it).
  `"redraw": [[4, 8]]` re-renders only seconds 4–8 of a finished run with a new seed; everything else stays identical.
- A hard cut inside one shot (`[7s cut] [Shot 2] ...`) also works, but a separate shot with `overlapIn` 0 is simpler.

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

## 10b. Singing, framing and camera (measured on a 160 s music video)
- **Sing only in medium and close shots.** In a wide shot the face is too small to show lips, so H3 inserts a
  front close-up of her singing by itself: a hard cut, or a translucent double exposure (a big face floating over the
  scene). "One continuous shot, no cuts" does not stop it. In wide shots write `She does not sing in this shot; her
  lips stay softly closed`, and let the song play as a voice-over.
- **Face size decides sharpness.** At 1920×1088 a knees-up shot gives a face of about 110 px (soft, blotchy skin; face
  refine cannot rescue it). Waist-up or closer, or a portrait 1088×1920 frame, gives 200+ px and a clear face. Where a
  face would be small, show her from behind, in profile far away, or in details (hair, hands, gown).
- **The face close-up photo only in medium and close shots.** Attached to a wide shot, the model may copy the photo as
  a shot of its own (its framing and its background).
- **Timed lyrics need one segment per shot.** Write `At 00:07.3 she sings: <d>[English] ...</d>` in the shot's own
  time and keep each singing shot ≤ about 500 frames (one segment), or split it into shots with their own prompts;
  autosplit would replay the timings from 0 in every part.
- **Lock the camera when a large foreground ground is in view.** A moving camera over a big meadow makes the grass
  ripple like waves. Write `The camera is locked off on a tripod and does not move at all; the ground stays perfectly
  still and only she, her gown and her hair move.` Keep camera moves for sky, clouds and distant shots.
- **No "slowly" words for motion.** slowly / very slowly / unhurried / slow waves turn into slow motion. Write `at a
  natural, relaxed pace in real time` and add `All motion plays at natural real-time speed, not slow motion.`
  Calm comes from the music and the framing.
- **Reference images must match the output orientation.** A landscape location picture in a portrait video comes back
  as a letterboxed strip with dark bars. Make a portrait version (extend sky above and clouds below) for portrait runs.
- **Stock-watermark ghosts.** Large snowfields sometimes show a faint yellow, blocky "watermark" patch; it depends on
  the seed: re-render that segment with a new seed.

## 10c. Instrumental parts, "no X" wording, reference images (measured on a 3-minute single take)
- **Intro, interlude and outro get their own segment, and that prompt never mentions singing.** A line like "she does
  not sing from 0 to 12.7 s" inside a singing segment does not hold: the model sees "a singer + music" and moves her
  lips with the music. As separate segments the 12 s intro and 14 s outro kept the mouth closed the whole time.
  Write the instrumental segment like this:
  - `<Audio 1> is an instrumental passage of the song's music, with no singing voice; <Subject 1> only listens to it and does not sing.`
  - summary `... listening silently to <Audio 1> ...`; retention `it is instrumental here, so her lips stay closed.`
  - non_diegetic_music: the instruments, `no singing voice`; overall_soundscape without her voice.
  - give her something concrete to do: a closed-mouth smile, slow breathing, closing and opening her eyes, looking far away.
- **A few instrumental seconds before the first sung line** (inside a singing segment): add `From 00:00.0 to
  00:02.1 <Audio 1> is purely instrumental, with no singing voice at all, so she is only listening ...`, together with
  the split above.
- **"no X" does nothing.** H3 is CFG-distilled: negative prompts have no effect, and `no shine, no oil, no gloss` in
  the positive prompt changes nothing either. Leave the thing out instead (see "When there is a reference photo" below).
- **Do not retouch the reference photo locally** (de-shine, local colour fixes). A retouched photo carried a yellow
  edge and an orange background patch into the whole video; the original photo did not. Re-generate the photo instead.
- **Yellow patches / colour casts: check the reference image first, then the seed.**
- **Seeds: fix the seed only for A/B tests (change one thing); use a random seed for real runs.**
- **When there is a reference photo, do not describe the person, the light or the colours - let the picture carry
  them.** Measured on the same reference and seed: a prompt that described her face, "no makeup ... visible pores on
  her forehead, nose and cheeks", "true-to-life skin texture", "music-video footage" and "light falls on her face and
  hands" gave a sweaty, glossy face and oily hands although the photo was matte; the same shot with `<Subject 1> is the
  woman shown in <Picture 1>.`, the place as "the room shown in `<Picture 1>`" and no light / colour / style words came
  out as matte as the photo. Write only what the picture cannot show: camera, framing, action, timing, sound. Skin and
  texture words make the model *render* skin, and that is where the shine comes from. (A satin dress still shines:
  that is the fabric.)
- **Skin shine also depends on the LoRA mix.** On the same photo and seed, moving to the FL2V turbo 4-step v1.2 768p
  LoRA, Enhancer 0.55 (low-res), realism-people 0.6 (high-res) and the cinematic LoRA on both passes removed most of the
  shine; the bare prompt above removed the rest. Test the LoRA mix on segment 1 before a long run.

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

## 12b. Importable plan file (H3MD v0.2.0+)
After the prompts, also give the whole plan as ONE JSON code block that the user saves as `<name>.json` and opens with
**Import plan…** in the Director (no copy-paste per shot). Format (`docs/plan_format.md` in the repo):

```json
{
  "version": 1, "fps": 24, "width": 704, "height": 1280, "globalPrompt": "",
  "media": {"images": [{"id": "eve", "file": "eve.png"}], "videos": [],
            "audios": [{"id": "song", "file": "song.mp3"}]},
  "segments": [
    {"length": 294, "overlapIn": 0, "images": ["eve"],
     "audios": [{"id": "song", "mode": "locked", "offset": 0.0}], "prompt": "Duration: 12.25 s (294 frames).\n\nsubject_definitions: ..."},
    {"length": 362, "overlapIn": 39, "images": ["eve"],
     "audios": [{"id": "song", "mode": "locked", "offset": 10.625}], "prompt": "Duration: 15.08 s (362 frames).\n\n..."}
  ]
}
```
- `file` = the file name the user has (or will upload) in ComfyUI's `input/` folder; `id` = a short name used by the shots.
- `length` 5+17n; `overlapIn` 0 for the first shot and hard cuts, 39 to continue.
- Locked song: `offset` = previous offset + (previous length − this overlapIn) / 24.
- `prompt` = the complete prompt text of that shot (newlines as `\n`), exactly what would be pasted.
- Valid JSON only (double quotes, no comments, no trailing commas). The Director lists any problem it finds
  (lengths, overlaps, tags without media, missing files) instead of fixing it.

## 13. Self-check before output
- [ ] It really is an H3MD task (otherwise use `h3-prompt-writing`).
- [ ] Format and field order right; field names exact.
- [ ] Times start at 0; the last shot time is inside the length; in continued shots new action starts after the overlap.
- [ ] Lengths are 5+17n; total counted as length − overlap; motion copy total within the reference video.
- [ ] Long shots that will autosplit describe a steady state, no ordered events or sub-shots.
- [ ] An outfit that no picture shows is written in full, word for word the same in every shot (fabric opaque).
- [ ] Window mode: lines and actions inside long shots timed from the shot's start (`At 00:07.9 ...` or `[8s] ...`), about 8 s apart; dialogue / lyrics in `<d>[English] ...</d>`; the summary says what stays the same.
- [ ] Tag numbers match the shot's media order; depth videos have no `<Video N>`; RefMods written `<RefMod j>`; no
      undefined tags.
- [ ] Each `<Picture N>`'s role (person / outfit / location) matches the actual picture; no plain / grey photo
      backgrounds, or replaced with a blurred location.
- [ ] Expression stated (not the photo's grin); no pile of lighting adjectives; light written as fixed.
- [ ] Simple set: no mirrors, wooden floors, tiles, stripes; one solid floor; "background never turns black or grey".
- [ ] Every shot has composition, positions, location and light, action, camera and sound; dialogue and lyrics in
      `<d>` in their own language; unknown lyrics → she does not sing.
- [ ] Singing only in medium / close shots; wide shots say she does not sing; face photo only in medium / close shots.
- [ ] Locked camera where a large ground is in view; no "slowly" words; reference images match the output orientation.
- [ ] With a reference photo: no face / skin / makeup / light / colour / style description - only camera, action,
      timing and sound.
- [ ] Intro / interlude / outro are their own segments with no singing in the prompt; no "no X" wording; the reference
      photo is the original (no local retouching).
- [ ] When the user wants to import: one valid plan JSON block whose lengths, overlaps, offsets and prompts match the shots above.
