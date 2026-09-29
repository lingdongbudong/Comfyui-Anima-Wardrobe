# -*- coding: utf-8 -*-
"""槽位契约自检 —— 不需要启动 ComfyUI。

    python tools/selfcheck.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nodes import (  # noqa: E402
    NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS,
    AnimaTagArbiter, AnimaWardrobeStudio, SLOT_META,
    _entries_from_tokens, _tokenize, arbitrate, render,
)

FAILED = []


def check_schemas():
    """模拟 ComfyUI 加载时的 schema 校验，避免重启才发现写错。"""
    print("\n【0】节点 schema 校验")
    for name, cls in NODE_CLASS_MAPPINGS.items():
        problems = []
        if not isinstance(NODE_DISPLAY_NAME_MAPPINGS.get(name), str):
            problems.append("缺少显示名")
        it = cls.INPUT_TYPES()
        for kind in it:
            if kind not in ("required", "optional", "hidden"):
                problems.append(f"INPUT_TYPES 出现非法键 {kind}")
        for kind in ("required", "optional"):
            for key, spec in (it.get(kind) or {}).items():
                if not isinstance(spec, tuple) or len(spec) < 1:
                    problems.append(f"{kind}.{key} 不是 (type, opts) 元组")
                    continue
                t = spec[0]
                if not isinstance(t, (str, list, tuple)):
                    problems.append(f"{kind}.{key} 的 type 非法: {t!r}")
        rt = getattr(cls, "RETURN_TYPES", None)
        rn = getattr(cls, "RETURN_NAMES", None)
        if rt is None:
            problems.append("缺 RETURN_TYPES")
        elif rn and len(rn) != len(rt):
            problems.append(f"RETURN_NAMES({len(rn)}) 与 RETURN_TYPES({len(rt)}) 长度不一致")
        if not callable(getattr(cls, getattr(cls, "FUNCTION", ""), None)):
            problems.append(f"FUNCTION={getattr(cls, 'FUNCTION', None)!r} 不是方法")
        status = "OK  " if not problems else "FAIL"
        print(f"  [{status}] {name}  ({cls.__name__})"
              + ("" if not problems else "  → " + "; ".join(problems)))
        if problems:
            FAILED.append(f"schema:{name}")
    print(f"  共 {len(NODE_CLASS_MAPPINGS)} 个节点")


def check(name, got, expect_contains, expect_absent=()):
    ok = all(s in got for s in expect_contains) and all(s not in got for s in expect_absent)
    print(("  [OK]  " if ok else "  [FAIL]") + f" {name}")
    print(f"          → {got}")
    if not ok:
        print(f"          期望含 {expect_contains}，不含 {list(expect_absent)}")
        FAILED.append(name)
    return ok


def two_text_sources(t1, t2):
    e1 = _entries_from_tokens(_tokenize(t1)[0], {}, seq=0, source="源1")
    e2 = _entries_from_tokens(_tokenize(t2)[0], {}, seq=1, source="源2")
    return arbitrate(e1 + e2)


def main():
    check_schemas()
    print("\n" + "=" * 74)
    print("槽位契约")
    for slot, meta in SLOT_META.items():
        print(f"  {slot:11s} {meta['label']:6s} → {meta['policy']}")
    print("=" * 74)

    print("\n【1】粘性槽：先写者锁定（服装）")
    decided, shadowed = two_text_sources(
        "1girl, solo, long black hair, school uniform, standing",
        "1girl, solo, short white hair, black leotard, sitting",
    )
    text = render(decided, "normalize")
    check("服装取先写的 school uniform，后写的 black leotard 被遮挡",
          text, ["school uniform"], ["black leotard"])
    check("被遮挡者已登记（可在 UI 查看）", str(shadowed), ["black leotard"])

    print("\n【2】派生槽：后写者覆盖（角色特征 / 身份）")
    check("角色特征取后写的 short white hair",
          text, ["short white hair"], ["long black hair"])
    check("姿势也是粘性槽 → 取先写的 standing",
          text, ["standing"], ["sitting"])

    print("\n【3】三层权威：正文层(default) < 插件层(explicit)")
    studio = AnimaWardrobeStudio()
    body = "1girl, solo, long black hair, off shoulder, standing"   # 正文里的散落服装词
    r = studio.build("", "black leotard", "", "", "", "respect", "respect", "respect",
                     "respect", "override", "preserve", "suggestive", False, False,
                     opt_prompt=body)
    check("造型台选的服装压过正文层散落服装词",
          r["result"][0], ["black leotard"], ["off shoulder"])

    print("\n【4】同层（插件 vs 插件）：粘性槽先写锁定")
    upstream = studio.build("", "school uniform", "", "", "", "respect", "respect",
                            "respect", "respect", "override", "preserve", "suggestive",
                            False, False)["result"][1]
    r2 = studio.build("", "black leotard", "", "", "", "respect", "respect", "respect",
                      "respect", "override", "preserve", "suggestive", False, False,
                      upstream_wardrobe=upstream)
    check("respect：上游插件已写的服装不被覆盖",
          r2["result"][0], ["school uniform"], ["black leotard"])
    r3 = studio.build("", "black leotard", "", "", "", "override", "respect", "respect",
                      "respect", "override", "preserve", "suggestive", False, False,
                      upstream_wardrobe=upstream)
    check("override：显式提权后本节点胜出",
          r3["result"][0], ["black leotard"], ["school uniform"])

    print("\n【5】角色特征不受防覆盖保护（默认 override）")
    up2 = studio.build("drake (nikke), long black hair", "", "", "", "", "respect",
                       "respect", "respect", "respect", "override", "preserve",
                       "suggestive", False, False)["result"][1]
    r4 = studio.build("mai shiranui, short brown hair", "", "", "", "", "respect",
                      "respect", "respect", "respect", "override", "preserve",
                      "suggestive", False, False, upstream_wardrobe=up2)
    check("换角色后特征词整体替换为后写的",
          r4["result"][0], ["short brown hair"], ["long black hair"])
    r5 = studio.build("", "", "", "", "", "respect", "respect", "respect", "respect",
                      "protect", "preserve", "suggestive", False, False,
                      upstream_wardrobe=up2)
    check("protect 时上游角色特征被提权锁定",
          r5["result"][0], ["long black hair"])

    print("\n【6】仲裁器：多源合并")
    arb = AnimaTagArbiter()
    a = "1girl, solo, long black hair, school uniform"
    b = "bedroom, on bed, sitting, black leotard"
    c = "@choujiroo, classroom, standing"
    out = arb.merge("normalize", True, a, b, c)["result"][0]
    check("画师被识别并排到最前", out, ["@choujiroo"], [])
    check("多源合并无重复", out, ["1girl", "school uniform", "bedroom", "sitting"])
    check("服装仍取首写", out, ["school uniform"], ["black leotard"])

    print("\n【7】preserve 模式不打乱原有词序")
    out2 = arb.merge("preserve", True, a, b, c)["result"][0]
    check("preserve 保留 1girl 在前的相对顺序", out2, ["1girl", "school uniform"])
    print(f"          normalize: {out}")
    print(f"          preserve : {out2}")

    print("\n【8】词典判槽抽查")
    idx_hits = {
        "black highleg leotard": "clothing",   # 靠整词包含 "highleg leotard"
        "off shoulder": "clothing",
        "villain lair": "free",
        "bedroom": "scene",
        "cowgirl position": "pose",
        "long black hair": "appearance",
        "@choujiroo": "artist",
        "1girl": "count",
        "eager sucking": "pose",     # 靠整词包含 "sucking" 命中姿势槽
    }
    from nodes import classify_token
    for tok, want in idx_hits.items():
        got = classify_token(tok)
        ok = got == want
        print(f"  [{'OK  ' if ok else 'FAIL'}] {tok!r:26s} → {got:11s}（期望 {want}）")
        if not ok:
            FAILED.append(f"classify:{tok}")

    print("\n【9】⭐ 回归：全空节点必须与原文逐字节一致（接进工作流不得改变出图）")
    real = ("@choujiroo, 1girl, solo, drake (nikke), goddess of victory: nikke, "
            "short white hair, hairclip, red eyes, small breasts, mechanical horns, "
            "black highleg leotard, red armor, red cape, off shoulder, deepthroat, "
            "throat bulge, pov, from above, eager sucking, assertive female, lustful, "
            "looking up, blush, drooling, tongue out, villain lair, throne")
    blank = studio.build("", "", "", "", "", "respect", "respect", "respect", "respect",
                         "override", "preserve", "suggestive", False, False,
                         opt_prompt=real)["result"][0]
    if blank == real:
        print("  [OK  ] 空造型台 = 原样透传")
    else:
        print("  [FAIL] 空造型台改变了正文！")
        print(f"         原文: {real}")
        print(f"         输出: {blank}")
        FAILED.append("passthrough")

    arb_blank = arb.merge("preserve", True, real)["result"][0]
    if arb_blank == real:
        print("  [OK  ] 单源仲裁器 = 原样透传")
    else:
        print("  [FAIL] 仲裁器改变了正文！")
        print(f"         输出: {arb_blank}")
        FAILED.append("passthrough-arbiter")

    print("\n【10】⭐ 一键搭配引擎：批量审计（五档 NSFW 标度 0-4）")
    import wardrobe_rules as wr

    # 混用「规范 key」与「历史中文标签」两种写法 —— 顺便验证兼容层没坏
    LEVEL_LABELS = ["sfw", "1 · 擦边", "explicit", "hardcore", "extreme"]
    N = 900
    bad, n, hit = 0, 0, 0
    miss = {}
    over = []          # 超档（比目标更露骨）—— 这个必须是 0
    under = 0          # 差一档（往安全方向偏）—— 可容忍
    viol_samples = []
    for lv in LEVEL_LABELS:
        want = wr.TARGET_NUM[wr.resolve_level(lv)]
        for ap in (False, True):
            for s2 in range(N // 8):
                for duo in ("", "2girls, hatsune miku"):
                    r = wr.roll_all(level=lv, seed=s2 * 7 + 1,
                                    allow_public=ap, duo_context=duo)
                    n += 1
                    got = r["nsfw"]["num"]
                    if r["violations"]:
                        bad += 1
                        if len(viol_samples) < 3:
                            viol_samples.append((lv, r["violations"], r["text"][:70]))
                    if got == want:
                        hit += 1
                    else:
                        miss[(lv, got)] = miss.get((lv, got), 0) + 1
                        if got > want:
                            over.append((lv, got, r["text"][:70]))
                        else:
                            under += 1

    print(f"  抽取 {n} 次（五档 × 公开/非公开 × 单女/双女）")
    print(f"  搭配/场景/等级 违规次数 : {bad}"
          + ("" if not bad else f"  ← {viol_samples}"))
    print(f"  档位命中率             : {hit}/{n} ({hit * 100 / n:.2f}%)"
          + ("" if not miss else f"  未命中 {miss}"))
    print(f"  超档次数（必须为 0）   : {len(over)}" + ("" if not over else f"  ← {over[:3]}"))
    print(f"  差一档（往安全方向）   : {under}")

    if bad == 0:
        print("  [OK  ] 违规 0 次 —— 所有随机搭配都满足三项判定标准")
    else:
        FAILED.append("roll-violations")
        print("  [FAIL] 存在违规搭配")
    if hit * 100 / n >= 99:
        print("  [OK  ] 档位命中率 ≥ 99%")
    else:
        FAILED.append("roll-level")
        print("  [FAIL] 档位命中率不足 99%")
    if not over:
        print("  [OK  ] 从不超档（擦边档不会抽出成人内容）")
    else:
        FAILED.append("roll-over")
        print("  [FAIL] 出现超档")

    print("\n【11】NSFW 分级判定")
    nsfw_cases = [
        (["school uniform", "long hair", "standing"], 0, "全年龄"),
        (["micro skirt", "off shoulder", "thighhighs"], 1, "擦边"),
        (["see-through shirt", "no bra"], 2, "成人"),
        (["completely nude", "high heels"], 2, "成人"),
        (["pantyhose", "open shirt", "partially undressed"], 2, "成人"),
        (["deepthroat"], 3, "过激"),
        (["vibrator"], 3, "过激"),
        (["blindfold", "ropes"], 3, "过激"),
        (["used condom"], 3, "过激"),
        # ---- 第 5 级（极端，本次新增）----
        (["group sex", "threesome"], 4, "极端·群体/多人"),
        (["fisting", "gaping"], 4, "极端·插入/扩张"),
        (["suspension", "fuck machine"], 4, "极端·拘束/装置"),
        (["peeing", "incontinence"], 4, "极端·极限体液"),
        (["branding", "slave"], 4, "极端·主奴/标记"),
        # 混合：只要有第 5 级内容，整条就按第 5 级算
        (["school uniform", "group sex", "long hair"], 4, "极端混入普通标签"),
    ]
    nsfw_bad = []
    for tags, want, label in nsfw_cases:
        got = wr.grade_nsfw(tags)
        flag = "OK  " if got["num"] == want else "FAIL"
        print(f"  [{flag}] {got['num']} {got['label']:4s}（期望 {want} {label}）"
              f"  {', '.join(tags)[:46]:48s} {list(got['categories'])}")
        if got["num"] != want:
            nsfw_bad.append((tags, want, got["num"]))
    if not nsfw_bad:
        print(f"  [OK  ] {len(nsfw_cases)} 组分级用例全部命中（含第 5 级 6 组）")
    else:
        FAILED.append("nsfw-level")
        print(f"  [FAIL] {nsfw_bad}")

    kept, removed = wr.strip_nsfw("school uniform, see-through shirt, deepthroat", 2)
    if removed == ["deepthroat"] and "deepthroat" not in kept:
        print(f"  [OK  ] 上限过滤正确：剔除 {removed}，保留「{kept}」")
    else:
        FAILED.append("nsfw-strip")
        print(f"  [FAIL] 上限过滤异常：removed={removed} kept={kept}")

    print("\n【12】随机女性角色")
    chars = wr.character_pool()
    not_girl = [c for c in chars if c.get("g") != "1girl"]
    no_trigger = [c for c in chars if not c.get("t")]
    print(f"  角色池 {len(chars)} 个；非女性 {len(not_girl)}；缺 trigger {len(no_trigger)}")
    if chars and not not_girl and not no_trigger:
        print("  [OK  ] 池内全部为女性且都有官方 trigger")
    else:
        FAILED.append("char-pool")
        print("  [FAIL] 角色池数据异常")

    # avoid_repeat 的窗口是 40 —— 连续抽 40 次必须互不重复。
    # （长跑 300 次必然会有重复：TOP200 池只有 197 个候选，去重后 168 个是数学必然，不是 bug）
    seq = []
    for s in range(40):
        r = wr.roll_girl(pool="热门 TOP200", seed=s, avoid_repeat=True)
        seq.append(r["picks"][0]["name"])
    long_run = {wr.roll_girl(pool="热门 TOP200", seed=s)["picks"][0]["name"]
                for s in range(300)}
    print(f"  连续 40 抽：去重后 {len(set(seq))} 个；长跑 300 抽覆盖 {len(long_run)} 个不同角色")
    if len(set(seq)) == 40:
        print("  [OK  ] 避免重复生效（40 次一个不重）")
    else:
        FAILED.append("char-repeat")
        print(f"  [FAIL] 连续 40 抽出现重复：{len(set(seq))} 个不同")

    g3 = wr.roll_girl(pool="热门 TOP200", count=3, seed=7)
    if g3["count"] == 3 and len({p["name"] for p in g3["picks"]}) == 3:
        print(f"  [OK  ] 一次抽 3 个且互不重复："
              f"{[p['name'] for p in g3['picks']]}")
    else:
        FAILED.append("char-multi")
        print("  [FAIL] 多人抽取异常")

    by_work = wr.roll_girl(pool="按作品", keyword="genshin", seed=1)
    wrong = [p for p in by_work["picks"] if "genshin" not in p["copyright"].lower()]
    if by_work["ok"] and not wrong:
        print(f"  [OK  ] 按作品筛选正确：genshin 候选 {by_work['candidates']} 个，"
              f"抽中 {by_work['picks'][0]['name']}")
    else:
        FAILED.append("char-filter")
        print(f"  [FAIL] 按作品筛选异常：{wrong}")

    print("\n【13】双女性识别 → 关系标签")
    duo_cases = [
        ("1girl, solo", False),
        ("2girls, hatsune miku", True),
        ("1girl, 1girl, reimu", True),
        ("multiple girls", True),
        ("1girl, 1boy", False),
        ("2boys", False),
    ]
    duo_bad = []
    for txt, want in duo_cases:
        got = wr.detect_female_duo(txt)
        if got["duo"] != want:
            duo_bad.append((txt, want, got))
    if not duo_bad:
        print("  [OK  ] 6 组双女识别用例全部命中")
    else:
        FAILED.append("duo-detect")
        print(f"  [FAIL] {duo_bad}")

    sfw_rel = wr.duo_relation_tags(1)
    nsfw_rel = wr.duo_relation_tags(2)
    hard_rel = wr.duo_relation_tags(3)
    if "friends" in sfw_rel and "yuri" not in sfw_rel:
        print(f"  [OK  ] NSFW 1（擦边）→ 正常互动: {sfw_rel}")
    else:
        FAILED.append("duo-sfw")
        print(f"  [FAIL] 擦边档关系标签异常: {sfw_rel}")
    if nsfw_rel[0] == "yuri" and wr.grade_nsfw(nsfw_rel)["num"] == 2:
        print(f"  [OK  ] NSFW 2（成人）→ 百合: {nsfw_rel}")
    else:
        FAILED.append("duo-nsfw")
        print(f"  [FAIL] 成人档关系标签异常: {nsfw_rel}")
    if hard_rel[0] == "yuri" and wr.grade_nsfw(hard_rel)["num"] == 3:
        print(f"  [OK  ] NSFW 3（过激）→ 百合: {hard_rel}")
    else:
        FAILED.append("duo-hard")
        print(f"  [FAIL] 过激档关系标签异常: {hard_rel}")

    ex_rel = wr.duo_relation_tags(4)
    if ex_rel[0] == "yuri" and wr.grade_nsfw(ex_rel)["num"] == wr.NSFW_MAX:
        print(f"  [OK  ] NSFW 4（极端，新增）→ 极端百合: {ex_rel}")
    else:
        FAILED.append("duo-extreme")
        print(f"  [FAIL] 极端档关系标签异常: {ex_rel}")
    # 按等级 key 走一遍（前端/节点走的就是这条路）
    ex_rel2 = wr.duo_relation_for_level("extreme")
    if ex_rel2[0] == "yuri" and wr.grade_nsfw(ex_rel2)["num"] == wr.NSFW_MAX:
        print(f"  [OK  ] duo_relation_for_level('extreme') 正确: {ex_rel2}")
    else:
        FAILED.append("duo-extreme-key")
        print(f"  [FAIL] 按 key 取极端关系标签异常: {ex_rel2}")

    # 双女 + SFW → 场景应落日常场所
    daily_hits = 0
    for s2 in range(40):
        r = wr.roll_all(level="0 · 全年龄", seed=s2, duo_context="2girls, miku")
        if r["scene"]["place"] in wr.DUO_SFW_PLACES:
            daily_hits += 1
    if daily_hits >= 38:
        print(f"  [OK  ] 双女 SFW 场景优先日常场所：{daily_hits}/40")
    else:
        FAILED.append("duo-scene")
        print(f"  [FAIL] 双女 SFW 场景只有 {daily_hits}/40 落在日常场所")

    print("\n【14】⭐ 等级合法性回归（针对「level 只能取 …」报错的可测断言）")
    import nodes as _nodes

    LEGAL = set(wr.LEVELS)

    # ① 任何写法都必须收敛到合法值
    probes = ["", None, "suggestive", "SFW", "extreme", "Extreme", "EXTREME",
              "4 · 极端", "4", 4, "3.0", "全年龄", "擦边", "成人", "过激", "极端",
              "不限", "any", "safe", "ecchi", "nsfw", "乱七八糟", "  explicit  "]
    illegal = [(p, wr.resolve_level(p)) for p in probes
               if wr.resolve_level(p) not in LEGAL]
    if not illegal:
        print(f"  [OK  ] resolve_level 对 {len(probes)} 种写法全部返回合法值")
    else:
        FAILED.append("level-resolve")
        print(f"  [FAIL] 出现非法返回值: {illegal}")

    # ② 兼容表 / 控件可选值 / 默认值 都必须在合法集合内
    bad_alias = [(k, v) for k, v in wr.LEVEL_ALIAS.items() if v not in LEGAL]
    if not bad_alias:
        print(f"  [OK  ] 兼容表 {len(wr.LEVEL_ALIAS)} 条别名全部指向合法等级")
    else:
        FAILED.append("level-alias")
        print(f"  [FAIL] 别名指向非法等级: {bad_alias}")

    combo_bad, default_bad = [], []
    for cls in (_nodes.AnimaWardrobeStudio, _nodes.AnimaWardrobeRoll,
                _nodes.AnimaRandomGirl, _nodes.AnimaNSFWGrader):
        req = cls.INPUT_TYPES()["required"]
        name = "roll_level" if "roll_level" in req else (
            "level" if "level" in req else ("max_level" if "max_level" in req else None))
        if not name:
            continue
        extra = [v for v in req[name][0] if v not in LEGAL]
        if extra:
            combo_bad.append((cls.__name__, name, extra))
        default = (req[name][1] or {}).get("default")
        if default not in LEGAL:
            default_bad.append((cls.__name__, name, default))
    if not combo_bad:
        print("  [OK  ] 四个节点的等级控件可选值全部 ⊆ LEVELS（本次报错根因）")
    else:
        FAILED.append("level-combo")
        print(f"  [FAIL] 控件值超出合法范围: {combo_bad}")
    if not default_bad:
        print("  [OK  ] 四个节点的等级默认值全部合法")
    else:
        FAILED.append("level-default")
        print(f"  [FAIL] 默认值不合法: {default_bad}")

    # ③ 路由的归一路径：模拟前端发来的各种写法，收敛后必须合法
    route_probe = ["1 · 擦边", "extreme", "Extreme", None, "", 4, "乱七八糟"]
    route_bad = [(p, wr.resolve_level(p)) for p in route_probe
                 if wr.resolve_level(p) not in LEGAL]
    if not route_bad:
        print(f"  [OK  ] 路由归一：{len(route_probe)} 种请求写法全部可用（不再 400）")
    else:
        FAILED.append("level-route")
        print(f"  [FAIL] {route_bad}")

    print("\n【15】第 5 级（极端）专属内容")
    sizes = {s: len(wr.clothing_pool().get(s, [])) for s in wr.EXTREME_SLOTS}
    if sizes and all(v >= 5 for v in sizes.values()):
        print(f"  [OK  ] 新槽位已装载内容: {sizes}")
    else:
        FAILED.append("extreme-pool")
        print(f"  [FAIL] 新槽位内容不足: {sizes}")

    if "sex machine" in wr.EXTREME_DETAILS:
        print(f"  [OK  ] 极端场景细节: {sorted(wr.EXTREME_DETAILS)}")
    else:
        FAILED.append("extreme-details")
        print("  [FAIL] 极端场景细节缺失")

    cat_probe = {
        "群体/多人": ["group sex"],
        "极端插入/扩张": ["fisting"],
        "极端拘束/装置": ["suspension"],
        "极限体液": ["peeing"],
        "主奴/标记": ["branding"],
    }
    bad_cat = [(lb, wr.grade_nsfw(t)["num"]) for lb, t in cat_probe.items()
               if wr.grade_nsfw(t)["num"] != wr.NSFW_MAX
               or lb not in wr.grade_nsfw(t)["categories"]]
    if not bad_cat:
        print(f"  [OK  ] 5 个新增分类全部判到第 {wr.NSFW_MAX} 级")
    else:
        FAILED.append("extreme-cats")
        print(f"  [FAIL] {bad_cat}")

    leak = []
    for lv in ("sfw", "suggestive", "explicit", "hardcore"):
        for s2 in range(60):
            r = wr.roll_all(level=lv, seed=s2 * 13 + 3)
            hits = [t for t in r["outfit"]["tags"] + r["scene"]["tags"]
                    if t.lower() in wr.EXTREME_TAGS]
            if hits:
                leak.append((lv, hits[:3]))
                break
    if not leak:
        print("  [OK  ] 0-3 级不会抽出第 5 级内容（档位之间不互相污染）")
    else:
        FAILED.append("extreme-leak")
        print(f"  [FAIL] 低等级漏出第 5 级内容: {leak[:3]}")

    place_bad, orphan = [], []
    for s2 in range(60):
        r = wr.roll_all(level="extreme", seed=s2 * 11 + 5)
        if r["scene"]["privacy"] not in ("private", "special"):
            place_bad.append((r["scene"]["place"], r["scene"]["privacy"]))
        by = {}
        for t in r["outfit"]["tags"]:
            by.setdefault(wr.classify_wear(t), []).append(t)
        if by.get("device") and not by.get("act"):
            orphan.append(r["outfit"]["tags"])
    if not place_bad:
        print("  [OK  ] 第 5 级场景只落私密 / 特殊场所（60/60）")
    else:
        FAILED.append("extreme-place")
        print(f"  [FAIL] 第 5 级落到公共场所: {place_bad[:3]}")
    if not orphan:
        print("  [OK  ] 拘束/装置始终有极端行为承载（E1）")
    else:
        FAILED.append("extreme-orphan")
        print(f"  [FAIL] 出现孤立装置: {orphan[:2]}")

    # 极端场景细节只在第 5 级出现
    det_leak = []
    for lv in ("sfw", "suggestive", "explicit", "hardcore"):
        for s2 in range(80):
            r = wr.roll_all(level=lv, seed=s2 * 19 + 9)
            hits = [t for t in r["scene"]["tags"] if t.lower() in wr.EXTREME_DETAILS]
            if hits:
                det_leak.append((lv, hits))
                break
    if not det_leak:
        print("  [OK  ] 极端场景细节不会漏到 0-3 级场景")
    else:
        FAILED.append("extreme-detail-leak")
        print(f"  [FAIL] 场景细节漏出: {det_leak[:3]}")

    print("\n【16】⭐ 新增：姿势随机 / 表情自动匹配 / 巨乳开关")
    # ① 姿势不超档：sfw 档 120 次，抽出的每个姿势标签 NSFW 必须 ≤ 0
    pose_over = []
    for s2 in range(120):
        rp = wr.roll_pose(level="sfw", seed=s2 * 5 + 1)
        for t in rp["tags"]:
            if wr.grade_nsfw([t])["num"] > 0:
                pose_over.append((s2, t, rp["group"]))
                break
    if not pose_over:
        print("  [OK  ] 姿势按等级分层：sfw 档 120 次 0 个超档标签")
    else:
        FAILED.append("pose-over")
        print(f"  [FAIL] sfw 档姿势超档: {pose_over[:3]}")

    # ② 高等级档姿势以性行为组为主（min_level 加权生效）
    hi_cnt = 0
    for s2 in range(80):
        rp = wr.roll_pose(level="hardcore", seed=s2 * 3 + 7)
        if wr.POSE_GROUP_MIN_LEVEL.get(rp["group"], 1) >= 3:
            hi_cnt += 1
    if hi_cnt >= 40:
        print(f"  [OK  ] hardcore 档姿势以性行为组为主（{hi_cnt}/80 命中 ≥3 级组）")
    else:
        FAILED.append("pose-weight")
        print(f"  [FAIL] hardcore 档性行为组占比过低: {hi_cnt}/80")

    # ③ 表情自动匹配：动作 → 情绪 全对
    em_cases = [
        (3, ["masturbation", "fingering"], "pleasured"),
        (4, ["orgasm", "squirting"], "ahegao"),
        (3, ["bondage", "ropes"], "reluctant"),
        (1, ["exposed", "public"], "embarrassed"),
        (3, ["smug", "riding"], "dominant"),
        (0, ["sleeping"], "neutral"),
        (2, ["blowjob"], "pleasured"),
    ]
    em_bad = []
    for n, tags, want in em_cases:
        got = wr.match_expression(n, tags)["emotion"]
        if got != want:
            em_bad.append((tags, want, got))
    if not em_bad:
        print(f"  [OK  ] 表情自动匹配：{len(em_cases)} 组动作→情绪全部命中")
    else:
        FAILED.append("expression-match")
        print(f"  [FAIL] 表情匹配错误: {em_bad}")

    # ④ 一键随机搭配已含姿势+表情，且表情随动作（非纯随机兜底）
    roll_ok = True
    for s2 in range(40):
        r = wr.roll_all(level="hardcore", seed=s2 * 4 + 11)
        if "pose" not in r or "expression" not in r:
            roll_ok = False
            break
        if not r["pose"]["tags"] or not r["expression"]["tags"]:
            roll_ok = False
            break
    if roll_ok:
        print("  [OK  ] 一键随机搭配已含姿势 + 表情，且均为非空")
    else:
        FAILED.append("roll-pose-expr")
        print("  [FAIL] 一键随机缺少姿势或表情")

    # ⑤ 巨乳开关：开启后特征必含 huge breasts 且不含其它胸型词
    gb_bad = []
    for s2 in range(30):
        g = wr.roll_girl(pool="全部女性", count=1, seed=s2 * 6 + 2,
                         huge_breasts=True)
        if not g.get("ok"):
            continue
        feats = [t.lower() for t in g["picks"][0]["features"]]
        if "huge breasts" not in feats:
            gb_bad.append((g["picks"][0]["name"], "缺 huge breasts"))
        other = [t for t in feats
                 if t in ("small breasts", "medium breasts", "large breasts",
                          "flat chest", "flat breasts", "gigantic breasts")]
        if other:
            gb_bad.append((g["picks"][0]["name"], f"残留 {other}"))
    if not gb_bad:
        print("  [OK  ] 巨乳开关：30 次抽取全部为 huge breasts，无其它胸型残留")
    else:
        FAILED.append("huge-breasts")
        print(f"  [FAIL] 巨乳开关异常: {gb_bad[:3]}")

    print("\n" + "=" * 74)
    if FAILED:
        print(f"结论：{len(FAILED)} 项未通过 → {FAILED}")
        return 1
    print("结论：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
