"""0.6.2 (user 2026-11-22 23:57): an auto-split shot gives every part its own timed prompt.

Before: every part got the whole shot's prompt; part 2 read '[11.7s] ...' from its own start (6.4 s late in plan
ref_splice_007) and actions past its end were lost, while the reference video / audio / keyframes were moved."""
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import h3md_director as D  # noqa: E402
import h3md_autosplit as AS  # noqa: E402

BUDGET = 182576   # the user's 5090 budget (log 2026-11-22 23:11)
ACTIONS = {3.4: "claps his hands twice", 7.0: "crouches down low", 11.7: "shouts at his opponent"}
TIMED = ("subject_definitions:\n<Subject 1> is the fighter in black.\n<Video 1> is the motion reference.\n\n"
         "summary:\n[reference generation] The target video presents a 15.1-second continuous shot.\n\n"
         "retention_analysis:\n<Video 1> (appears in [Shot 1]): partially_preserved - its moves.\n\n"
         "detailed_description:\nA realistic fight.\n[Shot 1] Two fighters in an arena. "
         + " ".join(f"[{t}s] <Subject 1> {a}." for t, a in ACTIONS.items())
         + "\n\noverall_soundscape:\nImpacts.\n\nnon_diegetic_music:\nN/A")


def plan(prompt, length=362):
    return {"version": 1, "fps": 24, "width": 1920, "height": 1088, "globalPrompt": "",
            "media": {"images": [], "audios": [], "videos": [{"id": "ref", "file": "ref.mp4", "duration": 15.4}]},
            "segments": [{"length": length, "overlapIn": 0, "prompt": prompt,
                          "videos": [{"id": "ref", "trimStart": 0.0, "useSoundtrack": False}]}]}


def parse(prompt):
    return D.parse_plan(plan(prompt), 1920, 1088, continuity=True, budget=BUDGET,
                        sizes={("videos", "ref"): (1664, 960)})


AT = re.compile(r"At about ([\d.]+) s into this part, <Subject 1> ([^.]+)\.")


class PartPrompts(unittest.TestCase):
    def test_each_action_once_at_its_shot_time(self):
        p = parse(TIMED)
        segs = p["segments"]
        self.assertGreater(len(segs), 1, p["notes"])
        seen = {}
        for s in segs:
            for x, what in AT.findall(s["prompt"]):
                t = s["startFrame"] / 24 + float(x)
                self.assertGreaterEqual(float(x) * 24 + 0.5, s["overlapIn"])   # never inside the carried frames
                self.assertLessEqual(float(x) * 24, s["length"])
                seen.setdefault(what, []).append(t)
        self.assertEqual(sorted(seen), sorted(ACTIONS.values()))
        for t, a in ACTIONS.items():
            self.assertEqual(len(seen[a]), 1, seen)                           # exactly one part plays it
            self.assertAlmostEqual(seen[a][0], t, delta=0.06)
        self.assertTrue(any("time marks given to the parts" in n for n in p["notes"]), p["notes"])

    def test_summary_length_is_the_parts(self):
        for s in parse(TIMED)["segments"]:
            self.assertIn(f"{s['length'] / 24:.1f}-second", s["prompt"])
            self.assertNotIn("15.1-second", s["prompt"])

    def test_cut_avoids_an_action(self):
        p = parse(TIMED)
        first = p["segments"][0]
        cut = first["startFrame"] + first["length"]                            # first new frame of part 2
        for t in ACTIONS:
            self.assertFalse(0 <= cut - round(t * 24) < AS.ACTION_GUARD, (cut, t))

    def test_untimed_prompt_unchanged(self):
        plain = TIMED.split("[Shot 1]")[0] + "[Shot 1] Two fighters trade kicks.\n\noverall_soundscape:\nImpacts."
        segs = parse(plain)["segments"]
        self.assertGreater(len(segs), 1)
        bodies = {s["prompt"].split("subject_definitions:", 1)[1].split("summary:")[0] for s in segs}
        self.assertEqual(len(bodies), 1)
        for s in segs:
            self.assertIn("Two fighters trade kicks.", s["prompt"])

    def test_words_times_warned(self):
        loose = TIMED.replace("[3.4s] <Subject 1>", "From 3.38 to 4.88 seconds, <Subject 1>")
        loose = re.sub(r"\[[\d.]+s\] ", "", loose)
        notes = parse(loose)["notes"]
        self.assertTrue(any("times written in words" in n for n in notes), notes)

    def test_plan_parts_avoid(self):
        base = AS.plan_parts(357, 0, 209, None)
        got = AS.plan_parts(357, 0, 209, None, [base[0] - 30])
        self.assertNotEqual(base, got)
        self.assertFalse(0 <= got[0] - (base[0] - 30) < AS.ACTION_GUARD)
        self.assertEqual(sum(got), 357)


if __name__ == "__main__":
    unittest.main()
