---
name: h3md-keyframe
description: Write the MiniMax H3 Image edit prompts for an H3MD multi-person swap keyframe package (a <name>_keyframe_export_<date>_<no>.zip from the H3MD Keyframe Export node) and answer with the .h3kf / .json file the user loads with H3MD Keyframe Import.
---

# H3MD keyframe skill - multi-person swap keyframes

The user runs the **H3MD Keyframe MultiSwap** workflow. Group 1 (*Keyframe Export*) picks the keyframes of the source
video and writes a package for you. You read the keyframes and write one **MiniMax H3 Image** edit prompt per keyframe.
Group 2 (*Keyframe Import*) loads your answer, H3 Image redraws every keyframe (about 25 s each), and the finished
keyframes become an **H3MD Multi-Swap Plan** for the H3MD Director (Remake engine).

H3 Image redraws the whole picture and draws **what the text says**. The keyframe is only a reference. Pose, hands,
legs, place, size and overlaps follow the original only when you write them, so your reading of each keyframe decides
the result. A keyframe that starts clearly wrong cannot be pulled back by the video model later. Small differences can
be pulled back.

## 1. What you receive

`<name>_keyframe_export_<YYYYMMDD>_<NNN>.zip`:

| file | what it is |
|---|---|
| `request.json` | `package` (this package's name), `answer_file`, and per keyframe: `keyframe` (number), `at_seconds`, `picture_1` (the keyframe file), `pictures` (the character pictures for this keyframe, in order, each with `picture` = its number in the prompt, `file`, `description`, `replaces_person_from_left`), `people_in_frame`, `keeps_people_from_left` (the people who stay, counted from the left in this keyframe), `people_from_left` (every person of this keyframe left to right: `place`, `box_percent` = left, top, right, bottom in % of the frame, `cut_by_edges`, `role` = "picture N" or "stays"), `sentence` (what the keyframe must become, in the user's words) |
| `keyframe_1.png` ... | the original video frames |
| `character_picture_N.png` | the new people |
| `README_for_AI.md` | a short version of this skill (for AIs without it) |
| `_h3md/` | data for the nodes: do not open, do not change |

## 2. Check the package first (intake checklist)

Before writing anything, check each item. If one fails, **stop and tell the user exactly what is missing and how to
fix it**. Do not guess.

- [ ] `request.json` is there and readable.
- [ ] One `keyframe_k.png` for every entry in `keyframes`.
- [ ] Every `character_picture_*.png` named in `pictures` is there.
- [ ] Every picture has a `description`. A half-body picture's description must cover what the picture does not
      show (trousers, shoes). If it does not, ask the user for it: nobody else can know it.
- [ ] Every keyframe has a `sentence`. Every picture has a `replaces_person_from_left`.
- [ ] `people_in_frame` matches the people you count in the keyframe, and the person at each
      `replaces_person_from_left` is really there.

Typical answers: "keyframe_3.png is missing - run Keyframe Export again", "picture 2 is half-body and its
description says nothing about trousers and shoes - add them to the descriptions on Prepare and export again".

## 3. Who is who

For keyframe k the edit model gets `<Picture 1>` = `keyframe_k.png`, then `<Picture 2>`, `<Picture 3>`, ... = that
keyframe's `pictures`, in their order. People are counted **from the left as they stand in THIS keyframe**. After a
place change the order differs from the start, so always use `replaces_person_from_left`, never the order of the
first keyframe.

**Check every position with `people_from_left` before you write it.** The boxes tell, by numbers, who is further
left or right (compare the box centres), who is closer to the camera (a lower box bottom and a taller box), who is cut
by which edge, and on which side of the person in front a half-hidden person shows (his box centre left or right of
the front person's). Read the picture, then confirm with the numbers; when they disagree, look again. (Measured: a
man peeking out from behind the lead singer was written "to the right of his head" although his box was to the left.)

The picture numbers change from keyframe to keyframe: `<Picture 2>` can be one character in keyframe 1 and another in
keyframe 3. Always take them from that keyframe's `pictures`.

