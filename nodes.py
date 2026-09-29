# -*- coding: utf-8 -*-
"""Comfyui-Anima-Wardrobe —— Anima 角色造型台 + 跨插件标签仲裁

设计目标（用户 2026-09-29 要求）：
  1. 保留原有「角色标签选择」功能（不重写它，只在其下游追加能力）
  2. 新增角色【服装 / 姿势】的修改能力
  3. 支持选择 anima3-prompt（阿尼玛）Skill 里预设的服装与场景
  4. 可独立成插件，且与其他插件组合时**互不覆盖**

核心机制 = 「槽位契约 + 声明序号」，见 docs/COORDINATION.md。
一句话：**服装/姿势/场景/画师是「粘性槽」——先写者锁定，后到者只登记不写入；
角色特征属「派生槽」——不受保护，换角色即整体替换。**
"""

import json
import os
import random
import re

try:                      # ComfyUI 以「包」的形式加载本目录
    from . import wardrobe_rules
except ImportError:       # 独立脚本（tools/selfcheck.py）直接 import nodes 时
    import wardrobe_rules

SECTION_NAME = "AnimaWardrobe"
HERE = os.path.dirname(os.path.abspath(__file__))
PRESET_PATH = os.path.join(HERE, "presets", "anima_presets.json")

# ============================================================================
# 一、槽位契约
# ============================================================================

# 输出顺序（与 anima3-prompt Skill 的槽位规范一致）
SLOT_ORDER = [
    "artist", "count", "character", "appearance", "relation",
    "clothing", "pose", "expression", "scene", "free",
]

# policy:
#   sticky      = 首写锁定，后到者不得覆盖（用户要求的"已写入不被自动覆盖"）
#   replaceable = 后写覆盖（换主体即整体重算）
SLOT_META = {
    "artist":     {"label": "画师",     "policy": "sticky"},
    "count":      {"label": "人数性别", "policy": "replaceable"},
    "character":  {"label": "角色身份", "policy": "replaceable"},
    "appearance": {"label": "角色特征", "policy": "replaceable",
                   "note": "不受防覆盖保护：换角色即整体替换"},
    "relation":   {"label": "关系/互动", "policy": "replaceable",
                   "note": "双女性时由等级决定：SFW=正常互动 / NSFW=百合"},
    "clothing":   {"label": "服装",     "policy": "sticky"},
    "pose":       {"label": "姿势",     "policy": "sticky"},
    "expression": {"label": "表情",     "policy": "sticky"},
    "scene":      {"label": "场景",     "policy": "sticky"},
    "free":       {"label": "其他/自由", "policy": "replaceable"},
}

TIER_RANK = {"locked": 3, "explicit": 2, "derived": 1, "default": 0}

# 本插件能"提权"的槽位（即 Studio 上那三个 policy 控件作用的槽）
STICKY_SLOTS = [s for s, m in SLOT_META.items() if m["policy"] == "sticky"]


# ============================================================================
# 二、标签归属词典（用于解析"其他插件输出的纯文本"）
# ============================================================================

_PRESET_CACHE = {"mtime": None, "index": None}

COUNT_RE = re.compile(r"^(solo|1girl|1boy|2girls|2boys|3girls|multiple girls|"
                      r"multiple boys|no humans|1other|\d+girls?|\d+boys?)$", re.I)

APPEARANCE_PATTERNS = [
    # 发色/瞳色可带限定词：short white hair / long brown hair / orange eyes
    re.compile(r"^[a-z\-]{1,20}( [a-z\-]{1,20}){0,2} hair$"),
    re.compile(r"^[a-z\-]{1,20}( [a-z\-]{1,20}){0,2} eyes$"),
    re.compile(r"^(twintails|ponytail|braid|bob cut|hime cut|ahoge|sidelocks|"
               r"wavy hair|curly hair|messy hair|wet hair|hair intakes|"
               r"hair between eyes|hair ornament|hair ribbon|hairclip|hair clip|"
               r"mole under eye|eyelashes|tsurime|tareme|heterochromia|"
               r"heart-shaped pupils|fang|pointy ears|animal ears|cat ears|fox ears|"
               r"horns|tail|wings|halo|small breasts|large breasts|medium breasts|"
               r"flat chest|huge breasts|thick thighs|wide hips|narrow waist|"
               r"muscular|slim|petite|chubby|tall|short|pale skin|dark skin|tan|"
               r"tanlines|freckles|skin fang|abs|toned)$"),
]


