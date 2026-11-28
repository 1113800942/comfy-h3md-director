"""v0.3 segments_to_run (H3MD Control Panel): one box instead of rerun_segments + stop_after."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401
import h3md_loop as L  # noqa: E402
import h3md_loop_split as LS  # noqa: E402


class FakeCache:
    def __init__(self, cached, frames=56, content=None, made_with=None):
        self.cached, self.frames, self.content = set(cached), frames, content or {}
        self.made_with = made_with or {}

    def manifest(self, k):
        return {"frames": self.frames, "lowres": True, "content": self.content.get(k, f"c{k}"),
                "made_with": self.made_with.get(k, "s")} if k in self.cached else None


SEGS = [{"length": 56, "overlapIn": 0 if k == 0 else 8} for k in range(10)]


def test_empty_is_normal():
    assert L.plan_segments_to_run("", SEGS, FakeCache([]), True) is None


def test_first_test_run_no_cache():
    stop, rerun, _ = L.plan_segments_to_run("2", SEGS, FakeCache([]), True)
    assert stop == 2 and rerun == []          # nothing cached: plain run of 1..2


def test_rerun_cached():
    stop, rerun, _ = L.plan_segments_to_run("1-2", SEGS, FakeCache(range(10)), True)
    assert stop == 10 and rerun == [0, 1]       # repair inside a finished video: the whole video is kept


def test_mixed_with_missing_before():
    stop, rerun, line = L.plan_segments_to_run("3, 6-7, 9", SEGS, FakeCache([0, 1, 2, 4, 5, 6, 7]), True)
    assert stop == 9                             # segment 10 never ran: stop at the highest listed one
    assert rerun == [2, 3, 5, 6, 8], rerun     # listed + missing earlier segment 4 (index 3)
    assert "4" in line.split("missing earlier segments generated too:")[1]


def test_only_new_segment_after_cached():
    stop, rerun, _ = L.plan_segments_to_run("5", SEGS, FakeCache([0, 1, 2]), True)
    assert stop == 5 and rerun == []           # 5 not cached, 4 missing: normal resume up to 5


# 0.4.5 试镜 (window mode): parts 1-5; part 3 continues part 2 (a long take), the others start after a hard cut
TSEGS = [{"length": 56, "overlapIn": ov} for ov in (0, 0, 8, 0, 0)]


def test_trial_single_part_after_cut():
    idx, rerun, line = L.plan_trial("4", TSEGS, FakeCache([]), True)
    assert idx == [3] and rerun == [], idx         # only part 4: nothing before it is needed


def test_trial_part_that_continues():
    idx, rerun, _ = L.plan_trial("3", TSEGS, FakeCache([]), True)
    assert idx == [1, 2] and rerun == []           # part 3 continues part 2: part 2 runs (or comes from the cache)


def test_trial_several_parts():
    idx, rerun, _ = L.plan_trial("1, 5", TSEGS, FakeCache([]), True)
    assert idx == [0, 4] and rerun == []


def test_trial_redo_in_finished_video():
    idx, rerun, _ = L.plan_trial("4", TSEGS, FakeCache(range(5)), True)
    assert idx == [0, 1, 2, 3, 4] and rerun == [3]   # all cached: a new take of part 4, whole video kept


def test_trial_redo_cached_part_others_missing():
    idx, rerun, _ = L.plan_trial("3", TSEGS, FakeCache([2]), True)
    assert idx == [1, 2] and rerun == [1, 2]        # part 3 cached (taken again), part 2 missing: generated


# 0.5.91 (user 23:48): a cache folder is shared by every plan with the same settings; segments made for another
# plan must never be kept (spliced into the video / used as the tail lock of a re-made segment)
PLAN = {k: f"c{k}" for k in range(10)}


def test_segments_to_run_ignores_other_plans_cache():
    other = FakeCache(range(10), content={1: "old plan"})
    stop, rerun, _ = L.plan_segments_to_run("1", SEGS, other, True, PLAN)
    assert stop == 1 and rerun == [0]            # only segment 1; segment 2 of the other plan is not kept
    stop, rerun, _ = L.plan_segments_to_run("1", SEGS, FakeCache(range(10)), True, PLAN)
    assert stop == 10 and rerun == [0]           # same plan: repair inside the finished video as before


def test_segments_to_run_old_manifest_not_kept():
    class Old(FakeCache):
        def manifest(self, k):
            return {"frames": 56, "lowres": True} if k in self.cached else None
    stop, rerun, line = L.plan_segments_to_run("2", SEGS, Old(range(10)), True, PLAN)
    assert stop == 2 and rerun == [0, 1]         # segment 1 (no content record) is made again, not kept


def test_trial_ignores_other_plans_cache():
    idx, rerun, _ = L.plan_trial("1", TSEGS, FakeCache(range(5), content={1: "old plan"}), True, PLAN)
    assert idx == [0] and rerun == [0]           # the trial output is part 1 only
    idx, rerun, _ = L.plan_trial("3", TSEGS, FakeCache([2, 1], content={1: "old plan"}), True, PLAN)
    assert idx == [1, 2] and rerun == [1, 2]     # part 3 continues part 2, which is from another plan: made again


# 0.5.91 (user 05:16): segments_to_run "1" with the LMS LoRA off took segments 2-4 (made with LMS 0.2) from the
# cache and locked segment 1 to their tail - a trial with other settings must give only the tried segment
def test_segments_to_run_other_settings_not_kept():
    stop, rerun, _ = L.plan_segments_to_run("1", SEGS, FakeCache(range(10)), True, PLAN, "s2")
    assert stop == 1 and rerun == [0]            # only segment 1; the old segments are not spliced in
    stop, rerun, _ = L.plan_segments_to_run("1", SEGS, FakeCache(range(10)), True, PLAN, "s")
    assert stop == 10 and rerun == [0]           # same settings: repair inside the finished video as before


def test_trial_other_settings_not_kept():
    idx, rerun, _ = L.plan_trial("4", TSEGS, FakeCache(range(5)), True, PLAN, "s2")
    assert idx == [3] and rerun == [3]           # only part 4 (new settings), the others are not kept
    idx, rerun, _ = L.plan_trial("3", TSEGS, FakeCache(range(5), made_with={1: "s2"}), True, PLAN, "s")
    assert idx == [1, 2] and rerun == [1, 2]     # part 2 (needed by part 3) was made with other settings: again


def test_made_with_changes_with_loras_and_settings():
    a = L.made_with_sig({"full": "x"}, {"high": [["lms", 0.2]]})
    assert a != L.made_with_sig({"full": "x"}, {}) and a != L.made_with_sig({"full": "y"}, {"high": [["lms", 0.2]]})
    assert a == L.made_with_sig({"full": "x"}, {"high": [["lms", 0.2]]})


def test_input_on_main_node_last():
    main, opts = LS.split_spec(L.H3MDLoopSampler.INPUT_TYPES())
    assert list(main["optional"])[-2:] == ["segments_to_run", "redraw"]   # 0.6.6: redraw appended after it
    assert "segments_to_run" not in opts["required"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
