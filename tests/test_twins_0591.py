"""0.5.91 (user 16:11): one person boxed twice by the tracker becomes one id (one number on the cast sheet)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_keyframe as K


def _frames(rows, step=1 / 6):
    return [{"at": round(i * step, 4), "who": [[pid, list(b)] for pid, b in r]} for i, r in enumerate(rows)]


A = (0.10, 0.20, 0.30, 0.90)
A2 = (0.11, 0.21, 0.30, 0.90)      # the same person, a second box
B = (0.55, 0.20, 0.75, 0.90)


def test_short_twin_merged_into_long_track():
    # 00154: track 3 lived 0.8 s, always on top of track 1000 (IoU 0.99)
    rows = [[(1000, A), (7, B)]] * 10 + [[(1000, A), (3, A2), (7, B)]] * 5 + [[(1000, A), (7, B)]] * 10
    f, notes = K.merge_twins(_frames(rows))
    ids = {pid for fr in f for pid, _ in fr["who"]}
    assert ids == {1000, 7}
    assert all(len(fr["who"]) == 2 for fr in f)
    assert notes and "track 3" in notes[0]


def test_two_people_side_by_side_not_merged():
    rows = [[(1, A), (2, B)]] * 30
    f, notes = K.merge_twins(_frames(rows))
    assert {pid for fr in f for pid, _ in fr["who"]} == {1, 2} and not notes


def test_brief_overlap_of_two_people_not_merged():
    # two dancers crossing: boxes the same for 2 frames of a long time together
    rows = [[(1, A), (2, B)]] * 20 + [[(1, A), (2, A2)]] * 2 + [[(1, A), (2, B)]] * 20
    f, notes = K.merge_twins(_frames(rows))
    assert {pid for fr in f for pid, _ in fr["who"]} == {1, 2} and not notes


def test_person_behind_with_smaller_box_not_merged():
    behind = (0.15, 0.25, 0.28, 0.60)   # inside A but much smaller: a real person hidden behind
    rows = [[(1, A), (2, behind)]] * 30
    f, notes = K.merge_twins(_frames(rows))
    assert {pid for fr in f for pid, _ in fr["who"]} == {1, 2} and not notes
