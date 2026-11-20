"""0.5.9: whole-frame Recast (Viggle Remake's way) - one edit per keyframe, every recast person in it."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_keyframe as K  # noqa: E402


def test_frame_rank_by_where_she_stands_now():
    own = [700, 100, 900, 900]
    others = [[100, 100, 300, 900], [1200, 100, 1400, 900], [400, 100, 600, 900]]
    assert K.frame_rank(own, others) == (3, 4)
    assert K.frame_rank([0, 0, 50, 50], []) == (1, 1)


def _people():
    return [{"rank": 1, "ref": 2, "desc": "a blonde woman, white tank top, black jeans", "look": "the woman in red"},
            {"rank": 3, "ref": 3, "desc": "a man in a trench coat", "look": "", "cut_bottom": True},
            {"rank": 2, "ref": 4, "desc": "an Asian man, black T-shirt", "look": ""}]


def test_group_prompt_names_every_person_and_slot():
    en = K.group_prompt(_people(), 4, "en")
    assert "<image1> is a photo of 4 people" in en
    assert "the person first from the left (the woman in red) becomes the person in <image2>" in en
    assert "third from the left becomes the person in <image3>" in en
    assert "second from the left becomes the person in <image4>" in en
    assert en.count("bottom edge") == 1
    zh = K.group_prompt(_people(), 4, "zh")
    assert "<image1>是一张有4个人的照片" in zh
    assert "左数第一个人（the woman in red）换成<image2>" in zh
    assert "左数第三个人换成<image3>" in zh


def test_group_prompt_stays_general():
    """user rule after runs 40/41: no item list (naming gloves etc. made the model add them)"""
    for lang in ("en", "zh"):
        p = K.group_prompt(_people(), 4, lang).lower()
        for word in ("glove", "sleeve", "stocking", "手套", "袖", "丝袜"):
            assert word not in p
    assert "from behind" in K.group_prompt(_people(), 4, "en")


def test_one_person_wording():
    assert "a photo of 1 person." in K.group_prompt([{"rank": 1, "ref": 2, "desc": "x"}], 1, "en")