def _load_presets():
    """读 presets/anima_presets.json，按 mtime 缓存。"""
    try:
        mtime = os.path.getmtime(PRESET_PATH)
    except OSError:
        return {"sections": {}}
    if _PRESET_CACHE["mtime"] != mtime:
        with open(PRESET_PATH, encoding="utf-8") as f:
            _PRESET_CACHE["index"] = json.load(f)
        _PRESET_CACHE["mtime"] = mtime
    return _PRESET_CACHE["index"] or {"sections": {}}


def presets_payload():
    return _load_presets()


def tag_slot_index():
    """{tag_lower: slot} —— 只索引粘性槽，避免误伤自由词序。"""
    data = _load_presets()
    idx = {}
    for section, slot in (("clothing", "clothing"), ("scene", "scene"),
                          ("pose", "pose"), ("expression", "expression")):
        sec = (data.get("sections") or {}).get(section)
        if not sec:
            continue
        for group in sec.get("groups", []):
            for item in group.get("items", []):
                for tag in item.get("tags", []):
                    idx.setdefault(tag.strip().lower(), slot)
    return idx


def classify_token(token, index=None):
    """给一个逗号分隔的 token 判定槽位。判定不出来 → free。

    两级匹配：
      ① 精确命中索引（词典来自 anima3-prompt Skill 的服装/场景/姿势库）
      ② 退一步做整词包含匹配 —— 让 "black highleg leotard" 这类
         词典里没有的复合写法也能靠 "highleg leotard" 认出服装归属。
    """
    t = token.strip()
    if not t:
        return "free"
    low = t.lower()
    if low.startswith("@"):
        return "artist"
    if COUNT_RE.match(low):
        return "count"
    for pat in APPEARANCE_PATTERNS:
        if pat.match(low):
            return "appearance"
    idx = tag_slot_index() if index is None else index
    hit = idx.get(low)
    if hit:
        return hit
    # ② 整词包含：取最长的命中标签，避免 "tail" 之类短词乱抓
    best_len, best_slot = 0, None
    for kw, slot in _loose_index(idx):
        if len(kw) <= best_len:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", low):
            best_len, best_slot = len(kw), slot
    return best_slot or "free"


_LOOSE_CACHE = {"key": None, "items": None}


def _loose_index(idx):
    """按长度倒序的 (tag, slot) 列表，用于整词包含匹配。"""
    key = len(idx)
    if _LOOSE_CACHE["key"] != key:
        items = sorted(((k, v) for k, v in idx.items() if len(k) >= 4 and " " in k),
                       key=lambda kv: -len(kv[0]))
        _LOOSE_CACHE["items"] = items
        _LOOSE_CACHE["key"] = key
    return _LOOSE_CACHE["items"]


# ============================================================================
# 三、声明（claim）构造与解析
# ============================================================================

CLAIM_TYPE = "ANIMA_WARDROBE"
CLAIM_VERSION = 1


def make_claim(slots=None, seq=0, source="unknown"):
    """slots: {slot: {"tags": [...], "tier": "..."}}"""
    entries = []
    for slot, spec in (slots or {}).items():
        tags = [t.strip() for t in (spec.get("tags") or []) if t and t.strip()]
        if not tags:
            continue
        entries.append({
            "slot": slot,
            "tags": tags,
            "tier": spec.get("tier", "explicit"),
            "force": bool(spec.get("force", False)),
            "seq": int(spec.get("seq", seq)),
            "source": spec.get("source", source),
        })
    return {"v": CLAIM_VERSION, "source": source, "entries": entries}


def merge_claims(claims):
    """把多个声明合成一个（不做仲裁），seq 原样保留。"""
    entries = []
    for c in claims:
        if not c:
            continue
        entries.extend(c.get("entries", []))
    return {"v": CLAIM_VERSION, "source": "merged", "entries": entries}


def bump_claims(claim):
    """把一份声明的 seq 整体后移一位，用于「下游声明晚于上游」。"""
    if not claim:
        return 0
    seqs = [e.get("seq", 0) for e in claim.get("entries", [])]
    return (max(seqs) + 1) if seqs else 0


# ============================================================================
# 四、仲裁
# ============================================================================

