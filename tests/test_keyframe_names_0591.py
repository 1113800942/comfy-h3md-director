"""0.5.91 Recast: the prompt enhancer tells who is who by look (names from people_at_start); H3MD writes the prompt.
Answers below are the real enhancer's (test 2026-11-20 17:12, 5 keyframes of the dance clip)."""
import h3md_keyframe as K

ROSTER = K.parse_roster("伊娃：金色长卷发，浅蓝色丝绸深V连衣裙\n左2: 阿比：棕红色长发，深蓝色短裙\n黑发盘起的人")
NAMES = [n for n, _ in ROSTER]


def test_parse_roster():
    assert ROSTER == [("伊娃", "金色长卷发，浅蓝色丝绸深V连衣裙"), ("阿比", "棕红色长发，深蓝色短裙"), ("人物3", "黑发盘起的人")]
    assert K.parse_roster("") == []
    ask = K.identify_ask(ROSTER)
    assert "伊娃：金色长卷发" in ask and "人物3：黑发盘起的人" in ask and "不修改图片" in ask


def test_identify_real_answers():
    # 0 s: 伊娃 阿比 义父 (the enhancer's own edit prompt, people named in brackets)
    a0 = ("在<image1>上执行三处人脸替换。将<image1>中从左数第1位人物（金色长卷发、穿浅蓝色丝绸深V连衣裙并戴浅蓝色长手套的伊娃）"
          "的面部替换为<image2>中人物的面部；将<image1>中从左数第2位人物（棕红色长发、穿深蓝色缎面吊带短上衣和深蓝色短裙的阿比）"
          "的面部替换为<image3>中人物的面部；将<image1>中从左数第3位人物（黑色头发盘起、穿深蓝色缎面V领裹身长裙的义父）的面部替换")
    r, _ = K.parse_identify(a0, ["伊娃", "阿比", "义父"])
    assert r == {"伊娃": 1, "阿比": 2, "义父": 3}
    # 8.33 s: '画面左侧第1个（最左）人物为...伊娃'
    a1 = ("画面左侧第1个（最左）人物为金色长卷发、穿浅蓝色丝绸深V连衣裙的伊娃，将其替换为<image2>中的人物；画面中间第2个（中央）"
          "人物为棕红色长发的阿比，将其替换为<image3>中的人物；画面右侧第3个（最右）人物为黑色头发盘起的义父")
    r, _ = K.parse_identify(a1, ["伊娃", "阿比", "义父"])
    assert r == {"伊娃": 1}            # '中间第2个' / '右侧第3个' are not 'from the left': not read (tracker used)
    # the form we ask for
    a2 = "左1：阿比，双臂高举，微笑；左2：义父，侧身，低头；左3：伊娃，跳起，看向左边，穿浅蓝长裙"
    r, p = K.parse_identify(a2, ["伊娃", "阿比", "义父"])
    assert r == {"阿比": 1, "义父": 2, "伊娃": 3}
    assert p["阿比"] == "双臂高举，微笑" and p["伊娃"] == "跳起，看向左边"
    assert K.parse_identify("", NAMES) == ({}, {})


def test_roster_answer():
    got = K.parse_roster_answer("左1：金色长卷发，浅蓝色长裙。左2：棕红色长发，深蓝短裙；左3：黑发盘起，深蓝长裙")
    assert got == [("人物1", "金色长卷发，浅蓝色长裙"), ("人物2", "棕红色长发，深蓝短裙"), ("人物3", "黑发盘起，深蓝长裙")]
    assert K.parse_roster_answer("左2：只有第二个") == []


def test_settle_rank():
    assert K.settle_rank(2, 2)[0] == 2
    assert K.settle_rank(2, None) == (2, "enhancer did not place her - tracker's place used")
    assert K.settle_rank(2, 3)[0] == 3 and "TRACKER SAYS 2" in K.settle_rank(2, 3)[1]


def test_recast_prompt():
    p = K.recast_prompt([{"rank": 3, "img": 2, "name": "伊娃", "look": "金色长卷发", "desc": "white tank top, jeans.",
                          "pose": "跳起"},
                         {"rank": 1, "img": 3, "name": "", "look": "", "desc": "", "pose": ""}])
    assert "从左数第3个人（伊娃，金色长卷发）整个人换成图2中的人" in p and "全身穿着：white tank top, jeans；" in p
    assert "头部朝向和视线：跳起。" in p
    assert "从左数第1个人整个人换成图3中的人" in p and "与图3一致的服装" in p
    assert p.startswith(K.SWAP_HEAD) and p.endswith(K.SWAP_TAIL)
    for w in ("手套", "手臂", "鞋"):                 # no single item named (the model does the opposite)
        assert w not in p