The people who stay are listed in `keeps_people_from_left` for every keyframe: any positions can stay (replace the
2nd and the 4th, keep the 1st, 3rd and 5th), and they change from keyframe to keyframe. Take them from that list, never
assume they are the ones at the end, and check each one in the picture before you describe them.

## 4. Write each prompt

Write in English, in the structure of the official H3 Image prompt enhancer:

a. **Define every character picture as a Subject.** Describe the person positively and completely, head to toe, from
   the picture plus its `description`: face, hair, every garment, the shoes, and what they do not have ("bare hands",
   "no hat", "nothing on his head"). Never describe or mention the clothes of the people in `<Picture 1>`, and never
   write what to remove. **Use the same sentence for a character in every keyframe** (only the parts the frame cuts
   off are left out, see c): when the picture is half-body, the trousers / skirt and shoes come from the
   `description` and must not change from keyframe to keyframe.
b. **Define `<Picture 1>` as a Subject that is partially preserved.** The background, floor, lighting, camera angle,
   framing and crop, and the places, spacing, sizes, front-to-back order and overlaps of the people are retained.
   **Never name anything in the background** (no walls, windows, doors, buildings, vehicles, signs, furniture, not
   even "the hangar" or "the street"): write only that everything that is not a replaced person stays exactly as it
   is, with nothing added, removed or moved. A named object is painted in where the original has none - one
   sentence "the hangar, its open door and its tall window wall" used for several keyframes put a window wall into
   open sky; the video then flickers there because the keyframe and the original disagree. Never reuse one scene
   sentence for several keyframes.