def arbitrate(entries):
    """按槽位契约仲裁。

    规则（先满足者先胜）：
      R1 提权（force）优先 —— 用户在这一层显式要求覆盖上游时
      R2 层级（locked > explicit > derived > default）
      R3 同层：
           sticky 槽 → seq 最小者胜（首写锁定，后到者不得覆盖）
           replaceable 槽 → seq 最大者胜（角色特征等随主体重算）
      R4 落选者不删除，登记为 shadowed，UI 可见
    """
    by_slot = {}
    for e in entries:
        by_slot.setdefault(e["slot"], []).append(e)

    decided, shadowed = {}, {}
    for slot, items in by_slot.items():
        policy = SLOT_META.get(slot, {}).get("policy", "replaceable")
        forced = [e for e in items if e.get("force")]
        pool = forced or items
        later_wins = bool(forced) or policy == "replaceable"

        top = max(TIER_RANK.get(e.get("tier", "explicit"), 0) for e in pool)
        pool = [e for e in pool if TIER_RANK.get(e.get("tier", "explicit"), 0) == top]
        winner = (max if later_wins else min)(pool, key=lambda e: e.get("seq", 0))

        decided[slot] = winner
        losers = [e for e in items if e is not winner]
        if losers:
            shadowed[slot] = losers
    return decided, shadowed


def render(decided, order_mode="preserve", first_positions=None):
    """渲染最终文本。

    层级模型（三层权威）：正文层(default) < 插件层(explicit) < 锁定层(locked)
      · opt_prompt / 纯文本输入   → default 层（用户已有的提示词）
      · 结构化声明 / 面板选择      → explicit 层（插件显式写入）
      · override / protect 开关   → locked 层（用户强制）
    仲裁时层级高者胜；同层内粘性槽「先写胜」、派生槽「后写胜」。

    order_mode:
      preserve（默认）—— 不打乱原有词序，只删除被遮挡的 token；
                        本节点新增的标签插在该槽最后一个原词之后。
      normalize       —— 按规范槽位顺序重排。
    """
    first_positions = first_positions or {}
    parts, seen = [], set()

    def take(tags):
        out = []
        for t in tags:
            k = t.strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(t.strip())
        return out

    if order_mode == "preserve" and first_positions:
        idx = tag_slot_index()
        orig = [t for t, _ in sorted(first_positions.items(), key=lambda kv: kv[1])]
        win_set = {s: {x.strip().lower() for x in e["tags"]} for s, e in decided.items()}

        kept, last_at = [], {}
        for t in orig:
            slot = classify_token(t, idx)
            entry = decided.get(slot)
            if not entry or t.strip().lower() not in win_set.get(slot, ()):
                continue                      # 该槽被遮挡 → 丢弃
            kept.append(t.strip())
            last_at[slot] = len(kept) - 1

        # 把各槽"新增的"（原句里没有的）标签插到该槽最后出现位置之后；
        # 原句里根本没出现过的槽，其标签统一追加到末尾
        inserts, orphan = {}, []
        for slot, entry in decided.items():
            fresh = [t for t in entry["tags"] if t.strip().lower() not in first_positions]
            if not fresh:
                continue
            if slot in last_at:
                inserts.setdefault(last_at[slot], []).extend(fresh)
            else:
                orphan.extend(fresh)

        out = []
        for i, t in enumerate(kept):
            out.extend(take([t]))
            if i in inserts:
                out.extend(take(inserts[i]))
        out.extend(take(orphan))
        return ", ".join(out)

    for slot in SLOT_ORDER:
        entry = decided.get(slot)
        if entry:
            parts.extend(take(entry["tags"]))
    return ", ".join(parts)


def build_report(decided, shadowed, stats=None):
    lines = []
    for slot in SLOT_ORDER:
        meta = SLOT_META.get(slot, {})
        entry = decided.get(slot)
        if not entry:
            continue
        lock = "锁定" if meta.get("policy") == "sticky" else "可覆盖"
        lines.append(f"✓ {meta.get('label', slot)}[{lock}] ← {entry['source']}"
                     f"（{entry.get('tier')}）: {', '.join(entry['tags'][:4])}"
                     f"{' …' if len(entry['tags']) > 4 else ''}")
    for slot, losers in shadowed.items():
        for e in losers:
            lines.append(f"✗ {SLOT_META.get(slot, {}).get('label', slot)} 已被遮挡"
                         f" ← {e['source']}: {', '.join(e['tags'][:4])}"
                         f"{' …' if len(e['tags']) > 4 else ''}")
    if stats:
        lines.append(f"— 共 {stats.get('tokens', 0)} 个 token，"
                     f"{stats.get('shadowed', 0)} 个被遮挡")
    return "\n".join(lines) or "（无内容）"


# ============================================================================
# 五、节点
# ============================================================================

def _split(tags):
    out, seen = [], set()
    for t in (tags or "").split(","):
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


