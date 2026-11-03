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


def test_input_on_main_node_last():
    main, opts = LS.split_spec(L.H3MDLoopSampler.INPUT_TYPES())
    assert list(main["optional"])[-1] == "segments_to_run"
    assert "segments_to_run" not in opts["required"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
