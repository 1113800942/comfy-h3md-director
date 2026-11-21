"""0.5.91 Recast: the prompt enhancer tells who is who by look (names from people_at_start); H3MD writes the prompt.
Answers below are the real enhancer's (test 2026-11-20 17:12, asked for the swap, 5 keyframes of the dance clip)."""
import h3md_keyframe as K

ROSTER = K.parse_roster("伊娃：金色长卷发，浅蓝色丝绸深V连衣裙，戴浅蓝色长手套\n左2: 阿比：棕红色长发，深蓝色缎面吊带短上衣和深蓝色短裙\n"
                        "义父：黑色头发盘起，深蓝色缎面V领裹身长裙")


def test_parse_roster():
    assert [n for n, _ in ROSTER] == ["伊娃", "阿比", "义父"] and ROSTER[1][1].startswith("棕红色长发")
    assert K.parse_roster("黑发盘起的人") == [("人物1", "黑发盘起的人")] and K.parse_roster("") == []
    ask = K.identify_ask(ROSTER, [("阿比", 2), ("伊娃", 3)])
    assert "伊娃：金色长卷发" in ask and "把阿比换成图2中的人，把伊娃换成图3中的人" in ask and "从左数第几个人是谁" in ask
    assert "不修改" not in ask                       # 17:32: 'change nothing' made it write 'add name labels'


def test_identify_real_answers():
    a0 = ("在<image1>上执行三处人脸替换。将<image1>中从左数第1位人物（金色长卷发、穿浅蓝色丝绸深V连衣裙并戴浅蓝色长手套的伊娃）"
          "的面部替换为<image2>中人物的面部；将<image1>中从左数第2位人物（棕红色长发、穿深蓝色缎面吊带短上衣和深蓝色短裙的阿比）"
          "的面部替换为<image3>中人物的面部；将<image1>中从左数第3位人物（黑色头发盘起、穿深蓝色缎面V领裹身长裙的义父）的面部替换")
    assert K.parse_identify(a0, ROSTER) == {"伊娃": 1, "阿比": 2, "义父": 3}
    # 8.33 s: by name for the first, the others not 'from the left'
    a1 = ("画面左侧第1个（最左）人物为金色长卷发、穿浅蓝色丝绸深V连衣裙的伊娃，将其替换为<image2>中的人物；画面中间第2个（中央）"
          "人物为棕红色长发的阿比，将其替换为<image3>中的人物")
    assert K.parse_identify(a1, ROSTER) == {"伊娃": 1}
    # 8.33 s real: no names, people by look only (阿比 义父 伊娃)
    a2 = ("在<image1>的舞蹈室场景中，将画面中从左数第二个人（中心位置、盘起黑色头发、穿深蓝色缎面V领裹身长裙、黑色高跟鞋）替换为"
          "<image4>中的人，将画面中从左数第三个人（右侧、金色长卷发、穿浅蓝色丝绸深V连衣裙、戴浅蓝色长手套）替换为<image2>中的人，"
          "将画面中从左数第一个人（左侧、棕红色长发、穿深蓝色缎面吊带短上衣和深蓝色短裙、黑色长靴）替换为<image3>中的人。")
    assert K.parse_identify(a2, ROSTER) == {"义父": 2, "伊娃": 3, "阿比": 1}
    # 16 s real: picture numbers swapped by the enhancer - places by look still right, pictures never read
    a3 = ("画面最左侧（从左数第1个）的棕红色长发、穿深蓝色缎面吊带短上衣和深蓝色短裙、黑色长靴的人物替换为<image4>中穿黑色T恤的短发男子；"
          "画面中间（从左数第2个）的盘黑发、穿深蓝色缎面V领裹身长裙的人物替换为<image3>中的男子；画面最右侧（从左数第3个）的金长卷发、"
          "穿浅蓝色丝绸深V连衣裙和戴浅蓝色长手套的人物替换为<image2>中的女子。")
    assert K.parse_identify(a3, ROSTER) == {"阿比": 1, "义父": 2, "伊娃": 3}
    assert K.parse_identify("", ROSTER) == {}


def test_roster_answer():
    got = K.parse_roster_answer("左1：金色长卷发，浅蓝色长裙。左2：棕红色长发，深蓝短裙；左3：黑发盘起，深蓝长裙")
    assert got == [("人物1", "金色长卷发，浅蓝色长裙"), ("人物2", "棕红色长发，深蓝短裙"), ("人物3", "黑发盘起，深蓝长裙")]
    assert K.parse_roster_answer("左2：只有第二个") == []


def test_settle_rank():
    assert K.settle_rank(2, 2) == (2, "tracker and enhancer agree")
    assert K.settle_rank(2, None)[0] == 2 and K.settle_rank(None, 3)[0] == 3
    r, note = K.settle_rank(2, 3)
    assert r == 2 and note.startswith("DIFFER")      # the tracker wins (17:32: the enhancer was wrong, tracker right)


def test_recast_prompt():
    p = K.recast_prompt([{"rank": 3, "img": 2, "name": "伊娃", "look": "金色长卷发", "desc": "white tank top, jeans."},
                         {"rank": 1, "img": 3, "name": "", "look": "", "desc": ""}])
    assert "从左数第3个人（伊娃，金色长卷发）整个人换成图2中的人" in p and "全身穿着：white tank top, jeans；" in p
    assert "头部朝向和视线。" in p and "从左数第1个人整个人换成图3中的人" in p and "与图3一致的服装" in p
    assert p.startswith(K.SWAP_HEAD) and p.endswith(K.SWAP_TAIL)
    for w in ("手套", "手臂", "鞋", "文字", "标注"):
        assert w not in p
