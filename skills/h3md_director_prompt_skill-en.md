# H3MD Director Per-Shot Prompt Rules

Each shot in the H3MD Director is a separate H3 generation. So the prompt for each shot is a complete H3 prompt, plus the H3MD-specific rules below.

## 1. Pick the format first (follow the H3 skill)
- When the shot has reference assets (images, videos, audio, RefMods), use the **full-reference six-section format** from `references/ref-en.txt` of `minimax-h3-prompt-skill`, in this order: `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`.
- When there are no reference assets at all, use base mode (T2VA / I2VA, etc.) following `references/base-en.txt`.
- Write the body in English. Put dialogue and lyrics in `<d>[language] ...</d>`, keeping the original text.
- Start `summary` with a task-type prefix, such as `[reference generation]`. `retention_analysis` uses fixed markers: fully_preserved, partially_preserved, attribute_transfer, weak_reference; for audio use fully_copy, partially_copy, reference, weak_reference.
- `subject_definitions` holds descriptions only: no timing, no camera.

## 2. Timeline: every shot starts at 0 s
- `[Shot 1]` has no time. Later shots are written as `[Shot N] At MM:SS.mmm, ...`.
- All times use **the shot's own timeline**, not the source clip's. The last sub-shot's time must be less than the shot length.
- Shot length is **5+17n frames** at 24 fps, e.g. 362 frames ≈ 15.1 s, 396 frames ≈ 16.5 s. When the length follows the first reference video, it is rounded up.
- There is no length cap and no automatic splitting any more. But **each reference video is read for at most 15 s**, so keep a shot to about 15 s or less; anything past 15 s has no motion reference.

## 3. Reference label numbering
- `<Picture N>`, `<Video N>` and `<Audio N>` are auto-numbered **in the order the assets appear in this shot**: images first, then videos (a video with original audio checked has its `<Audio>` track numbered just before it), then standalone audio last.
- Dragging an asset into a shot only attaches it. Insert labels with the label button under the prompt, or by typing @.
- **Check what each `<Picture N>` actually shows in this shot before writing its role** (the label chips under the prompt show a thumbnail of every picture). A reused or generic prompt that says "`<Picture 1>` is the person" breaks when the shot's first image is the empty scene: the person and outfit then have no source and the video can jump to another background / outfit midway. Write the person, the outfit and the location each against the picture that really contains it, and say once that the scene picture is the location for the whole shot.
- An image used only to define a character, scene or outfit goes into the `<Subject N>` definition, e.g. "`<Subject 1>` is the man in `<Picture 1>` ...", not on a line of its own.
- A reference video that only supplies motion, camera, edit rhythm and environment is written as the structural source `<Video N>`. For a character swap use `attribute_transfer`, meaning the original person's motion and position are transferred to `<Subject N>`.
- **A video with depth ticked (motion copy) has no `<Video N>` label**: it drives only the depth ControlNet and is not a reference, so do not mention it or use attribute_transfer for it. Describe the take ("one continuous take, whole body in view, smooth following camera"), write the location, floor and light explicitly, and add "the background stays fully visible in every frame and never turns black, grey or plain". (A depth video with its soundtrack on is the exception and stays a `<Video N>`.)
- **No plain / grey studio backgrounds in reference photos**: a grey-backdrop photo pulls the video into a grey studio. Use photos with a real location, or cut the person out onto a blurred version of the target location. Keep the floor out of full-body photos and describe ONE floor in the text (plain, untextured is safest, e.g. plain marble) so the floor pattern cannot change between segments.

## 4. How to write RefMods
- In the prompt write **`<RefMod j>`**; `RefMod j` or `参考RefMod 1` also work, case-insensitive. At runtime it is replaced with its actual `<Picture k>`, `<Video k>` or `<Audio k>`; the text saved in the plan is not changed.
- Only the word "RefMod" is recognized. Forms like `REF model 1`, `Ref 1` or `<Video 2>` are not replaced.
- j is the RefMod's **order in this shot's RefMod row** (drag left/right to reorder), not the "Ref N" on the asset-bin icon.
- Drop an image- or video-type RefMod onto a specific image or video and it takes that position; later numbers shift up. With no position set, it comes after the shot's own images or videos, e.g. if the shot already has 1 video, RefMod 1 becomes `<Video 2>`.
- RefMods not used by this shot are ignored at runtime and cause no error.
- Example: `<Subject 1> is the man in the black suit from <RefMod 1>, an identity reference. His facial identity, hairstyle, and clothing are preserved throughout.`

## 5. Split shots and where the reference video starts
- With **Split shot…**, each segment reads its reference video **from its own starting point** (internal field trimStart). So just write "the entire clip of `<Video 1>`"; don't write "from 00:19.6".
- Custom mode rounds each segment length up to 5+17n frames, and the length includes the overlap frames, so the actual cut lands slightly later than the seconds you entered. To match the source's scene cuts exactly, the safest way is to cut the reference video into separate files first and drag each into its own shot.
- If you manually drag the whole source clip into several shots, each one plays from 0 s of the source.

## 6. Joining shots
- `overlapIn = 0` is a **hard cut**: the segment is generated independently and joined end to end.
- `overlapIn = 22` or `39` is a **continuation**: the new segment starts from the last 22 or 39 frames of the previous segment's final output and continues. The system automatically prepends a "Continuity: ..." sentence, so you don't need to write "continuing from the previous shot".
- Use a hard cut where the source changes scene or background. Use a continuation where the same continuous take needs motion to carry over.
- There is no visual crossfade; crossfade applies to audio only.

## 7. Pre-output checklist
- [ ] Uses the six-section or base format, with correct section order and field names.
- [ ] Times start at 0 s, the last sub-shot is within the shot length, and the shot is about 15 s or less.
- [ ] Label numbers match the asset order in this shot; RefMods are written as `<RefMod j>`.
- [ ] No source-clip timecodes, and no description of scenes from other shots.
- [ ] Hard cut vs continuation matches how the source transitions.
- [ ] Every sub-shot covers composition, character position, environment and lighting, action, camera, and sound.
