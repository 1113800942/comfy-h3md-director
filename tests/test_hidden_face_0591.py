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


def test_only_the_front_face_does_not_keep_him():
    # the one face in the frame is the front man's (it also lies inside the small box): no second face -> dropped
    fr, _ = K.clean_frames(frames(), {0: [(0.40, 0.25, 0.48, 0.33)]})
    assert [w[0] for w in fr[0]["who"]] == [1]
