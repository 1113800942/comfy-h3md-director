"""0.5.91: keyframes by MiniMax H3 Image character swap on the whole frame, everybody in one edit, prompt by the
official H3 Image prompt enhancer."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_keyframe as K  # noqa: E402


def test_frame_rank():
    assert K.frame_rank([160, 20, 240, 190], [[20, 20, 100, 190], [300, 20, 380, 190]]) == (2, 3)
    assert K.frame_rank([0, 0, 5, 5], []) == (1, 1)


def test_swap_size():
    w, h = K.swap_size(1664, 960)
    assert w % 32 == 0 and h % 32 == 0 and 3.6e6 < w * h < 4.4e6 and abs(w / h - 1664 / 960) < 0.02


def test_instruction_names_everybody_by_place_now():
    t = K.swap_instruction([{"rank": 3, "ref": 3, "desc": "B", "cut_bottom": True},
                            {"rank": 1, "ref": 2, "desc": "A", "look": "the man in grey"}])
    assert t.index("first from the left (the man in grey)") < t.index("third from the left")
    assert "<Picture 2> (A)" in t and "<Picture 3> (B)" in t and t.count("bottom edge") == 1
    assert "same pose, position, scale and gaze" in t and "camera framing" in t
    for w in ("glove", "sleeve", "stocking", "hand"):          # user rule after runs 40/41: no item list
        assert w not in t.lower()


def test_pe_caption():
    assert K.pe_caption('{"expanded_caption":"A photo.","target_aspect_ratio":"16:9"}') == "A photo."
    assert K.pe_caption('```json\n{"target_aspect_ratio":"16:9","expanded_caption":" X "}\n```') == "X"
    assert K.pe_caption("no json here") is None and K.pe_caption('{"expanded_caption": ""}') is None
