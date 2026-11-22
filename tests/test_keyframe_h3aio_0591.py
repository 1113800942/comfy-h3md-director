"""0.5.91 Recast = the user's H3 Image AIO chain: the user's plain sentence -> official H3 Image enhancer -> one edit;
single keyframes can be made again."""
import pytest
import h3md_keyframe as K


def test_swap_sentence_everybody():
    s = K.swap_sentence([(1, 2), (2, 3), (3, 4)], 3)
    assert s.startswith("把图1里从左到右的三个人，替换成图2、图3、图4里的人，严格保持图1中的对应人物的朝向,姿势,动作,手掌姿势,表情,口型,")
    assert "还有前后遮挡关系不变. 严格保持图2、图3、图4里的人的五官外貌,发型发色,服装等特征不变," in s
    s2 = K.swap_sentence([(3, 2), (1, 3), (2, 4)], 3)      # changed places: pictures in the order they stand now
    assert s2.startswith("把图1里从左到右的三个人，替换成图3、图4、图2里的人，")
    assert "严格保持图2、图3、图4里的人" in s2
    s5 = K.swap_sentence([(r, r + 1) for r in range(1, 6)], 5)
    assert s5.startswith("把图1里从左到右的五个人，替换成图2、图3、图4、图5、图6里的人，")


def test_swap_sentence_some():
    s = K.swap_sentence([(2, 2)], 3)
    assert s.startswith("把图1里从左数第2个人替换成图2里的人，图1里其他的人保持不变，严格保持")
    s = K.swap_sentence([(1, 3), (3, 2)], 4)
    assert s.startswith("把图1里从左数第1个人替换成图3里的人，从左数第3个人替换成图2里的人，图1里其他的人保持不变，")
    for w in ("Eva", "金发", "人数不变", "整个人", "灰"):
        assert w not in s


def test_parse_redo():
    assert K.parse_redo("", 5) is None and K.parse_redo("  ", 5) is None
    assert K.parse_redo("2", 5) == [2] and K.parse_redo("4,2，2 第5张", 5) == [2, 4, 5]
    with pytest.raises(K.KeyframeError):
        K.parse_redo("9", 5)


def test_parse_prompts():
    assert K.parse_prompts("", 5) == {}
    assert K.parse_prompts('{"1": "A", "2": " B ", "3": ""}', 5) == {1: "A", 2: "B"}
    assert K.parse_prompts('{"prompts": ["A", "B"]}', 5) == {1: "A", 2: "B"}
    assert K.parse_prompts('{"keyframe 2": "B"}', 5) == {2: "B"}
    assert K.parse_prompts("#1: first line\ngoes on\n#2: second", 5) == {1: "first line goes on", 2: "second"}
    with pytest.raises(K.KeyframeError):
        K.parse_prompts('{"7": "x"}', 5)
    assert "<Picture 1>" in K.AI_README and "prompts.json" in K.AI_README


def test_h3_size():
    assert K.h3_size(1664, 960, mp=4.0) == (2624, 1504)