class AnimaWardrobeStudio:
    """角色造型台：保留角色选择，新增服装 / 姿势 / 场景，默认不覆盖上游已写入的粘性槽。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "character_tags": ("STRING", {"multiline": True, "default": ""}),
                "clothing_tags": ("STRING", {"multiline": True, "default": ""}),
                "pose_tags": ("STRING", {"multiline": True, "default": ""}),
                "expression_tags": ("STRING", {"multiline": True, "default": ""}),
                "scene_tags": ("STRING", {"multiline": True, "default": ""}),
                "clothing_policy": (["respect", "override"], {"default": "respect"}),
                "pose_policy": (["respect", "override"], {"default": "respect"}),
                "expression_policy": (["respect", "override"], {"default": "respect"}),
                "scene_policy": (["respect", "override"], {"default": "respect"}),
                "character_feature_policy": (["override", "protect"],
                                             {"default": "override"}),
                "order_mode": (["preserve", "normalize"], {"default": "preserve"}),
                "roll_level": (list(wardrobe_rules.LEVELS),
                               {"default": "suggestive"}),
                "allow_public": ("BOOLEAN", {"default": False}),
                "huge_breasts": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "opt_prompt": ("STRING", {"forceInput": True}),
                "upstream_wardrobe": (CLAIM_TYPE,),
            },
        }

    RETURN_TYPES = ("STRING", CLAIM_TYPE)
    RETURN_NAMES = ("text", "wardrobe")
    FUNCTION = "build"
    CATEGORY = SECTION_NAME

    def build(self, character_tags, clothing_tags, pose_tags, expression_tags, scene_tags,
              clothing_policy, pose_policy, expression_policy, scene_policy,
              character_feature_policy, order_mode, roll_level, allow_public,
              huge_breasts, opt_prompt="", upstream_wardrobe=None):

        tokens, first_pos = _tokenize(opt_prompt)

        entries = []
        # ① 上游声明（结构化，槽位明确，seq 保持原值 = "先写"）
        if upstream_wardrobe:
            entries.extend(_normalize_entries(upstream_wardrobe.get("entries", []), floor=0))
            base_seq = bump_claims(upstream_wardrobe)
        else:
            base_seq = 0

        # ② 上游纯文本（可能来自任意插件）—— 视为「正文层」，层级低于本节点的插件层写入
        if tokens and not upstream_wardrobe:
            entries.extend(_entries_from_tokens(tokens, first_pos, seq=0,
                                                source="上游正文", tier="default"))
            base_seq = max(base_seq, 1)

        # ③ 本节点的五个槽
        mine = [
            ("character", _split(character_tags), "explicit", False),
            ("clothing", _split(clothing_tags), "explicit",
             clothing_policy == "override"),
            ("pose", _split(pose_tags), "explicit", pose_policy == "override"),
            ("expression", _split(expression_tags), "explicit",
             expression_policy == "override"),
            ("scene", _split(scene_tags), "explicit", scene_policy == "override"),
        ]
        for slot, tags, tier, force in mine:
            if tags:
                entries.append({"slot": slot, "tags": tags, "tier": tier,
                                "force": force, "seq": base_seq,
                                "source": "角色造型台"})

        # ③ 角色特征策略：protect 时把上游的角色特征提权锁定
        if character_feature_policy == "protect":
            for e in entries:
                if e["slot"] == "appearance" and e["source"] != "角色造型台":
                    e["tier"] = "locked"

        decided, shadowed = arbitrate(entries)
        text = render(decided, order_mode, first_pos)
        claim = merge_claims([{"v": CLAIM_VERSION, "entries": entries}])
        report = build_report(decided, shadowed,
                              {"tokens": len(tokens), "shadowed": sum(len(v) for v in shadowed.values())})
        return {"ui": {"anima_wardrobe_report": [report]},
                "result": (text, claim)}


class AnimaWardrobeRoll:
    """一键随机搭配（服务端版）：每次运行工作流自动随机一套合理的服装 + 场景。

    与造型台上的三个按钮共用同一套搭配引擎（wardrobe_rules），因此同样保证合理性。
    seed = -1 每次随机；固定 seed 可复现。
    结果按「插件层」写入，所以会压过正文里的散落服装词，
    但**不会覆盖上游插件已写的粘性槽**（除非把上游的 policy 改成 override）。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "enable_outfit": ("BOOLEAN", {"default": True}),
                "enable_pose": ("BOOLEAN", {"default": True}),
                "enable_expression": ("BOOLEAN", {"default": True}),
                "enable_scene": ("BOOLEAN", {"default": True}),
                "level": (list(wardrobe_rules.LEVELS),
                          {"default": "suggestive"}),
                "allow_public": ("BOOLEAN", {"default": False}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 2147483647}),
                "order_mode": (["preserve", "normalize"], {"default": "preserve"}),
            },
            "optional": {
                "opt_prompt": ("STRING", {"forceInput": True}),
                "upstream_wardrobe": (CLAIM_TYPE,),
            },
        }

    RETURN_TYPES = ("STRING", CLAIM_TYPE, "STRING")
    RETURN_NAMES = ("text", "wardrobe", "report")
    FUNCTION = "run"
    CATEGORY = SECTION_NAME

    def run(self, enable_outfit, enable_pose, enable_expression, enable_scene,
            level, allow_public, seed, order_mode, opt_prompt="",
            upstream_wardrobe=None):
        base = seed if (seed is not None and seed >= 0) else random.randrange(1 << 30)
        result = wardrobe_rules.roll(section="all", level=level, seed=base,
                                     allow_public=allow_public,
                                     duo_context=opt_prompt or "")
        outfit = result["outfit"]
        scene = result["scene"]
        pose = result["pose"]
        expression = result["expression"]
        relation = result.get("relation", [])

        lines = [
            f"🎲 seed={base}  目标等级={wardrobe_rules.level_label(level)}"
            f"  实得={result['nsfw']['num']} · {result['nsfw']['label']}",
            f"   服装形态 : {outfit['form']}（{len(outfit['tags'])} 个标签）",
            f"   姿势     : {pose['group']}"
            + (f"／{pose['label']}" if pose.get('label') else "")
            + f"（{len(pose['tags'])} 个标签）",
            f"   表情     : {expression['emotion']}"
            + (f" · 强度 {expression['intensity']}" if expression.get('emotion') else "")
            + f"（{len(expression['tags'])} 个标签）",
            f"   场所     : {scene['place']}／{wardrobe_rules.PRIVACY_LABEL[scene['privacy']]}"
            f"{' · 室内' if scene['indoor'] else ' · 室外'}",
            f"   裸露判据 : {outfit['why'].get('reason', '')}",
        ]
        if result.get("duo", {}).get("duo"):
            lines.append(f"   双女性识别 : 是（{result['duo']['count']} 位）"
                         f" → 关系标签 {relation}")
        else:
            lines.append("   双女性识别 : 否（未加关系标签）")
        lines.append("   ⚠ 违规 : " + str(result["violations"])
                     if result["violations"] else
                     "   ✓ 服装搭配 / 场景 / 裸露等级 三项校验全部通过")

        tokens, first_pos = _tokenize(opt_prompt)
        entries = []
        if upstream_wardrobe:
            entries.extend(_normalize_entries(upstream_wardrobe.get("entries", []), floor=0))
            base_seq = bump_claims(upstream_wardrobe)
        else:
            base_seq = 0
        if tokens and not upstream_wardrobe:
            entries.extend(_entries_from_tokens(tokens, first_pos, seq=0,
                                                source="上游正文", tier="default"))
            base_seq = max(base_seq, 1)

        if enable_outfit:
            entries.append({"slot": "clothing", "tags": outfit["tags"],
                            "tier": "explicit", "force": False, "seq": base_seq,
                            "source": f"随机搭配(seed={base})"})
        if enable_pose:
            entries.append({"slot": "pose", "tags": pose["tags"],
                            "tier": "explicit", "force": False, "seq": base_seq + 2,
                            "source": f"随机姿势(seed={base})"})
        if enable_expression:
            entries.append({"slot": "expression", "tags": expression["tags"],
                            "tier": "explicit", "force": False, "seq": base_seq + 3,
                            "source": f"随机表情(seed={base})"})
        if enable_scene:
            entries.append({"slot": "scene", "tags": scene["tags"],
                            "tier": "explicit", "force": False, "seq": base_seq + 1,
                            "source": f"随机场景(seed={base})"})
        if relation:
            entries.append({"slot": "relation", "tags": relation,
                            "tier": "explicit", "force": False, "seq": base_seq,
                            "source": f"双女关系(seed={base})"})

        decided, shadowed = arbitrate(entries)
        text = render(decided, order_mode, first_pos)
        claim = merge_claims([{"v": CLAIM_VERSION, "entries": entries}])
        report = build_report(decided, shadowed) + "\n" + "\n".join(lines)
        return {"ui": {"anima_wardrobe_report": [report]},
                "result": (text, claim, "\n".join(lines))}


