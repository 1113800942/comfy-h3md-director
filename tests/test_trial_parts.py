"""0.4.5 试镜: the Director lists what each number in the 试镜 box means (shots and seconds)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401
import h3md_director as D  # noqa: E402


def _plan(window):
    segs = [{"length": 243, "overlapIn": 0, "prompt": f"Duration: 10.12 s (243 frames).\\n\\nshot {i}"} for i in range(3)]
    segs[2]["length"] = 226
    raw = {"version": 1, "fps": 24, "width": 1088, "height": 1920, "segments": segs, "media": {}}
    if window:
        raw["windowMode"] = {"length": 243, "highLength": 175, "highOverlap": 22}
    return raw


def test_trial_window_mode_one_part():
    plan = D.parse_plan(_plan(True), 1088, 1920, True)
    t = plan["trial"]
    assert len(t) == 1 and t[0]["shots"] == [1, 3] and t[0]["start"] == 0 and t[0]["after"] == "cut", t


def test_trial_segment_mode():
    plan = D.parse_plan(_plan(False), 1088, 1920, True)
    t = plan["trial"]
    assert [x["shots"] for x in t] == [[1, 1], [2, 2], [3, 3]], t
    assert t[1]["start"] == round(243 / 24, 2)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