c. **Body parts the frame cuts off are not mentioned.** When the feet are outside the picture, do not mention shoes
   for that person, or the model pulls the camera back. Say plainly where the frame cuts each person ("cut off at
   mid-thigh", "the right side of his body cut off by the right edge", "only a narrow strip of him is seen").
   - **A person mostly outside the frame** (`cut_by_edges`, a narrow box): write only what shows - "only his shoulder
     and one bare arm come in from the right edge, the hand open, palm down; his head is outside the top right
     corner". Written like this the model does not pull the whole person into the frame (measured).
   - **A person almost hidden behind another**: write exactly which part shows (the top of the head and the eyes
     beside the front man's head, on the left side of it) and that everything else is hidden by whom. The model tends
     to show more of a hidden person than the original does; the clearer the text, the less it adds.
   - **A different body or garment** (a woman in a dress for a man in a long coat): write the character's own clothes
     only as far as the frame shows them - dress length, bare legs, shoes only when the feet are in the frame.
d. **For every replaced person**, say which Subject takes which place, then write each item below as you read it
   from `<Picture 1>`. Never skip an item.
   - **Body:** orientation and how far it is turned; leaning, toward whom.
   - **Head:** turn and tilt (toward which side of the image), up or down; where the eyes look.
   - **Small angles too:** a clear head tilt or a forward lean is easily straightened by the model - write it
     plainly and say it is clear ("the head clearly tilted toward the right side of the image", "the upper body
     leaning forward toward the camera").
   - **Each hand, separately:**
     - where it is (above the head, beside the head, at the shoulder, chest, waist or thigh, behind the back, on someone);
     - its shape (fist, open, fingers spread or together, pointing, palms together, on the hip, hidden);
     - which way the palm faces;
     - the arm (straight or bent, and its direction).
   - **Legs:**
     - how much the knees bend and how deep the squat is;
     - which leg carries the weight;
     - how far apart the feet are, and where the toes point.

     Describe the still pose you see. **Never write a motion you guess from one frame** ("walking", "stepping",
     "jumping"): a bent-knee bounce written as "walking" comes out as walking.
   - **Face:** expression, eyes, mouth (open, shouting, closed smile, pout).
   - **Place and size** in the frame.
   - **Relations:** who is in front of or behind whom, which part of whom is hidden, who leans toward whom, standing
     apart or pressed together.

   **Left and right always mean the sides of the image** ("the hand on the left side of the image"), never the
   person's own left and right. That is the easiest thing to get backwards.
e. **People who are not replaced** must be written too, or H3 Image redraws them as copies of the new characters (it
   redraws the whole picture). Say how many people the frame holds, which positions are replaced and which stay
   (from `keeps_people_from_left`, e.g. "five people: the 2nd and the 4th from the left are replaced, the 1st, 3rd and
   5th stay themselves"). Describe every kept person positively and
   completely as they look in `<Picture 1>` - face, hair, every garment, shoes - with their place, pose and overlaps,
   and say they stay themselves, unchanged. (The rule in (a) is about the people who are replaced: never describe
   *their* clothes.)
f. End with: the outfits come only from the pictures, and the result is one natural photograph.

These are rules about what to look at. Write only what this keyframe shows, never moves remembered from another clip.

## 5. Check before you answer

Put every prompt next to its keyframe and check every person, item by item: orientation, both hands, legs, head,
expression, mouth, place, size, front/back, overlap, crop. The redrawn keyframe must not differ visibly from the
original. Typical misses:
- one fist written where there are two;
- the wrong body turn;
- the wrong overlap;
- a squat written as a walk;
- a head tilt to the wrong side, or a clear tilt / lean not written at all (it comes out straight);
- a half-hidden person put on the wrong side of the person in front (check `people_from_left`);
- a person mostly outside the frame or almost hidden written as a whole person (the model then shows all of him);
- a character's clothes written differently from one keyframe to the next;
- a kept person left undescribed (the model turns them into one of the new characters), including a narrow strip at
  the frame edge that stays.
- anything in the background named (a wall, a window, a building): the model paints it in where the original shows
  something else - say only that the background stays exactly as it is.
- in a frame with several similar people, outfits swapped between them (two yellow shirts, the wrong trousers):
  in every "Subject Picture N takes the place of ..." sentence name that person's shirt again, and the trousers when the legs show (not for a half-body framing: it pulls the camera back)
  ("Subject Picture 4, in the yellow shirt and light-blue jeans, takes the place of the fifth person"), so each outfit
  stays bound to its place;
- trousers / shoes left out for a person whose legs do show in the frame (even only one leg at the edge): the model
  then invents them, often from another person.

After the run, look at the Compare sheet for the background too, not only the people: anything that is not in the
original (a new wall, window, building, sign) means a redo with the background left unnamed. Recast's report names
the keyframes whose background differs from the original.

If you are not sure about something, say so to the user instead of guessing.

## 6. Answer

**Best:** send back the package as **`answer_file`** (from `request.json`, e.g.
`<name>_keyframe_import_<YYYYMMDD>_<NNN>.h3kf`). It is the same zip, with every file unchanged, plus one file at the
top:

```json
{"package": "<request.json package>", "prompts": {"1": "prompt for keyframe 1", "2": "..."}}
```

saved as `prompts.json`. The user clicks **Import package** on H3MD Keyframe Import and picks this file.

**If you cannot make files:** give the text of that `prompts.json` and tell the user to save it as
`<name>_keyframe_import_<YYYYMMDD>_<NNN>.json` next to the export package (output/H3MD_keyframes/). It must contain
`"package"`, which is how Import finds the keyframes.

## 7. Redo single keyframes

The user looks at **Compare** (original | new, numbered #1, #2 ...). For a keyframe that is clearly wrong, they tell
you its number and what is wrong.
1. Rewrite only that prompt.
2. Answer with a new file whose `"prompts"` holds only the numbers you redid.
3. The user imports it, clicks those numbers on **H3MD Keyframe Redo**, and runs. Only those keyframes are made again;
   the others are kept.

A keyframe whose prompt is right but whose picture came out wrong needs no new prompt: the user changes the seed on
Recast and redoes that number.

## 8. Acceptance

A keyframe passes when nothing differs visibly from the original:
- the right person in the right place;
- their own clothes;
- orientation, both hands, legs, overlaps and crop clearly matching.

Small differences (a slightly wider smile, a few percent of size) are fine: the Director's Remake follows the source
video and pulls them back.
