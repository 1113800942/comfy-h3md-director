"""0.5.91 (user 07:00, keyframe #4): a man half hidden behind the front man, with his face showing, was dropped as
'hidden' - nobody replaced him and H3 Image drew a copy of the front man there.  His face showing keeps him."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401
import h3md_keyframe as K  # noqa: E402

FRONT = [0.30, 0.20, 0.75, 1.00]          # the man in front, centre
BEHIND = [0.35, 0.24, 0.50, 0.60]         # the man behind his shoulder: small, almost all inside FRONT
FRONT_FACE = (0.48, 0.22, 0.58, 0.32)     # the front man's face
BEHIND_FACE = (0.38, 0.27, 0.45, 0.35)    # the face peeking out beside his head


def frames():
    return [{"at": 0.0, "who": [[1, list(FRONT)], [2, list(BEHIND)]]}]


def test_hidden_without_face_is_dropped():
    fr, _ = K.clean_frames(frames())
    assert [w[0] for w in fr[0]["who"]] == [1]


def test_hidden_with_face_showing_is_kept():
    fr, notes = K.clean_frames(frames(), {0: [FRONT_FACE, BEHIND_FACE]})
    assert sorted(w[0] for w in fr[0]["who"]) == [1, 2]
    assert "1 half-hidden with the face showing kept" in notes[0]


# 0.5.91 (_017): three more tracker mistakes that must never become a keyframe
def _five(at, skip=None, extra=None):
    who = [[i, [0.1 + 0.15 * i, 0.4, 0.2 + 0.15 * i, 0.6]] for i in range(5) if i != skip]
    return {"at": at, "who": who + (extra or [])}


def test_box_from_ceiling_to_floor_is_bad():
    K.FRAME_ASPECT = 960 / 1664                              # a portrait frame, as Prepare sets it
    fr = [_five(t / 6) for t in range(6)]
    fr[3]["who"].append([9, [0.45, 0.02, 0.60, 0.64]])       # 15 % wide, 62 % tall: no person
    out, _ = K.clean_frames(fr)
    assert out[3].get("bad") and not out[2].get("bad")


def test_stray_small_box_dropped():
    fr = [_five(0.0, extra=[[7, [0.81, 0.41, 0.84, 0.44]]])]  # a spot on a truck among five people
    out, notes = K.clean_frames(fr)
    assert 7 not in [w[0] for w in out[0]["who"]] and "1 stray small box(es) dropped" in notes[0]


def test_person_missed_between_frames_is_bad():
    fr = [_five(t / 6) for t in range(7)]
    fr[3] = _five(0.5, skip=2)                                 # man 2 seen before and after, not boxed here
    out, _ = K.clean_frames(fr)
    assert out[3].get("bad") == [2]
    assert not any(out[i].get("bad") for i in (0, 1, 2, 4, 5, 6))


def test_only_the_front_face_does_not_keep_him():
    # the one face in the frame is the front man's (it also lies inside the small box): no second face -> dropped
    fr, _ = K.clean_frames(frames(), {0: [(0.40, 0.25, 0.48, 0.33)]})
    assert [w[0] for w in fr[0]["who"]] == [1]