class AnimaRandomGirl:
    """随机女性角色：从 5981 个女性角色里抽 1~3 个，连官方标签特征一起输出。

    数据源 = 角色库（官方 trigger + 特征 tags + 同人作品数 + 发色/瞳色）。
    · 按 sqrt(同人作品数) 加权 —— 热门角色更容易抽到，但不至于翻来覆去只有那几个
    · 默认开启「避免重复」：最近 40 次抽过的会避开
    · 输出按槽位拆分：性别词 → count 槽；官方 trigger + 特征 → character 槽
      （**刻意不占服装槽** —— 角色的标志性装扮属于角色身份，不该锁死用户挑衣服的能力）
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pool": (list(wardrobe_rules.CHAR_POOLS), {"default": "热门 TOP200"}),
                "keyword": ("STRING", {"default": "",
                                       "tooltip": "池选「按作品/按发色/按瞳色」时生效"}),
                "min_post_count": ("INT", {"default": 0, "min": 0,
                                           "max": 1000000, "step": 100}),
                "character_count": ("INT", {"default": 1, "min": 1, "max": 3}),
                "include_features": ("BOOLEAN", {"default": True}),
                "huge_breasts": ("BOOLEAN", {"default": False}),
                "avoid_repeat": ("BOOLEAN", {"default": True}),
                "level": (list(wardrobe_rules.LEVELS), {"default": "suggestive"}),
                "relation": (["自动（按等级）", "正常互动", "百合向", "不加"],
                             {"default": "自动（按等级）"}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 2147483647}),
                "order_mode": (["preserve", "normalize"], {"default": "preserve"}),
            },
            "optional": {
                "opt_prompt": ("STRING", {"forceInput": True}),
                "upstream_wardrobe": (CLAIM_TYPE,),
            },
        }

    RETURN_TYPES = ("STRING", CLAIM_TYPE, "STRING")
    RETURN_NAMES = ("text", "wardrobe", "report")
    FUNCTION = "run"
    CATEGORY = SECTION_NAME

    def run(self, pool, keyword, min_post_count, character_count,
            include_features, huge_breasts, avoid_repeat, level, relation, seed,
            order_mode, opt_prompt="", upstream_wardrobe=None):
        base = seed if (seed is not None and seed >= 0) else random.randrange(1 << 30)
        got = wardrobe_rules.roll_girl(
            pool=pool, keyword=keyword, min_post=min_post_count,
            count=character_count, include_features=include_features,
            seed=base, avoid_repeat=avoid_repeat,
            huge_breasts=huge_breasts)

        if not got.get("ok"):
            return {"ui": {"anima_wardrobe_report": [got.get("error", "抽取失败")]},
                    "result": (opt_prompt, upstream_wardrobe, got.get("error", ""))}

        lines = [f"🎲 随机女性角色 · 池={pool}"
                 + (f" 关键词={keyword}" if keyword else "")
                 + f"  候选={got['candidates']}  seed={base}"]
        if huge_breasts:
            lines.append("  ⭐ 巨乳开关：已开启，体型强制为 huge breasts")
        for p in got["picks"]:
            lines.append(f"   · {p['name']}（{p['copyright']}）"
                         f"  热度 {p['post_count']}  发色 {p['hair'] or '-'}"
                         f" / 瞳色 {p['eye'] or '-'}  特征 {len(p['features'])} 个")
        lines.append("  " + got["text"])
        if got.get("nsfw"):
            lines.append(f"  NSFW 等级 : {got['nsfw']['num']} / "
                         f"{got['nsfw'].get('max', wardrobe_rules.NSFW_MAX)}"
                         f" · {got['nsfw']['label']}（角色资料本身中性，不含裸体标签）")

        # 双女性（抽 2 个及以上）→ 按等级加关系标签
        rel = []
        if len(got["picks"]) >= 2 and relation != "不加":
            mode = wardrobe_rules.resolve_level(level, "suggestive")
            num = wardrobe_rules.NUM_OF.get(mode, 1)
            if relation == "正常互动":
                rel = wardrobe_rules.duo_relation_tags(0)
            elif relation == "百合向":
                # 「百合向」= 至少百合、但不主动升到第 5 级
                rel = wardrobe_rules.duo_relation_tags(min(max(num, 2), 3))
            else:
                rel = wardrobe_rules.duo_relation_for_level(mode)
            lines.append(f"  双女性识别 : 是（{len(got['picks'])} 位）→ 关系标签 {rel}")
        elif len(got["picks"]) >= 2:
            lines.append("  双女性识别 : 是，但关系标签已关闭")

        tokens, first_pos = _tokenize(opt_prompt)
        entries = []
        if upstream_wardrobe:
            entries.extend(_normalize_entries(upstream_wardrobe.get("entries", []), floor=0))
            base_seq = bump_claims(upstream_wardrobe)
        else:
            base_seq = 0
        if tokens and not upstream_wardrobe:
            entries.extend(_entries_from_tokens(tokens, first_pos, seq=0,
                                                source="上游正文", tier="default"))
            base_seq = max(base_seq, 1)

        # ⚠️ 多角色必须**合并成一条** entry —— 拆成多条的话，同层同序号在 sticky/replaceable
        #    仲裁下只会活一个，另一个角色会被静默丢掉。
        n = len(got["picks"])
        count_tag = ("1girl" if n == 1 else
                     f"{n}girls" if n <= 4 else "multiple girls")
        merged, seen_ch = [], set()
        for p in got["picks"]:
            for t in p["identity_tags"] + p["features"]:
                if t.lower() not in seen_ch:
                    seen_ch.add(t.lower())
                    merged.append(t)

        entries.append({"slot": "count", "tags": [count_tag], "tier": "explicit",
                        "force": False, "seq": base_seq,
                        "source": f"随机角色 x{n}"})
        entries.append({"slot": "character", "tags": merged, "tier": "explicit",
                        "force": False, "seq": base_seq,
                        "source": f"随机角色 x{n}"})
        if rel:
            entries.append({"slot": "relation", "tags": rel, "tier": "explicit",
                            "force": False, "seq": base_seq,
                            "source": "双女关系"})

        decided, shadowed = arbitrate(entries)
        text = render(decided, order_mode, first_pos)
        claim = merge_claims([{"v": CLAIM_VERSION, "entries": entries}])
        report = build_report(decided, shadowed) + "\n" + "\n".join(lines)
        return {"ui": {"anima_wardrobe_report": [report]},
                "result": (text, claim, "\n".join(lines))}


class AnimaNSFWGrader:
    """NSFW 分级：判定成人裸露向等级（0 全年龄 / 1 擦边 / 2 成人 / 3 过激）
    并列出具体内容分类，可选按上限过滤掉超标标签。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (["只报告", "过滤超标标签"], {"default": "只报告"}),
                "max_level": (list(reversed(wardrobe_rules.LEVELS)),
                              {"default": "explicit"}),
            },
            "optional": {
                "text": ("STRING", {"forceInput": True}),
                "wardrobe": (CLAIM_TYPE,),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "INT", "STRING")
    RETURN_NAMES = ("text", "level", "level_num", "report")
    FUNCTION = "grade"
    CATEGORY = SECTION_NAME

    def grade(self, mode, max_level, text="", wardrobe=None):
        src = text or ""
        if not src and wardrobe:
            src = ", ".join(t for e in wardrobe.get("entries", [])
                            for t in e.get("tags", []))
        tags = [t.strip() for t in src.split(",") if t.strip()]
        rep = wardrobe_rules.grade_nsfw(tags)

        out_text = src
        removed = []
        if mode == "过滤超标标签":
            limit = wardrobe_rules.NUM_OF.get(
                wardrobe_rules.resolve_level(max_level, "explicit"),
                wardrobe_rules.NSFW_MAX)
            out_text, removed = wardrobe_rules.strip_nsfw(src, limit)

        lines = [wardrobe_rules.format_nsfw(rep)]
        if removed:
            lines.append(f"已剔除（超过上限的 {len(removed)} 个）: {', '.join(removed[:8])}"
                         + (" …" if len(removed) > 8 else ""))
        elif mode == "过滤超标标签":
            lines.append("已剔除: 无（全部在等级上限内）")
        report = "\n".join(lines)

        return {"ui": {"anima_wardrobe_report": [report]},
                "result": (out_text, rep["label"], rep["num"], report)}


