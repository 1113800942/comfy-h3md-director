"""0.5.91 Recast: one H3 Image edit per keyframe, the prompt filled in by H3MD in the structure the official enhancer
wrote in the user's good run (18:27), with what every person does and how the group stands read from the keyframe."""
import h3md_keyframe as K

EVA = "a young woman with long wavy blonde hair, in a white ribbed tank top, long black jeans and white sneakers, bare hands."
PETER = "a bearded man in a beige trench coat, dark grey trousers and brown leather shoes"
DAVID = "a man with short black hair in a black T-shirt, dark blue jeans and black sneakers"


def test_h3_prompt_changed_places():
    p = K.h3_prompt([(3, 2, EVA), (1, 3, PETER), (2, 4, DAVID)], 3,
                    {1: "leans towards the second person, fists up", 3: "claps"}, "they stand close, the middle one in front.")
    assert p.startswith("The person in <Picture 2> is <Subject 3>: a young woman with long wavy blonde hair")
    assert "The person in <Picture 3> is <Subject 1>: a bearded man" in p and "<Picture 4> is <Subject 2>" in p
    assert "The group of people in <Picture 1> is <Subject 4>." in p
    assert "The target image keeps <Picture 1> exactly: they stand close, the middle one in front." in p
    assert "<Subject 4> is partially preserved: its background" in p and "who stands in front of whom" in p
    assert ("<Subject 1> is partially preserved: the face, hair and outfit from <Picture 3> are integrated into the "
            "position of the first person from the left in <Picture 1>") in p
    assert "overlap with the others: that person leans towards the second person, fists up." in p
    assert "overlap with the others: that person claps." in p
    assert "overlap with the others." in p                       # the second person: nothing read
    assert p.count("a young woman with long wavy blonde hair") == 1      # every description once (19:55)
    for w in ("shoes are outside", "% of the image", "Every other person", "grey figure"):
        assert w not in p


def test_h3_prompt_one_of_three():
    p = K.h3_prompt([(2, 2, "")], 3)
    assert "the person exactly as shown in the picture" in p and K.H3_OTHERS in p and "keeps <Picture 1> exactly" not in p


def test_poses_and_group():
    ans = ("Here you go:\n1: stands facing the camera, hands in long gloves resting on the hips, smiling, cut off at the "
           "thighs.\n- 2) raises both fists, in front of person 1\n3: claps\n4: extra\n"
           "Group: the three stand close together, the middle one slightly in front, wearing satin dresses, cut at the thighs.")
    poses, group = K.parse_poses(ans, 3)
    assert poses == {1: "stands facing the camera, smiling, cut off at the thighs",
                     2: "raises both fists, in front of person 1", 3: "claps"}
    assert group == "the three stand close together, the middle one slightly in front, cut at the thighs"
    assert "There are 3 people" in K.POSE_ASK.format(n=3) and "group:" in K.POSE_ASK


def test_h3_size():
    assert K.h3_size(1664, 960, mp=4.0) == (2624, 1504) and K.h3_size(1664, 960, mp=2.0) == (1856, 1088)
