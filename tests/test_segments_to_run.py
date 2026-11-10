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
    def __init__(self, cached, frames=56):
        self.cached, self.frames = set(cached), frames

    def manifest(self, k):
        return {"frames": self.frames, "lowres": True} if k in self.cached else None


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


def test_input_on_main_node_last():
    main, opts = LS.split_spec(L.H3MDLoopSampler.INPUT_TYPES())
    assert list(main["optional"])[-1] == "segments_to_run"
    assert "segments_to_run" not in opts["required"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