class AnimaTagArbiter:
    """标签仲裁器：汇总多路来源（本插件声明 / 任意插件的纯文本），输出无冲突的最终文本。

    放在链路末端即可对**上游任何插件**的自动写入做防护。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "order_mode": (["normalize", "preserve"], {"default": "normalize"}),
                "strict_shadow": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "source_1": ("*",),
                "source_2": ("*",),
                "source_3": ("*",),
                "source_4": ("*",),
            },
        }

    RETURN_TYPES = ("STRING", CLAIM_TYPE)
    RETURN_NAMES = ("text", "wardrobe")
    FUNCTION = "merge"
    CATEGORY = SECTION_NAME

    def merge(self, order_mode, strict_shadow, source_1=None, source_2=None,
              source_3=None, source_4=None):
        sources = [s for s in (source_1, source_2, source_3, source_4) if s is not None]
        entries, tokens_all, first_pos = [], [], {}
        seq_cursor = 0
        for i, src in enumerate(sources):
            if isinstance(src, dict) and "entries" in src:
                entries.extend(_normalize_entries(src["entries"], floor=seq_cursor))
                seq_cursor = bump_claims({"entries": entries})
            else:
                # 纯文本源 = 正文层；结构化声明 = 插件层，天然高于正文
                toks, pos = _tokenize(str(src))
                for t in toks:
                    first_pos.setdefault(t.lower(), len(first_pos))
                entries.extend(_entries_from_tokens(toks, {}, seq=seq_cursor,
                                                    source=f"文本源{i + 1}",
                                                    tier="default"))
                tokens_all.extend(toks)
                seq_cursor += 1

        decided, shadowed = arbitrate(entries)
        if not strict_shadow:
            for slot, losers in list(shadowed.items()):
                decided.setdefault(slot, losers[0])
                shadowed.pop(slot, None)

        text = render(decided, order_mode, first_pos)
        claim = merge_claims([{"v": CLAIM_VERSION, "entries": entries}])
        report = build_report(decided, shadowed,
                              {"tokens": len(tokens_all),
                               "shadowed": sum(len(v) for v in shadowed.values())})
        return {"ui": {"anima_wardrobe_report": [report]},
                "result": (text, claim)}


class AnimaWardrobeInspect:
    """只读检查器：把任意输入的槽位归属与最终仲裁结果打出来，不产生副作用。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {},
                "optional": {"text": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("report",)
    FUNCTION = "inspect"
    CATEGORY = SECTION_NAME

    def inspect(self, text=""):
        idx = tag_slot_index()
        toks = _split(text)
        buckets = {}
        for t in toks:
            buckets.setdefault(classify_token(t, idx), []).append(t)
        lines = ["【槽位归属识别】"]
        for slot in SLOT_ORDER:
            if slot in buckets:
                lines.append(f"  {SLOT_META[slot]['label']}[{SLOT_META[slot]['policy']}]: "
                             f"{', '.join(buckets[slot])}")
        lines.append(f"  词典规模: {len(idx)} 个已索引标签")
        report = "\n".join(lines)
        return {"ui": {"anima_wardrobe_report": [report]}, "result": (report,)}


# ============================================================================
# 六、内部工具
# ============================================================================

def _tokenize(text):
    """返回 (tokens, {token_lower: 首次出现序号})。"""
    raw = (text or "").strip()
    if not raw:
        return [], {}
    toks = [t.strip() for t in raw.split(",") if t.strip()]
    pos = {}
    for i, t in enumerate(toks):
        pos.setdefault(t.lower(), i)
    return toks, pos


def _entries_from_tokens(tokens, first_pos, seq, source, tier="explicit"):
    idx = tag_slot_index()
    bucket = {}
    for t in tokens:
        bucket.setdefault(classify_token(t, idx), []).append(t)
    return [{"slot": slot, "tags": tags, "tier": tier, "force": False,
             "seq": seq, "source": source}
            for slot, tags in bucket.items()]


def _normalize_entries(entries, floor=0):
    out = []
    for e in entries:
        out.append({
            "slot": e.get("slot", "free"),
            "tags": list(e.get("tags", [])),
            "tier": e.get("tier", "explicit"),
            "force": bool(e.get("force", False)),
            "seq": float(e.get("seq", 0)) + floor,
            "source": e.get("source", "上游声明"),
        })
    return out


# ============================================================================
# 七、注册
# ============================================================================

NODE_CLASS_MAPPINGS = {
    "AnimaWardrobeStudio": AnimaWardrobeStudio,
    "AnimaRandomGirl": AnimaRandomGirl,
    "AnimaNSFWGrader": AnimaNSFWGrader,
    "AnimaWardrobeRoll": AnimaWardrobeRoll,
    "AnimaTagArbiter": AnimaTagArbiter,
    "AnimaWardrobeInspect": AnimaWardrobeInspect,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "AnimaWardrobeStudio": "Anima 角色造型台（服装 / 姿势 / 场景）",
    "AnimaRandomGirl": "Anima 随机女性角色（含官方标签特征）",
    "AnimaNSFWGrader": "Anima NSFW 分级判定（0-3 级 + 内容分类）",
    "AnimaWardrobeRoll": "Anima 一键随机搭配（每次运行重抽）",
    "AnimaTagArbiter": "Anima 标签仲裁器（防覆盖）",
    "AnimaWardrobeInspect": "Anima 槽位归属检查器",
}
