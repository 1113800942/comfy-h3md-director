"""0.5.91 (user 03:31): a Remake part takes 4 keyframes - H3MD picks the 4 that show every replaced person."""
import h3md_remake_engine as RE


def test_pick_covers_everybody():
    kfs = [{"image": "a", "frame": 10, "people": [1, 2]}, {"image": "b", "frame": 40, "people": [1, 2]},
           {"image": "c", "frame": 70, "people": [1]}, {"image": "d", "frame": 100, "people": [2, 3]},
           {"image": "e", "frame": 130, "people": [4]}, {"image": "f", "frame": 160, "people": [1, 4]}]
    got, missing = RE.pick_keyframes(kfs, 4)
    shown = set().union(*[set(k["people"]) for k in got])
    assert len(got) == 4 and shown == {1, 2, 3, 4} and not missing
    assert [k["frame"] for k in got] == sorted(k["frame"] for k in got)


def test_pick_reports_who_is_missing():
    kfs = [{"image": str(i), "frame": i * 10, "people": [i]} for i in range(1, 7)]
    got, missing = RE.pick_keyframes(kfs, 4)
    assert len(got) == 4 and len(missing) == 2


def test_old_plans_spread_over_the_part():
    kfs = [{"image": str(i), "frame": i * 10} for i in range(10)]
    got, missing = RE.pick_keyframes(kfs, 4)
    assert len(got) == 4 and not missing and got[0]["frame"] <= 10 and got[-1]["frame"] >= 80


def test_pins_for_segment_uses_the_pick():
    kfs = [{"image": "a", "frame": 5, "people": [1]}, {"image": "b", "frame": 6, "people": [1]},
           {"image": "c", "frame": 7, "people": [1]}, {"image": "d", "frame": 8, "people": [1]},
           {"image": "e", "frame": 150, "people": [2]}]
    got = RE._pins_for_segment(kfs, 175, 0)
    assert len(got) == 4 and any(k["image"] == "e" for k in got)
