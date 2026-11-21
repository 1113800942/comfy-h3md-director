"""0.5.91 Recast: one H3 Image edit per keyframe, the prompt filled in by H3MD in the official enhancer's structure."""
import h3md_keyframe as K

EVA = "a young woman with long wavy blonde hair, in a white ribbed tank top, long black jeans and white sneakers, bare hands."
PETER = "a bearded man in a beige trench coat, dark grey trousers and brown leather shoes"
DAVID = "a man with short black hair in a black T-shirt, dark blue jeans and black sneakers"


def test_h3_prompt_changed_places():
    p = K.h3_prompt([(3, 2, EVA), (1, 3, PETER), (2, 4, DAVID)], 3)   # 16 s: Abby, Eve, Eva -> Peter, David, Eva
    assert p.startswith("The person in <Picture 2> is <Subject 3>: a young woman with long wavy blonde hair")
    assert "The person in <Picture 3> is <Subject 1>: a bearded man" in p and "<Picture 4> is <Subject 2>" in p
    assert "where <Subject 1>, <Subject 2> and <Subject 3> take the places" in p
    assert ("<Subject 1> is partially preserved: the face, the hair and the complete outfit of <Subject 1> come from "
            "<Picture 3>") in p and "follow the first person from the left in <Picture 1>" in p
    assert "The third person from the left is now <Subject 3>, a young woman" in p and "bare hands." in p
    assert "hand gestures (fists stay fists" in p and "mouth shape" in p and "in front of or behind" in p
    assert "Every other person" not in p and p.endswith(K.H3_END) and "bare hands.." not in p


def test_h3_prompt_one_of_three():
    p = K.h3_prompt([(2, 2, "")], 3)
    assert "where <Subject 1> take" in p and "the person exactly as shown in the picture" in p
    assert K.H3_OTHERS in p


def test_h3_size():
    assert K.h3_size(1664, 960, mp=4.0) == (2624, 1504) and K.h3_size(1664, 960, mp=2.0) == (1856, 1088)


def test_poses():
    ans = "Here you go:\n1: stands with hands on the hips, smiling, cut off at the thighs.\n- 2) raises both fists\n3: claps\n4: extra"
    assert K.parse_poses(ans, 3) == {1: "stands with hands on the hips, smiling, cut off at the thighs",
                                     2: "raises both fists", 3: "claps"}
    p = K.h3_prompt([(1, 2, EVA)], 3, {1: "claps above the head."})
    assert "follow the first person from the left in <Picture 1>, who claps above the head." in p
    assert "in exactly the pose, framing and crop of the original first person from the left in <Picture 1>." in p
    assert "There are 3 people" in K.POSE_ASK.format(n=3)


def test_size_and_cut():
    p = K.h3_prompt([(1, 2, EVA, (0.82, True)), (2, 3, PETER, (0.5, False))], 2)
    assert "<Subject 1> is exactly as big in the image as that original person, about 82% of the image height" in p
    assert "so the feet and shoes are outside the image." in p
    assert "about 50% of the image height." in p
