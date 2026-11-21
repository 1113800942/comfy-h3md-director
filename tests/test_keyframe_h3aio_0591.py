"""0.5.91 Recast = the user's H3 Image AIO v1.01 chain: one plain sentence -> official H3 Image enhancer -> one edit."""
import h3md_keyframe as K


def test_swap_sentence():
    s = K.swap_sentence([(3, 2), (1, 3), (2, 4)], 3)
    assert s.startswith("把图1里从左数第1个人替换成图3里的人，从左数第2个人替换成图4里的人，从左数第3个人替换成图2里的人，严格保持图1中")
    assert "严格保持图2、图3、图4里的人的五官外貌" in s and "其他的人" not in s
    s2 = K.swap_sentence([(2, 2)], 3)                  # one of three recast: the others are kept
    assert "从左数第2个人替换成图2里的人，图1里其他的人保持不变，" in s2 and "严格保持图2里的人" in s2
    for w in ("Eva", "金发", "人数不变", "整个人"):        # no looks, no names, no 'keep the count' (18:01)
        assert w not in s


def test_pe_caption():
    assert K.pe_caption('{"expanded_caption": "A woman \\"Eva\\" stands.", "aspect_ratio": "16:9"}') == 'A woman "Eva" stands.'
    assert K.pe_caption('<think>plan {x}</think>\n{"expanded_caption": "B", "aspect_ratio": "1:1"}') == "B"
    assert K.pe_caption('{"expanded_caption": "C, broken json", "aspect_ratio": 16:9}') == "C, broken json"
    assert K.pe_caption("no json here") is None and K.pe_caption('{"expanded_caption": ""}') is None
