# -*- coding: utf-8 -*-
"""搭配合理性引擎 —— 服装槽位 / 场景槽位 / 裸露分级 / 校验 / 随机抽取

三个判定标准全部在本文件实现，rules 都是显式的、可单测的：
  · 服装搭配合理性  validate_outfit()
  · 场景合理性      validate_scene()
  · 成人裸露向分级  grade_explicitness()

数据来源：presets/anima_presets.json（从 anima3-prompt Skill 提取）
          presets/attire_slots.json（服装槽位表，由 tools/sync_presets.py 生成）
"""

import json
import os
import random
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(HERE, "presets")
PRESETS_PATH = os.path.join(PRESET_DIR, "anima_presets.json")
ATTIRE_PATH = os.path.join(PRESET_DIR, "attire_slots.json")

# ============================================================================
# 一、服装槽位定义
# ============================================================================

# 主体槽：最多命中一个（互斥组）
MAIN_SLOTS = ("set", "onepiece", "two_piece", "swim", "underwear", "nude")
# 极端槽（第 5 级专属）：只在抽取等级 = extreme 时进入候选池
EXTREME_SLOTS = ("act", "device", "mark")
# 叠加槽：有数量上限
WEAR_SLOTS = MAIN_SLOTS + ("top", "bottom", "socks", "shoes",
                           "accessory", "material", "state", "prop", "misc") \
    + EXTREME_SLOTS

SLOT_LIMITS = {
    "set": 1, "onepiece": 1, "two_piece": 1, "swim": 1, "underwear": 1, "nude": 1,
    "top": 1, "bottom": 1, "socks": 1, "shoes": 1,
    "accessory": 2, "material": 2, "state": 2, "prop": 2, "misc": 1,
    # 极端槽：行为 ≤2 / 装置 ≤2 / 体液标记 ≤2
    "act": 2, "device": 2, "mark": 2,
}

SLOT_LABEL = {
    "set": "整套制服/造型", "onepiece": "连衣裙/连体", "two_piece": "上装+下装",
    "swim": "泳装", "underwear": "内衣套装", "nude": "全裸",
    "top": "上装", "bottom": "下装", "socks": "袜", "shoes": "鞋",
    "accessory": "配饰", "material": "材质/改造", "state": "穿着状态",
    "prop": "道具", "misc": "其他",
    "act": "极端行为", "device": "拘束/装置", "mark": "极限体液/标记",
}

# ============================================================================
# 一之二、第 5 级「极端」专属内容包（新增内容，不是给已有标签改名）
# ----------------------------------------------------------------------------
# 这里是一批**新的可抽取标签**，装载在 3 个新槽位上：
#   act    极端行为  —— 群体/多人、极端插入扩张、主奴调教
#   device 拘束/装置 —— 吊缚悬吊、固定装置、机械/密闭
#   mark   极限体液  —— 过量、排泄失禁、分泌标记
# 外加一批**新的场景细节**（EXTREME_DETAILS）。
# ⚠️ 这些标签只在抽取等级 = `extreme`（NSFW 4）时进入候选池；
#    低等级会被 _pool_allowed 按上限挡掉，等级之间不会互相污染。
# ============================================================================
EXTREME_GROUPS = {
    "act": [
        ("群体/多人", ["group sex", "threesome", "ffm threesome", "mmf threesome",
                    "foursome", "gangbang", "orgy", "spitroast", "bukkake"]),
        ("极端插入/扩张", ["double penetration", "triple penetration",
                      "large insertion", "gaping", "stomach bulge", "fisting",
                      "anal fisting", "vaginal fisting", "cervix"]),
        ("主奴/调教", ["slave", "pet play", "branding", "collar and leash",
                    "human pet", "leash in mouth"]),
    ],
    "device": [
        ("吊缚/悬吊", ["suspension", "suspension bondage", "suspension bar",
                    "inverted suspension"]),
        ("固定装置", ["stocks", "pillory", "wooden horse", "bondage furniture"]),
        ("机械/密闭", ["sex machine", "fuck machine", "vacuum bed",
                    "mummification", "restraint cage"]),
    ],
    "mark": [
        ("过量", ["excessive cum", "cum inflation", "cum bath", "wet and messy"]),
        ("排泄/失禁", ["peeing", "urination", "incontinence", "golden shower"]),
        ("分泌/标记", ["lactation", "milking", "squirt"]),
    ],
    # 场景细节：只在 extreme 档才允许出现在场景里
    "scene_details": [
        ("装置痕迹", ["sex machine", "bondage furniture", "suspension bar",
                  "dungeon", "many used condoms"]),
    ],
}

# 槽位索引：这些新标签必须固定归到新槽，不能让通用规则把它们判成 prop / state
EXTREME_SLOT_MAP = {}
for _slot, _groups in EXTREME_GROUPS.items():
    if _slot == "scene_details":
        continue
    for _label, _tags in _groups:
        for _t in _tags:
            EXTREME_SLOT_MAP[_t.lower()] = _slot

EXTREME_TAGS = frozenset(EXTREME_SLOT_MAP)
EXTREME_SLOT_LABEL = {t: f"{SLOT_LABEL[s]}·{t}" for t, s in EXTREME_SLOT_MAP.items()}
# 极端场景细节（第 5 级专属，低等级一律剔除）
EXTREME_DETAILS = frozenset(
    t.lower() for _l, _ts in EXTREME_GROUPS["scene_details"] for t in _ts)

# ---- 精确匹配表（高精度，优先级最高） ----
NUDE_SET = {"completely nude", "nude", "naked", "full nude", "topless bottomless"}

MATERIAL_SET = {
    "silk", "satin", "lace", "thin", "see-through", "sheer", "cotton", "latex",
    "rubber", "glossy", "shiny", "leather", "pvc", "transparent", "translucent",
    "mesh", "lace trim", "wet", "skintight", "second skin", "glossy pvc material",
    "holographic clothing", "transparent vinyl clothing", "transparent pvc",
    "shiny clothes", "wet look", "vinyl",
}

MATERIAL_PATTERNS = [
    # ⚠️ 只收「明确的改造修饰」，不要用 \blatex\b 这类裸材质词 ——
    #    'latex bra'/'latex boots'/'silk robe' 首先是一件衣服，让服装词去接。
    #    裸材质词（latex / silk / satin / denim …）由 MATERIAL_SET 精确匹配兜住。
    r"^see-through", r"^transparent", r"^translucent", r"^sheer\b",
    r"^wet( look| appearance| body| dress| shirt| clothes)?$", r"^wet ",
    r"^glossy", r"^shiny", r"^holographic", r"^black mesh", r"pvc material$",
    r"neon trim", r"^mesh fabric$", r"^fishnet fabric$",
]

STATE_PATTERNS = [
    r"off shoulder", r"shoulder slip", r"strap slip", r"areola slip", r"panties peek",
    r"bra exposed", r"shirt lift", r"skirt lift", r"dress lift", r"clothes lift",
    r"open shirt", r"open jacket", r"open clothes", r"open front", r"open fly",
    r"open kimono", r"open robe", r"unbuttoned", r"unzipped", r"partially undressed",
    r"half-dressed", r"clothes pull", r"pants down", r"pants pull", r"one leg out",
    r"torn clothes", r"ripped clothes", r"torn fabric", r"damaged clothes",
    r"revealing clothes", r"wet clothes", r"wet shirt", r"wet dress",
    r"nipples visible through clothes", r"no panties", r"no bra", r"braless",
    r"bottomless", r"topless", r"clothes around waist", r"clothes on floor",
    r"covered nipples", r"messy clothes", r"untied", r"undressed", r"dress removed",
    r"shirt removed", r"clothes removed", r"one breast out", r"breasts out",
    r"breast curtain", r"nipple slip", r"cameltoe",
    # ↓ 第二轮补充：改造向修饰（cutout / 裁剪 / 破损 / 非对称 / 暴露）
    r"cutout", r"cut-out", r"crotch cut", r"pussy cut", r"side slit", r"high slit",
    r"single side slit", r"hip vent", r"center opening", r"frontless", r"sideless",
    r"backless", r"bare back", r"bare shoulders?", r"bare arms?", r"single bare shoulder",
    r"sleeveless", r"strapless", r"one sleeve", r"single sleeve", r"puffy sleeves",
    r"asymmetrical sleeves", r"asymmetrical legwear", r"asymmetrical docking",
    r"mismatched legwear", r"mismatched gloves", r"single glove", r"one shoe missing",
    r"hood up", r"shrug", r"highleg", r"high cut", r"covered navel", r"see-through silhouette",
    r"battle damage", r"cracked", r"battle scars",
    r"bloodstain", r"blood on clothes", r"oil body", r"linked piercing", r"tape on pussy",
    r"ofuda on pussy", r"groin", r"thigh gap", r"between breasts", r"breasts curtain",
    r"nipples visible", r"breasts on tray", r"exposed pocket", r"exposed chest",
    r"underboob", r"sideboob", r"\bcleavage\b", r"frontal wedgie", r"wedgie",
    r"v-neckline", r"crotch zipper", r"crotchless", r"bare ass", r"exposed pussy",
    r"^naked \w+", r"nipple", r"lingerie visible", r"panties visible",
]

# 放在服装槽判定「之后」才检查的状态词：torn school uniform 仍应算一件制服，
# 只有找不到任何服装词时才退回状态槽
LATE_STATE_PATTERNS = [r"\btorn\b", r"\bripped\b", r"\btears\b"]

# 天气/时辰词（必须用词边界，否则 'train' 会命中 'rain'）
WEATHER_RE = re.compile(
    r"\b(rain|rainy|rainfall|raindrops|snow|snowy|snowfall|blizzard|fog|foggy|mist|"
    r"pavement|cityscape|thunder|storm)\b")

PROP_WORDS = [
    "handcuff", "shackle", "rope", "chain", "duct tape", "tape bondage",
    "ball gag", "bit gag", "ring gag", "cloth gag", "tape over mouth",
    "blindfold", "eye mask", "leash", "o-ring choker", "o-ring harness",
    "vibrator", "dildo", "strap-on", "butt plug", "tail plug", "anal beads",
    "nipple clamp", "clothespin", "artificial vagina", "onahole", "masturbator",
    "lotion", "candle wax", "whipped cream", "body writing", "tally marks",
    "lipstick mark", "handprint", "chastity cage", "chastity belt", "feather",
    "whip", "spreader bar", "condom", "pasties", "bondage outfit", "bondage",
    "harness", "pelvic curtain", "breast curtain", "nipple ring",
]
PROP_EXACT = {"crop", "paddle", "ice", "oil", "belt"}

SHOES_WORDS = [
    "heels", "boot", "boots", "mary janes", "loafers", "sneaker", "sandal",
    "slipper", "barefoot", "geta", "zori", "uwabaki", "shoes", "pumps", "platform",
    "wedges", "mules", "flip-flops", "tabi boots", "high heel", "stiletto",
]

SOCKS_WORDS = [
    "thighhigh", "pantyhose", "fishnet", "socks", "stocking", "tights",
    "leggings", "leg warmers", "kneehigh", "knee-high", "loose socks",
    "bobby socks", "ankle socks", "sheer socks", "sideless thighhighs",
]

SWIM_WORDS = ["swimsuit", "bikini", "slingshot", "swim briefs", "maillot", "monokini"]

UNDERWEAR_WORDS = [
    "bra", "panties", "thong", "g-string", "lingerie", "corset", "babydoll",
    "negligee", "chemise", "bodystocking", "bustier", "camisole", "teddy",
    "garter belt", "bridal garter", "boxer briefs", "briefs", "undershirt",
    "undies", "pajamas", "nightgown", "silk robe", "satin robe", "nightwear",
]

SET_WORDS = [
    "uniform", "serafuku", "maid outfit", "maid headdress", "french maid",
    "nurse", "police", "office lady", "business suit", "flight attendant",
    "stewardess", "teacher", "waitress", "bunny girl", "playboy bunny",
    "bunny suit", "race queen", "kimono", "yukata", "miko", "hanfu",
    "cheongsam", "china dress", "qipao", "armor", "nun", "saint", "witch",
    "idol costume", "stage costume", "suit", "tuxedo", "costume", "harem outfit",
    "santa", "elf", "school uniform", "sailor", "gym uniform", "bloomers uniform",
    "doctor", "ninja outfit", "prison uniform", "sacrament robe", "leather clothing",
]

ONEPIECE_WORDS = [
    "dress", "gown", "leotard", "bodysuit", "catsuit", "jumpsuit", "overalls",
    "sundress", "robe", "cloak", "poncho", "tabard", "habit", "one-piece",
    "playsuit", "romper", "apron dress", "swimsuit dress",
]

TOP_WORDS = [
    "shirt", "blouse", "sweater", "jacket", "coat", "hoodie", "t-shirt", "tshirt",
    "tank top", "crop top", "vest", "cardigan", "parka", "cape", "apron", "tie",
    "necktie", "bowtie", "scarf", "sweatshirt", "turtleneck", "polo", "tunic",
    "bra top", "tube top", "halter", "off-shoulder top", "top",
]
BOTTOM_WORDS = [
    "skirt", "pants", "shorts", "jeans", "trousers", "hakama", "buruma",
    "bloomers", "sarong", "bottom", "culottes", "capri", "bell-bottoms",
    "bike shorts", "hot pants", "miniskirt", "maxi skirt", "chaps",
]
ACCESSORY_WORDS = [
    "hat", "cap", "headdress", "headband", "gloves", "choker", "collar",
    "necklace", "earring", "ribbon", "glasses", "eyewear", "mask", "belt",
    "scarf", "veil", "tiara", "crown", "hair ornament", "hairband", "armband",
    "bracelet", "anklet", "bangle", "brooch", "badge", "whistle", "watch",
    "backpack", "bag", "purse", "helmet", "sunglasses", "eye patch", "bandage",
    "cross necklace", "detached sleeves", "detached collar", "gas mask",
    # ↓ 第二轮补充
    "bunny ears", "bunny tail", "cuffs", "o-ring", "id card", "wings",
    "fox hood", "honggaitou", "eyeshadow", "chest belt", "load bearing vest",
    "barcode tattoo", "piercing", "obi", "bowtie", "hairclip", "collar",
]

# 危险/极短词：只允许整词相等，避免子串误伤
EXACT_ONLY = {"top", "bottom", "belt", "tie", "mask", "hat", "cap", "vest", "cross"}


def _has(tag, words):
    t = tag.lower()
    for w in words:
        if w in EXACT_ONLY:
            if t == w:
                return True
        elif w in t:
            return True
    return False


def classify_wear(tag, use_authority=True):
    """把一个服装标签判到 wear 槽位。优先级从高到低，冲突已被顺序消解。

    结果按 (tag, use_authority) 记忆化 —— 抽取时会反复判同一批标签。
    """
    t = (tag or "").strip().lower().strip(",")
    if not t:
        return "misc"
    if _CACHE["key"] is None:      # 首次调用时惰性加载（O(1) 判断，不做文件 stat）
        _ensure()
    memo_key = (t, use_authority)
    hit = _CLASSIFY_MEMO.get(memo_key)
    if hit:
        return hit
    out = _classify_wear_uncached(t, use_authority)
    _CLASSIFY_MEMO[memo_key] = out
    return out


def _classify_wear_uncached(t, use_authority=True):
    # 0) 第 5 级专属槽位优先（act / device / mark 必须固定归位）
    hit_extreme = EXTREME_SLOT_MAP.get(t)
    if hit_extreme:
        return hit_extreme

    # 1) 权威槽位表优先（由 sync_presets 从 Danbooru tag group 生成的快照）
    if use_authority:
        hit = _authoritative_slot(t)
        if hit:
            return hit

    if t in NUDE_SET:
        return "nude"
    if t in MATERIAL_SET:
        return "material"
    for pat in MATERIAL_PATTERNS:
        if re.search(pat, t):
            return "material"
    for pat in STATE_PATTERNS:
        if re.search(pat, t):
            return "state"
    if t in PROP_EXACT:
        return "prop"
    if t not in EXACT_ONLY and _has(t, PROP_WORDS):
        return "prop"

    # 鞋先于袜：'thigh boots' 属鞋，'thighhighs' 属袜
    if _has(t, SHOES_WORDS):
        return "shoes"
    if _has(t, SOCKS_WORDS):
        return "socks"
    if _has(t, SWIM_WORDS):
        return "swim"
    if _has(t, UNDERWEAR_WORDS):
        return "underwear"
    if _has(t, SET_WORDS):
        return "set"
    if _has(t, ONEPIECE_WORDS):
        return "onepiece"
    if _has(t, TOP_WORDS):
        return "top"
    if _has(t, BOTTOM_WORDS):
        return "bottom"
    if _has(t, ACCESSORY_WORDS):
        return "accessory"
    for pat in LATE_STATE_PATTERNS:
        if re.search(pat, t):
            return "state"
    return "misc"


_CLASSIFY_MEMO = {}
_DATA = {"mtime": None, "data": None}
_CACHE = {"key": None, "attire_map": {}, "clothing_pool": None, "scene_pool": None,
          "pose_pool": None, "expression_pool": None}


def _mt(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def _ensure():
    """一次性加载所有派生数据；按两个预设文件的 mtime 做失效判断。

    ⚠️ 必须在每次抽取开始时调一次，不能在 classify_wear 里做文件 stat ——
    800 个标签 × 几千次抽取会退化成几十万次系统调用（实测直接超时）。
    """
    key = (_mt(PRESETS_PATH), _mt(ATTIRE_PATH))
    if _CACHE["key"] == key:
        return
    _CLASSIFY_MEMO.clear()

    # 服装槽位权威表
    amap = {}
    if os.path.isfile(ATTIRE_PATH):
        with open(ATTIRE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for slot, tags in (data.get("slots") or {}).items():
            for t in tags:
                amap.setdefault(t.lower(), slot)
    _CACHE["attire_map"] = amap

    # 预设
    try:
        with open(PRESETS_PATH, encoding="utf-8") as f:
            _DATA["data"] = json.load(f)
    except OSError:
        _DATA["data"] = {"sections": {}}
    _DATA["mtime"] = key[0]

    # ⚠️ key 必须在下游 build 之前置位：_build_* 会经 presets() 再进 _ensure，
    #    置位晚了一步就会无限递归
    _CACHE["key"] = key
    _CACHE["clothing_pool"] = _build_clothing_pool()
    _CACHE["scene_pool"] = _build_scene_pool()
    _CACHE["pose_pool"] = _build_pose_pool()
    _CACHE["expression_pool"] = _build_expression_pool()
    _POSE_BY_LEVEL.clear()


def _authoritative_slot(tag):
    return _CACHE["attire_map"].get(tag)


# ============================================================================
# 二、成人裸露向分级
# ============================================================================

# 直接命中即判 explicit：裸露部位、性行为、涩涩道具、体液
# 判定口径（写进 docs/ROLL_RULES.md，改动必须同步）：
#   explicit   = 画面里能直接看到性器官 / 乳头 / 全裸 / 性行为 / 性道具 / 体液
#   suggestive = 没有直接裸露，但透视、剪切、半脱、掀起、内衣外露、湿身
#   sfw        = 以上都没有
EXPLICIT_TAGS = {
    "completely nude", "nude", "naked", "spread pussy", "pussy cut",
    "exposed pussy", "bare ass", "butt crack cutout", "crotchless",
    "crotchless panties", "crotch cutout", "crotch zipper", "bottomless",
    "no panties", "topless", "nipples", "nipple slip", "nipple ring",
    "nipples visible through clothes", "areola slip", "breasts out",
    "one breast out", "breast curtain", "breasts curtain", "between breasts",
    "underboob", "sideboob", "underboob cutout", "pasties", "deepthroat",
    "throat bulge", "fellatio", "cunnilingus", "handjob", "paizuri",
    "masturbation", "fingering", "sexual intercourse", "vaginal", "anal",
    "creampie", "cum on body", "cum on sheets", "cum pool", "cum",
    "pussy juice stain", "used condom", "many used condoms", "condom belt",
    "vibrator", "dildo", "double dildo", "strap-on", "butt plug", "tail plug",
    "anal beads", "nipple clamps", "onahole", "artificial vagina",
    "masturbator", "ball gag", "bit gag", "ring gag", "bondage",
    "bondage outfit", "shibari", "pubic hair", "cameltoe", "o-ring harness",
    "body writing", "tally marks", "chastity cage", "chastity belt",
    "sex toy nearby", "vibrator on bed", "dildo on floor", "anal beads on table",
    "tape on pussy", "ofuda on pussy", "covered nipples", "groin",
    "frontal wedgie", "nipple",
}
# 例外：这些虽然含 nipple 字样，但语义是"被遮住"
EXPLICIT_EXCEPTIONS = {"covered nipples", "nipples covered"}

# 模糊命中：解决 'ofuda on pussy' / 'tape on pussy' 这类精确表抓不到的复合写法
EXPLICIT_PATTERNS = [
    r"\bpussy\b", r"\banus\b", r"\bnipples?\b", r"\bareola\b", r"\bdeepthroat\b",
    r"\bfellatio\b", r"\bcunnilingus\b", r"\bhandjob\b", r"\bpaizuri\b",
    r"masturbat", r"\bcreampie\b", r"\bcum\b", r"\banal\b", r"\bcrotchless\b",
    r"\bbottomless\b", r"no panties", r"\btopless\b", r"butt ?plug",
    r"\bdildo\b", r"\bvibrator\b", r"\bcondom\b", r"\bbondage\b", r"\bshibari\b",
    r"\bpasties\b", r"\bunderboob\b", r"\bsideboob\b", r"\bcameltoe\b",
    r"\bchastity\b", r"sex toy", r"^naked (?!jacket|apron|cape|cloak|coat|"
                               r"ribbon|tabard|poncho|vest|kimono|hanfu)",
    r"\bnude\b", r"\bcrotch cut\b", r"\bareola\b",
]

SUGGESTIVE_TAGS = {
    "see-through", "transparent", "translucent", "sheer", "thin",
    "see-through shirt", "transparent shirt", "see-through dress",
    "see-through leotard", "see-through sleeves", "see-through silhouette",
    "micro bikini", "micro skirt", "micro shorts", "micro dress",
    "micro panties", "highleg", "highleg panties", "highleg leotard",
    "high cut", "high slit", "side slit", "single side slit", "hip vent",
    "side cutout", "side cut-out", "waist cutout", "clothing cutout",
    "chest cutout", "navel cutout", "stomach cutout", "midriff cutout",
    "cleavage cutout", "deep v-neckline", "exposed chest", "center opening",
    "off shoulder", "shoulder slip", "strap slip", "bare shoulders",
    "single bare shoulder", "bare arms", "sleeveless", "backless",
    "backless outfit", "bare back", "shirt lift", "skirt lift", "dress lift",
    "clothes lift", "open shirt", "open jacket", "open clothes", "open front",
    "open fly", "unbuttoned", "unzipped", "partially undressed", "half-dressed",
    "pants down", "one leg out", "clothes around waist", "shirt removed",
    "dress removed", "revealing clothes", "strapless", "frontless outfit",
    "sideless outfit", "shrug", "hood up", "wet", "wet clothes", "wet shirt",
    "wet dress", "wet look", "wet body", "wet appearance", "skintight",
    "second skin", "latex", "pvc", "lingerie", "lace lingerie", "babydoll",
    "negligee", "chemise", "bodystocking", "garter belt", "no bra", "braless",
    "bra exposed", "panties peek", "upskirt", "downblouse", "reverse bunnysuit",
    "slingshot swimsuit", "g-string", "thong", "nipples visible",
    "one stocking rolled down", "single thighhigh", "mismatched legwear",
    "asymmetrical legwear", "single glove", "one shoe missing",
    "battle damage", "torn", "ripped", "tears", "cracked", "bloodstain",
    "oil body", "linked piercing", "leather clothing", "hip vent",
    "unworn panties", "clothes on floor", "used tissue",
}
ESCALATE_MIN = None  # 已停用：见下方 grade_explicitness 说明

# （保留常量名以免外部引用报错；不再参与判定）
ESCALATE_TAGS = set()


def _explicit_hits(low):
    """精确表 + 模糊正则表双查（模糊表是必需的：`ofuda on pussy` 这类复合写法精确表抓不到）。"""
    hits = [t for t in low if t in EXPLICIT_TAGS and t not in EXPLICIT_EXCEPTIONS]
    if hits:
        return hits
    for t in low:
        if t in EXPLICIT_EXCEPTIONS:
            continue
        if any(re.search(p, t) for p in EXPLICIT_PATTERNS):
            hits.append(t)
    return hits


def _suggestive_hits(low):
    return [t for t in low if t in SUGGESTIVE_TAGS or _has(t, list(SUGGESTIVE_TAGS))]


def _normalize_tags(tags):
    if isinstance(tags, str):
        return [t.strip().lower() for t in tags.split(",") if t.strip()]
    return [str(t).strip().lower() for t in (tags or []) if str(t).strip()]


def _nsfw_core(low):
    """⭐ 唯一的判定真源：NSFW 等级 + 内容分类。

    1. 命中 群体 / 极端插入扩张 / 极端拘束装置 / 极限体液 / 主奴标记 → 4 极端
    2. 命中 性行为 / 性道具 / 束缚 / 体液                              → 3 过激
    3. 否则 num = max(基线, 命中分类的最高等级)
       基线：explicit 表命中 → 2 / suggestive 表命中 → 1 / 否则 0

    ⚠️ 刻意**不做**「多个暗示标签叠加就升级」的启发式：
    那会让「擦边档」抽出被判过激的内容，档位与结果自相矛盾。
    分级 = 逐标签表的并集，规则可预期、可复现。
    """
    cats = _nsfw_categories(low)
    if any(c in cats for c in EXTREME_CATEGORIES):
        return NSFW_MAX, cats, "命中极端向内容（第 5 级）"
    if any(c in cats for c in HARDCORE_CATEGORIES):
        return 3, cats, "命中过激向内容"
    expl, sug = _explicit_hits(low), _suggestive_hits(low)
    num = 2 if expl else (1 if sug else 0)
    if cats:
        num = max(num, max(NSFW_TABLES[c]["level"] for c in cats))
    reason = ("命中成人裸露词" if expl else
              "命中暗示向标签" if sug else "无暴露向标签")
    return num, cats, reason


def grade_explicitness(tags):
    """三段式投影：num 0→sfw / 1→suggestive / ≥2→explicit。

    ⚠️ 这个函数是 `_nsfw_core` 的**投影**，不再自己判一套。
    历史上两套标度各判各的，直接导致「sfw 档抽出 suggestive 内容」和
    「擦边档抽出 NSFW2 内容」两个 bug。任何新的判定都只能加进 NSFW 分类表。
    """
    low = _normalize_tags(tags)
    num, cats, reason = _nsfw_core(low)
    key = "sfw" if num == 0 else "suggestive" if num == 1 else "explicit"
    hits = [t for c in cats.values() for t in c] or _explicit_hits(low) \
        or _suggestive_hits(low)
    return key, {"reason": reason, "hits": hits[:6]}


# ⭐ 等级词汇表 —— 唯一真源。控件值就是这里的 key（一定是合法值），
#    界面上显示的中文由 locales/*/nodeDefs.json 的 values 映射提供。
LEVELS = ("sfw", "suggestive", "explicit", "hardcore", "extreme", "any")
LEVEL_RANK = {"sfw": 0, "suggestive": 1, "explicit": 2, "hardcore": 3,
              "extreme": 4, "any": 99}
# 每个等级对应的 NSFW 数字（既做上限也做保底）
TARGET_NUM = {"sfw": 0, "suggestive": 1, "explicit": 2, "hardcore": 3,
              "extreme": 4, "any": 99}
# 控件的可选值 = 合法 key 本身（不再用中文当值，避免接口校验对不上）
LEVEL_CHOICES = list(LEVELS)
# 中文显示名：前端报告 / 文档 / 日志共用
LEVEL_ZH = {"sfw": "全年龄", "suggestive": "擦边", "explicit": "成人",
            "hardcore": "过激", "extreme": "极端", "any": "不限"}
# 兼容层：老工作流用过中文标签，外部调用可能传数字或旧 key，一律归一
LEVEL_ALIAS = {
    "0 · 全年龄": "sfw", "1 · 擦边": "suggestive", "2 · 成人": "explicit",
    "3 · 过激": "hardcore", "4 · 极端": "extreme", "不限": "any",
    "全年龄": "sfw", "擦边": "suggestive", "成人": "explicit",
    "过激": "hardcore", "极端": "extreme",
    "0": "sfw", "1": "suggestive", "2": "explicit", "3": "hardcore", "4": "extreme",
    "safe": "sfw", "ecchi": "suggestive", "nsfw": "explicit",
    "hardcore_nsfw": "hardcore", "extreme_nsfw": "extreme",
}


def resolve_level(value, default="suggestive"):
    """把任意等级表示统一成**合法 key**（一定是 LEVELS 之一）。

    ⭐ 契约：**永远不抛异常，返回值永远合法**。
      ① 空值 / 认不出来 → default（default 自身不合法时回退 suggestive）
      ② 中文标签 / 数字 / 旧英文 key → 经 LEVEL_ALIAS 归一
      ③ 已经是合法 key（大小写无关）→ 原样返回
    """
    fallback = default if default in LEVELS else "suggestive"
    if value is None or value == "":
        return fallback
    v = str(value).strip()
    if v in LEVEL_RANK:
        return v
    low = v.lower()
    if low in LEVEL_RANK:
        return low
    if v in LEVEL_ALIAS:
        return LEVEL_ALIAS[v]
    if low in LEVEL_ALIAS:
        return LEVEL_ALIAS[low]
    m = re.match(r"^([0-4])\b", v)          # 「0 · 全年龄」这类带数字前缀的写法
    if m:
        return LEVEL_ALIAS[m.group(1)]
    for zh, key in (("全年龄", "sfw"), ("擦边", "suggestive"), ("成人", "explicit"),
                    ("过激", "hardcore"), ("极端", "extreme"), ("不限", "any")):
        if zh in v:
            return key
    return fallback


def level_label(level):
    """等级 → 中文显示名（带数字前缀，便于在报告里一眼看级别）。"""
    key = resolve_level(level)
    if key == "any":
        return "不限"
    return f"{TARGET_NUM[key]} · {LEVEL_ZH[key]}"


# ============================================================================
# 二之二、NSFW 等级与内容分类
# ============================================================================

# 五级 NSFW 标度（数字越大越露骨）。
# ⚠️ key 直接复用 LEVELS 的规范值 —— 历史上这里另起过 safe/ecchi/nsfw 一套，
#    结果「同一件事两个词」反复打架，现在统一成一套词汇。
NSFW_SCALE = [
    (0, "sfw", "全年龄", "无任何暴露向标签"),
    (1, "suggestive", "擦边", "轻度暗示：露肩 / 短裙 / 内衣轮廓 / 材质诱惑"),
    (2, "explicit", "成人", "明显裸露或透视：全裸 / 性器官 / 乳头 / 内衣外露 / 半脱"),
    (3, "hardcore", "过激", "性行为 / 性道具 / 体液 / 束缚调教"),
    (4, "extreme", "极端", "群体 → 极端插入扩张 / 拘束装置 / 极限体液 / 主奴标记"),
]
NSFW_MAX = 4
NSFW_NUM = {k: n for n, k, _l, _d in NSFW_SCALE}
NSFW_LABEL = {k: l for _n, k, l, _d in NSFW_SCALE}
NSFW_KEY_OF_NUM = {n: k for n, k, _l, _d in NSFW_SCALE}
# 「任意等级表示 → NSFW 数字」：any（不限）按顶格 4 处理，用于场所/细节的承载力判定
NUM_OF = {k: min(v, NSFW_MAX) for k, v in TARGET_NUM.items()}

# 内容分类表：用于回答「这个提示词里到底有什么成人向内容」
NSFW_TABLES = {
    "nudity": {
        "label": "裸露", "level": 2,
        "tags": {"completely nude", "nude", "naked", "topless", "bottomless",
                 "no panties", "pussy cut", "exposed pussy", "spread pussy",
                 "bare ass", "nipples", "nipple slip", "nipples visible through clothes",
                 "areola slip", "breasts out", "one breast out", "breast curtain",
                 "breasts curtain", "underboob", "sideboob", "underboob cutout",
                 "crotchless", "crotchless panties", "crotch cutout", "pasties",
                 "nipple ring", "cameltoe", "pubic hair", "tape on pussy",
                 "ofuda on pussy", "groin", "nipples visible"},
        "patterns": [r"\bpussy\b", r"\banus\b", r"\bnipples?\b", r"\bareola\b",
                     r"\bnude\b", r"\btopless\b", r"bottomless", r"no panties",
                     r"crotchless", r"\bcameltoe\b", r"\bunderboob\b", r"\bsideboob\b"],
    },
    "see_through": {
        "label": "透视/湿身", "level": 2,
        "tags": {"see-through", "transparent", "translucent", "sheer",
                 "see-through shirt", "transparent shirt", "see-through dress",
                 "see-through leotard", "see-through sleeves", "see-through silhouette",
                 "wet", "wet clothes", "wet shirt", "wet dress", "wet look",
                 "wet body", "wet appearance", "skintight", "second skin",
                 "lace lingerie", "lingerie", "bodystocking", "garter belt",
                 "no bra", "braless", "breast curtain", "reverse bunnysuit",
                 "transparent heels", "transparent pvc", "holographic clothing"},
        "patterns": [r"^see-through", r"^transparent", r"^translucent",
                     r"^wet\b", r"^wet ", r"\bsheer\b", r"see-through"],
    },
    "undressed": {
        "label": "半脱/掀起", "level": 2,
        "tags": {"shoulder slip", "strap slip", "areola slip", "panties peek",
                 "bra exposed", "shirt lift", "skirt lift", "dress lift",
                 "clothes lift", "open shirt", "open jacket", "open clothes",
                 "open front", "open fly", "open kimono", "open robe",
                 "unbuttoned", "unzipped", "partially undressed", "half-dressed",
                 "clothes pull", "pants down", "pants pull", "one leg out",
                 "clothes around waist", "shirt removed", "dress removed",
                 "clothes removed", "upskirt", "downblouse", "exposed chest",
                 "revealing clothes", "clothes on floor", "unworn panties",
                 "single bare shoulder", "one stocking rolled down",
                 "one shoe missing", "mismatched legwear", "asymmetrical legwear"},
        "patterns": [r"slip$", r"\bopen (shirt|jacket|front|fly|kimono|robe)\b",
                     r"undressed", r"half-dressed", r"pants down",
                     r"\bupskirt\b", r"\bdownblouse\b"],
    },
    "cutout": {
        "label": "裁剪/镂空", "level": 1,
        "tags": {"micro bikini", "micro skirt", "micro shorts", "micro dress",
                 "micro panties", "highleg", "highleg panties", "highleg leotard",
                 "high cut", "high slit", "side slit", "single side slit", "hip vent",
                 "side cutout", "side cut-out", "waist cutout", "clothing cutout",
                 "chest cutout", "navel cutout", "stomach cutout", "midriff cutout",
                 "cleavage cutout", "deep v-neckline", "center opening",
                 "cleavage", "off shoulder", "bare shoulders", "bare arms",
                 "bare back", "backless", "backless outfit", "sleeveless",
                 "strapless", "frontless outfit", "sideless outfit",
                 "slingshot swimsuit", "g-string", "thong", "crop top",
                 "crop top overhang", "short shorts", "lowleg pants",
                 "exposed pocket", "thigh gap"},
        "patterns": [r"cutout", r"cut-out", r"micro (skirt|shorts|dress|bikini|panties)",
                     r"^backless", r"bare (shoulders?|arms?|back)"],
    },
    "yuri": {
        "label": "百合", "level": 2,
        "tags": {"yuri", "girls love", "shoujo ai", "lesbian", "yuri couple",
                 "kiss", "french kiss"},
        # ⚠️ holding hands / looking at each other / love 刻意不收 —— 它们是通用互动词，
        #    收进来会把「SFW 双人正常互动」顶到 2 级
        "patterns": [r"\byuri\b", r"girls love", r"\blesbian\b", r"shoujo ai"],
    },
    # ===== 第 5 级（极端）专属分类：新增内容，只有 extreme 档才会命中 =====
    "group": {
        "label": "群体/多人", "level": 4,
        "tags": {"group sex", "threesome", "ffm threesome", "mmf threesome",
                 "foursome", "gangbang", "orgy", "spitroast", "bukkake"},
        "patterns": [r"group sex", r"\bgangbang\b", r"\borgy\b", r"threesome",
                     r"\bfoursome\b", r"spitroast", r"\bbukkake\b"],
    },
    "extreme_insert": {
        "label": "极端插入/扩张", "level": 4,
        "tags": {"double penetration", "triple penetration", "large insertion",
                 "gaping", "stomach bulge", "fisting", "anal fisting",
                 "vaginal fisting", "cervix"},
        "patterns": [r"double penetration", r"triple penetration",
                     r"large insertion", r"\bgaping\b", r"stomach bulge",
                     r"\bfisting\b", r"\bcervix\b"],
    },
    "extreme_bondage": {
        "label": "极端拘束/装置", "level": 4,
        "tags": {"suspension", "suspension bondage", "suspension bar",
                 "inverted suspension", "stocks", "pillory", "wooden horse",
                 "bondage furniture", "sex machine", "fuck machine",
                 "vacuum bed", "mummification", "restraint cage"},
        "patterns": [r"suspension", r"\bpillory\b", r"wooden horse",
                     r"bondage furniture", r"(sex|fuck) machine",
                     r"vacuum bed", r"mummification"],
    },
    "extreme_fluid": {
        "label": "极限体液", "level": 4,
        "tags": {"excessive cum", "cum inflation", "cum bath", "wet and messy",
                 "peeing", "urination", "incontinence", "golden shower",
                 "lactation", "milking", "squirt"},
        "patterns": [r"excessive cum", r"cum inflation", r"cum bath",
                     r"wet and messy", r"\bpeeing\b", r"\burination\b",
                     r"\bincontinence\b", r"golden shower", r"\bsquirt"],
    },
    "marked": {
        "label": "主奴/标记", "level": 4,
        "tags": {"slave", "pet play", "branding", "collar and leash",
                 "human pet", "leash in mouth"},
        "patterns": [r"\bbranding\b", r"\bslave\b", r"pet play",
                     r"collar and leash", r"human pet"],
    },
    "sexual": {
        "label": "性行为", "level": 3,
        "tags": {"deepthroat", "throat bulge", "fellatio", "cunnilingus", "handjob",
                 "paizuri", "masturbation", "fingering", "sexual intercourse",
                 "vaginal", "anal", "creampie", "doggystyle", "missionary",
                 "cowgirl position", "prone bone", "standing sex", "spread legs",
                 "presenting", "tribadism", "scissoring", "breast sucking",
                 "grinding", "straddling"},
        "patterns": [r"\bdeepthroat\b", r"\bfellatio\b", r"\bcunnilingus\b",
                     r"\bhandjob\b", r"\bpaizuri\b", r"masturbat", r"\bcreampie\b"],
    },
    "toy": {
        "label": "性道具", "level": 3,
        "tags": {"vibrator", "remote control vibrator", "dildo", "double dildo",
                 "strap-on", "suction cup dildo", "butt plug", "tail plug",
                 "anal beads", "nipple clamps", "clothespins", "onahole",
                 "artificial vagina", "masturbator", "chastity cage", "chastity belt",
                 "sex toy nearby", "vibrator on bed", "dildo on floor",
                 "anal beads on table", "spreader bar", "crop", "paddle"},
        "patterns": [r"\bdildo\b", r"\bvibrator\b", r"butt ?plug", r"anal beads",
                     r"sex toy", r"\bchastity\b"],
    },
    "bondage": {
        "label": "束缚/BDSM", "level": 3,
        "tags": {"bondage", "bondage outfit", "shibari", "ball gag", "bit gag",
                 "ring gag", "cloth gag", "tape over mouth", "blindfold", "eye mask",
                 "leash", "o-ring choker", "o-ring harness", "harness", "chest belt",
                 "handcuffs", "shackles", "ropes", "chains", "duct tape",
                 "tape bondage", "spiked collar", "red rope"},
        "patterns": [r"\bbondage\b", r"\bshibari\b", r"\bhandcuffs?\b", r"\bshackles?\b"],
    },
    "fluid": {
        "label": "体液/事后", "level": 3,
        "tags": {"cum", "cum on body", "cum on sheets", "cum pool", "creampie",
                 "pussy juice stain", "used condom", "many used condoms",
                 "condom belt", "condom wrapper", "condom box", "used tissue",
                 "sweat stain", "oil body", "candle wax", "whipped cream", "lotion",
                 "body writing", "tally marks", "handprint", "lipstick mark"},
        "patterns": [r"\bcum\b", r"\bcondom\b", r"pussy juice"],
    },
    "fetish": {
        "label": "暴露癖/情境", "level": 1,
        "tags": {"exposed", "against window", "outdoors", "public", "recording",
                 "webcam", "streaming", "torn", "ripped", "tears", "battle damage",
                 "bloodstain", "blood on clothes", "linked piercing",
                 "single glove", "lace trim", "neon trim"},
        "patterns": [r"\btorn\b", r"\bripped\b", r"\btears\b"],
    },
    "suggestive": {
        "label": "轻度暗示", "level": 1,
        "tags": {"nightgown", "babydoll", "negligee", "chemise", "camisole",
                 "corset", "bustier", "sundress", "silk robe", "satin robe",
                 "pajamas", "shrug", "hood up", "oversized shirt",
                 "boyfriend shirt", "boyfriend shirt"},
        "patterns": [r"crop top", r"oversized shirt"],
    },
}
# 命中这些分类即判过激（第 4 级上限）
HARDCORE_CATEGORIES = ("sexual", "toy", "bondage", "fluid")
# 命中这些分类即判极端（第 5 级）—— 直接升到标度顶格
EXTREME_CATEGORIES = ("group", "extreme_insert", "extreme_bondage",
                      "extreme_fluid", "marked")
# 分类标签的展示顺序（露骨度从高到低）
CATEGORY_ORDER = ("group", "extreme_insert", "extreme_bondage", "extreme_fluid",
                  "marked", "sexual", "toy", "bondage", "fluid", "nudity",
                  "see_through", "undressed", "yuri", "cutout", "fetish",
                  "suggestive")


def _nsfw_categories(low_tags):
    hits = {}
    for cat, spec in NSFW_TABLES.items():
        found = []
        for t in low_tags:
            if t in spec["tags"] or any(re.search(p, t) for p in spec.get("patterns", ())):
                found.append(t)
        if found:
            hits[cat] = found
    return hits


def grade_nsfw(tags):
    """返回 NSFW 等级 + 内容分类明细。

    等级判定（第 5 级为本次新增）：
      · 命中 群体 / 极端插入扩张 / 极端拘束装置 / 极限体液 / 主奴标记 → 4 极端
      · 命中 性行为/性道具/束缚/体液                                  → 3 过激
      · 否则 取 max(基线等级, 命中分类的等级)                         → 0/1/2
    基线等级复用 grade_explicitness（不另写一套匹配逻辑，避免两套表打架）。
    """
    low = _normalize_tags(tags)
    num, cats, reason = _nsfw_core(low)
    level = NSFW_KEY_OF_NUM[num]
    detail = {NSFW_TABLES[c]["label"]: cats[c]
              for c in CATEGORY_ORDER if c in cats}
    return {
        "level": level, "num": num, "label": NSFW_LABEL[level],
        "max": NSFW_MAX,
        "scale": [{"num": n, "key": k, "label": l, "desc": d}
                  for n, k, l, d in NSFW_SCALE],
        "categories": detail,
        "hits": [t for c in CATEGORY_ORDER if c in cats for t in cats[c]],
        "base": {0: "sfw", 1: "suggestive", 2: "explicit", 3: "explicit",
                 4: "explicit"}[num],
        "reason": reason,
        "hardcore_categories": HARDCORE_CATEGORIES,
        "extreme_categories": EXTREME_CATEGORIES,
        "is_extreme": num >= NSFW_MAX,
    }


def format_nsfw(report):
    """把 grade_nsfw 的结果排成人类可读的多行文本。"""
    lines = [f"NSFW 等级 : {report['num']} / {report.get('max', NSFW_MAX)}"
             f"  ·  {report['label']}（{report['level']}）",
             f"判定基线 : {report['reason']}"]
    if report["categories"]:
        lines.append("成人向内容 :")
        for label, items in report["categories"].items():
            lines.append(f"  · {label}（{len(items)}）: {', '.join(items[:5])}"
                         + (" …" if len(items) > 5 else ""))
    else:
        lines.append("成人向内容 : 无")
    return "\n".join(lines)


def strip_nsfw(text, max_num=1):
    """把超过 max_num 的成人向标签从文本里剔掉，返回 (剩余文本, 被剔除的标签)。"""
    removed = []
    keep = []
    for t in str(text or "").split(","):
        t = t.strip()
        if not t:
            continue
        n = grade_nsfw([t])["num"]
        if n > max_num:
            removed.append(t)
        else:
            keep.append(t)
    return ", ".join(keep), removed


# ============================================================================
# 三、服装搭配合理性校验
# ============================================================================

def _forms_present(slot_set):
    """把槽位集合折算成「主体形态」集合。分体的 top+bottom 属于同一种形态。"""
    out = set()
    for s in slot_set:
        if s in ("top", "bottom"):
            out.add("two_piece")
        elif s in ("set", "onepiece", "swim", "underwear", "nude"):
            out.add(s)
    return out


def validate_outfit(tags, level=None):
    """返回违规说明列表；空列表 = 搭配合理。"""
    from collections import defaultdict
    bad = []
    by = defaultdict(list)
    for t in tags:
        by[classify_wear(t)].append(t)

    forms = _forms_present(by.keys())
    if len(forms) > 1:
        bad.append(f"主体冲突：同时出现 {sorted(forms)}"
                   f"（制服/连体/分体/泳装/内衣/全裸 六选一）")

    for slot, limit in SLOT_LIMITS.items():
        items = by.get(slot, [])
        if len(items) > limit:
            bad.append(f"{SLOT_LABEL[slot]}超量（{len(items)}>{limit}）：{items[:4]}")

    if by.get("nude"):
        # 全裸只与「主体服装」互斥；袜/鞋/配饰/材质/状态/道具都可以保留
        # （Skill §8.4.6：completely nude, naked jacket, open jacket, high heels 是经典搭配）
        BODY_SLOTS = ("set", "onepiece", "top", "bottom", "swim", "underwear")
        others = [t for s in BODY_SLOTS for t in by.get(s, [])]
        if others:
            bad.append(f"全裸与主体服装并存：{others[:4]}")

    low = [t.lower() for t in tags]
    joined = ", ".join(low)
    if re.search(r"pantyhose|stockings|thighhighs", joined) and "barefoot" in low:
        if "torn pantyhose" not in joined and "ripped stockings" not in joined:
            bad.append("丝袜与光脚互斥（除非 torn pantyhose）")
    if re.search(r"lingerie|babydoll|negligee|chemise", joined):
        if re.search(r"no panties|bottomless", joined):
            bad.append("内衣套装与 no panties / bottomless 互斥")
    if by.get("material") and not (by.get("set") or by.get("onepiece") or by.get("top")
                                   or by.get("bottom") or by.get("swim")
                                   or by.get("underwear")):
        bad.append(f"材质/改造无依附对象：{by['material']}")

    # ---- 第 5 级（极端）专属合理性 ----
    # E1 装置必须有行为承载：吊缚 / 机械 / 固定装置不能孤立出现
    if by.get("device") and not (by.get("act") or by.get("state")):
        bad.append(f"拘束/装置缺少行为承载：{by['device']}（需配极端行为或穿着状态）")
    # E2 极端档不与「内衣套装 / 泳装」这类主体并存（与装置/行为语义冲突）
    if grade_nsfw(tags)["num"] >= NSFW_MAX and (by.get("underwear") or by.get("swim")):
        bad.append(f"极端档与内衣套装/泳装主体冲突："
                   f"{by.get('underwear', []) + by.get('swim', [])}")

    if level is not None:
        got = grade_explicitness(tags)[0]
        if got != level and "any" not in str(level):
            pass  # 由抽取器保证，这里不重复判
    return bad


# ---------------------------------------------------------------------------
# 抽取后的兜底修复：定点删标签，不是暴力乱删
# ---------------------------------------------------------------------------

# 兜底删除顺序：装饰性槽先让路，第 5 级专属槽排在其后（保护目标内容），
# 主体槽最后 —— 避免「为了修一个材质违规，把整轮的目标等级一起删掉」
DROP_FIRST = ("misc", "state", "material", "accessory", "prop", "shoes", "socks",
              "act", "device", "mark",
              "top", "bottom", "onepiece", "set", "swim", "underwear", "nude")


def repair_outfit(tags, max_pass=6):
    """把违规的搭配修到通过校验为止。全部是定点规则。"""
    out = list(tags)

    def has(pat):
        return any(re.search(pat, t.lower()) for t in out)

    def drop(pred):
        nonlocal out
        out = [t for t in out if not pred(t)]

    # 1) 丝袜 × 光脚 → 丢 barefoot
    if has(r"pantyhose|stockings|thighhighs") and not has(r"torn pantyhose|ripped stockings"):
        drop(lambda t: t.lower() == "barefoot")

    # 2) 内衣套装 × no panties / bottomless → 丢状态词
    if has(r"lingerie|babydoll|negligee|chemise"):
        drop(lambda t: bool(re.search(r"no panties|bottomless", t.lower())))

    # 3) 全裸 × 主体服装 → 丢主体服装
    if any(t.lower() in NUDE_SET for t in out):
        drop(lambda t: classify_wear(t) in
             ("set", "onepiece", "top", "bottom", "swim", "underwear"))

    # 4) 主体冲突 → 只保留先出现的那一种形态
    seen_form, keep = None, []
    for t in out:
        s = classify_wear(t)
        f = "two_piece" if s in ("top", "bottom") else (
            s if s in ("set", "onepiece", "swim", "underwear", "nude") else None)
        if f:
            if seen_form is None:
                seen_form = f
            if f != seen_form:
                continue
        keep.append(t)
    out = keep

    # 5) 各槽超量 → 保留前 N 个
    from collections import defaultdict
    count = defaultdict(int)
    keep = []
    for t in out:
        s = classify_wear(t)
        if count[s] >= SLOT_LIMITS.get(s, 1):
            continue
        count[s] += 1
        keep.append(t)
    out = keep

    # 7) 第 5 级：装置缺少行为承载 → 丢装置
    if any(classify_wear(t) == "device" for t in out) and \
            not any(classify_wear(t) in ("act", "state") for t in out):
        drop(lambda t: classify_wear(t) == "device")

    # 8) 第 5 级：极端档 → 丢掉内衣套装 / 泳装主体
    if grade_nsfw(out)["num"] >= NSFW_MAX:
        drop(lambda t: classify_wear(t) in ("underwear", "swim"))

    # 6) 兜底：仍有违规就按 DROP_FIRST 顺序继续删
    for _ in range(max_pass):
        if not validate_outfit(out):
            break
        idx = min(range(len(out)),
                  key=lambda i: DROP_FIRST.index(classify_wear(out[i]))
                  if classify_wear(out[i]) in DROP_FIRST else len(DROP_FIRST))
        out.pop(idx)
    return out


# ============================================================================
# 四、场景槽位与合理性
# ============================================================================

PLACE_PRIVACY = {
    "私密空间": "private", "半公开空间": "semi",
    "公共空间": "public", "特殊场景": "special",
}
PRIVACY_LABEL = {"private": "私密", "semi": "半公开", "public": "公共", "special": "特殊"}
# 各隐私场所能承载的 NSFW 数字上限（第 5 级只在私密 / 特殊场所成立）
PRIVACY_MAX_NUM = {"private": NSFW_MAX, "special": NSFW_MAX, "semi": 1, "public": 1}
# 各隐私等级允许的服装裸露等级 —— 从 PRIVACY_MAX_NUM 派生，
# 以后再加新等级会自动跟上，不会再出现「表里漏了一档」。
PRIVACY_ALLOWS = {
    p: {k for k in LEVELS if k != "any" and NUM_OF[k] <= m}
    for p, m in PRIVACY_MAX_NUM.items()
}
OUTDOOR_PLACES = {"公园/长椅", "海滩/泳池", "废墟/战后", "楼梯间", "神社/寺庙"}
# 3 级（过激）起允许的场景细节
EXPLICIT_DETAILS = {
    "used condom", "condom wrapper", "condom box", "many used condoms",
    "condom belt", "cum on sheets", "cum pool", "pussy juice stain",
    "sex toy nearby", "vibrator on bed", "dildo on floor", "anal beads on table",
}
SUGGESTIVE_DETAILS = {"unworn panties", "used tissue", "sweat stain", "clothes on floor"}
# 第 5 级（极端）专属场景细节：只有 extreme 档才允许出现（来自 EXTREME_GROUPS）
EXTREME_SCENE_DETAILS = set(EXTREME_DETAILS)


def privacy_allows(privacy, level, allow_public=False):
    """场所能否承载该等级的服装。

    · `any`（不限）→ 不做承载限制（等级都不限了，场所也没理由限）
    · allow_public → 放开公共/半公开场所的承载上限
    """
    key = resolve_level(level, "suggestive")
    if allow_public or key == "any":
        return True
    return NUM_OF.get(key, 1) <= PRIVACY_MAX_NUM.get(privacy, 1)


def validate_scene(tags, outfit_level="sfw", privacy=None, allow_public=False,
                   indoor=None):
    """返回违规说明列表；空列表 = 场景合理。

    四条判定：
      S1 场所隐私等级必须承载得住服装的暴露等级（2 级起只落私密/特殊场所）
      S2 室内场所不出现天气描写（雨/雪/雾/路面/城市景观）
      S3 过激向场景细节（3 级）与低等级服装不匹配
      S4 极端向场景细节（第 5 级）只有 extreme 档才成立
    """
    bad = []
    low = [str(t).strip().lower() for t in (tags or []) if str(t).strip()]
    num = NUM_OF.get(resolve_level(outfit_level, "suggestive"), 1)
    label = level_label(outfit_level)

    if privacy and not privacy_allows(privacy, outfit_level, allow_public):
        bad.append(f"场景/服装曝光冲突：{PRIVACY_LABEL.get(privacy, privacy)}场所"
                   f"不承载 {label} 级服装（需 allow_public 或换私密场所）")

    if (indoor if indoor is not None else _place_indoor(low)):
        weather = [t for t in low if WEATHER_RE.search(t)]
        if weather:
            bad.append(f"室内场所不应出现天气描写：{weather}")

    ex = [t for t in low if t in EXTREME_SCENE_DETAILS]
    if ex and num < NSFW_MAX:
        bad.append(f"极端向场景细节与 {label} 级服装不匹配（需 extreme）：{ex}")

    det = [t for t in low if t in EXPLICIT_DETAILS]
    if det and num < 3:
        bad.append(f"过激向场景细节与 {label} 级服装不匹配（需 3 级起）：{det}")
    return bad


def _place_indoor(low_tags):
    joined = ", ".join(low_tags)
    return not any(k in joined for k in
                   ("outdoors", "beach", "ocean", "park", "ruins", "pool",
                    "torii gate", "open sky", "street"))


# ============================================================================
# 五、数据加载与随机抽取
# ============================================================================

def presets():
    _ensure()
    return _DATA["data"] or {"sections": {}}


def _group(section, prefix):
    sec = (presets().get("sections") or {}).get(section) or {}
    for g in sec.get("groups", []):
        if g["group"].startswith(prefix):
            return g
    return None


def clothing_pool():
    """服装池：全部标签去掉 warn，按槽位分桶（memoized）。"""
    _ensure()
    return _CACHE["clothing_pool"]


def _build_clothing_pool():
    sec = (presets().get("sections") or {}).get("clothing") or {}
    pool = {}
    for g in sec.get("groups", []):
        for it in g.get("items", []):
            warn = set(it.get("warn") or [])
            for t in it.get("tags", []):
                if t in warn:
                    continue
                pool.setdefault(classify_wear(t), []).append(t)
    # 第 5 级专属内容包：新槽位（act / device / mark）的标签直接入池。
    # ⚠️ 入池 ≠ 可被抽到 —— 抽取时会先按等级上限过滤，只有 extreme 档放行。
    for slot, groups in EXTREME_GROUPS.items():
        if slot == "scene_details":
            continue
        for _label, tags in groups:
            pool.setdefault(slot, []).extend(tags)
    return {k: sorted(set(v)) for k, v in pool.items()}


def scene_pool():
    """场景池：场所（带隐私等级）/ 天气时辰 / 场景细节（memoized）。"""
    _ensure()
    return _CACHE["scene_pool"]


def _build_scene_pool():
    places, ambience, details = [], [], []
    sec = (presets().get("sections") or {}).get("scene") or {}
    for g in sec.get("groups", []):
        head = g["group"]
        if head.startswith(("私密空间", "半公开空间", "公共空间", "特殊场景")):
            priv = PLACE_PRIVACY[head.split("（")[0].split("(")[0].strip()
                                 if "（" not in head else head.split("（")[0]]
            for it in g.get("items", []):
                places.append({"label": it.get("label") or "", "tags": it["tags"],
                               "privacy": priv,
                               "indoor": (it.get("label") or "") not in OUTDOOR_PLACES})
        elif head.startswith("天气/时辰"):
            for it in g.get("items", []):
                warn = set(it.get("warn") or [])
                ambience.append({"label": it.get("label") or "", "tags": it["tags"],
                                 "warn": it.get("warn") or []})
        elif head.startswith("场景细节"):
            for it in g.get("items", []):
                details.append({"label": it.get("label") or "", "tags": it["tags"]})
    # 第 5 级专属场景细节（新增内容）：只有 extreme 档才会被抽进场景
    extreme_details = [{"label": label, "tags": list(tags)}
                       for label, tags in EXTREME_GROUPS.get("scene_details", [])]
    return {"places": places, "ambience": ambience, "details": details,
            "extreme_details": extreme_details}


# ---------------------------------------------------------------------------
# 抽取：服装
# ---------------------------------------------------------------------------

def _pick(rng, seq):
    seq = [s for s in seq if s]
    return rng.choice(seq) if seq else None


def _pool_allowed(tag, level):
    """按目标等级过滤标签。

    ⚠️ 必须直接复用 grade_explicitness，不能另写一套匹配 ——
    否则「池子放行、分级却判超」会出现 sfw 档抽出 suggestive 内容的漏洞。
    """
    limit = TARGET_NUM.get(level, 99)
    if limit >= 99:
        return True
    return grade_nsfw([tag])["num"] <= limit


def roll_outfit(level="suggestive", seed=None, allow_public=False):
    """抽一套搭配合理的服装。

    组装规则（保证合理性的关键）：
      主体 六选一：整套(set) | 连体(onepiece) | 分体(top+bottom) | 泳装 | 内衣套装 | 全裸
      → 叠加 袜(≤1) 鞋(≤1) 配饰(≤2) 材质(≤2) 状态(≤2) 道具(≤2)
      → 池子先按 level 过滤，保证抽出来的等级可控
      → 材质/状态必须有依附对象；最后统一过 validate_outfit
    """
    level = resolve_level(level, "suggestive")
    rng = random.Random(seed if seed is not None else random.randrange(1 << 30))
    raw = clothing_pool()
    pool = {k: [t for t in v if _pool_allowed(t, level)] for k, v in raw.items()}

    def draw(slot, n=1):
        items = list(pool.get(slot, []))
        rng.shuffle(items)
        return items[:n]

    want = resolve_level(level, "suggestive")

    forms = ["set", "onepiece", "two_piece"]
    if want == "sfw":
        pass
    elif want == "extreme":
        # 第 5 级：主体只留 整套 / 连体 / 分体 / 全裸 ——
        # 泳装、内衣套装与「装置 + 行为」语义冲突，validate_outfit 的 E2 会判违规
        forms += ["nude", "nude"]
    elif want == "any":
        forms += ["swim", "underwear", "nude"]
    else:
        forms += ["swim", "underwear"]
        if TARGET_NUM.get(want, 1) >= 2:
            forms += ["nude", "nude"]      # 高等级提高全裸权重
    form = rng.choice(forms)

    outfit = []
    if form == "nude":
        outfit += ["completely nude"]
    elif form == "two_piece":
        outfit += draw("top", 1) + draw("bottom", 1)
    else:
        outfit += draw(form, 1)

    high = TARGET_NUM.get(want, 99) >= 2
    if form != "nude" or high:
        outfit += draw("socks", 1)
        if "barefoot" not in outfit:
            outfit += draw("shoes", 1)
        outfit += draw("accessory", rng.randint(0, 2))
        # ⚠️ 材质/改造必须有依附对象（validate_outfit 的 M1）——
        #    全裸主体没有可依附的衣服，此时一律不抽材质，否则必然违规
        if form != "nude" and rng.random() < 0.45:
            outfit += draw("material", rng.randint(1, 2))
        if rng.random() < 0.55:
            outfit += draw("state", rng.randint(1, 2))
        if high and rng.random() < 0.35:
            outfit += draw("prop", rng.randint(1, 2))

    # ---- 第 5 级专属叠加：行为 + 装置（+ 极限体液/标记）----
    # 装置必须有行为承载（validate_outfit 的 E1），所以两者成对抽取
    if want == "extreme":
        acts = draw("act", 1)
        outfit += acts
        if acts:
            outfit += draw("device", 1 if rng.random() < 0.7 else 2)
        if rng.random() < 0.6:
            outfit += draw("mark", 1)

    # 去重 → 自动修复 → 再校验
    seen, clean = set(), []
    for t in outfit:
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            clean.append(t)

    def _boost(tags, target_num):
        """挑一个「加进去不产生新违规」的标签，把 NSFW 等级推到 target_num。

        候选按「离目标等级最近」排序：抽 2 级时优先挑恰好 2 级的，
        挑不到才退而用 3 级的顶上。
        """
        # ⚠️ 只收「恰好等于目标等级」的标签，不允许超档。
        #    宁可偶尔够不到（往安全方向偏），也不能让「擦边档」抽出 2 级内容。
        cands = []
        for slot in ("state", "material", "prop", "accessory", "misc",
                     "act", "device", "mark"):
            for t in raw.get(slot, []):
                if grade_nsfw([t])["num"] == target_num:
                    cands.append((0, t))
        cands.sort(key=lambda x: x[0])
        existing = {t.lower() for t in tags}
        # ① 直接加进去就合法
        for _d, c in cands[:200]:
            if c.lower() in existing:
                continue
            if not validate_outfit(tags + [c]):
                return tags + [c]
        # ② 加进去再修复，只要修复后仍然达标就接受
        for _d, c in cands[:200]:
            if c.lower() in existing:
                continue
            fixed = repair_outfit(tags + [c])
            if grade_nsfw(fixed)["num"] >= target_num:
                return fixed
        return None

    target = TARGET_NUM.get(want, 99)
    got_num = grade_nsfw(clean)["num"]
    if target < 99 and got_num < target:
        boosted = _boost(clean, target)
        if boosted:
            clean = boosted
        elif want in ("explicit", "hardcore", "extreme"):
            # 极端兜底：退回「全裸 + 保留原配饰/袜/鞋/极端槽」；
            # 够不到目标等级时再补一个同档标签（第 5 级优先补「极端行为」，
            # 因为装置单独存在会被 E1 判违规）
            keep = [t for t in clean if classify_wear(t) in
                    ("accessory", "shoes", "socks", "prop", "material", "state",
                     "act", "device", "mark")]
            clean = repair_outfit(["completely nude"] + keep)
            if grade_nsfw(clean)["num"] < target:
                top = [t for t in raw.get("act", []) + raw.get("mark", [])
                       if grade_nsfw([t])["num"] >= target]
                if not top:
                    top = [t for t in raw.get("prop", [])
                           if grade_nsfw([t])["num"] >= min(target, 3)]
                if top:
                    clean = repair_outfit(clean + [rng.choice(top)])

    if want == "sfw":
        clean = [t for t in clean if _pool_allowed(t, "sfw")]

    clean = repair_outfit(clean)
    got, why = grade_explicitness(clean)
    report = grade_nsfw(clean)

    bad = validate_outfit(clean)
    return {
        "tags": clean, "text": ", ".join(clean), "form": form,
        # ⭐ level = 规范等级 key（sfw…extreme），场景会拿它做承载力判定
        "level": report["level"],
        # 三档投影，保留给旧调用方 / 报告用
        "explicitness": got,
        "target": want, "num": report["num"],
        "label": report["label"], "nsfw": report,
        "why": why, "violations": bad, "seed": seed,
    }


# ---------------------------------------------------------------------------
# 抽取：场景
# ---------------------------------------------------------------------------

def roll_scene(level="sfw", seed=None, allow_public=False, place_hint=None,
               duo_daily=False):
    """抽一个与服装曝光等级相容的场景（场所 + 天气时辰 + 场景细节）。

    duo_daily=True（双女性 + SFW）时，优先落日常场所，配"正常互动"语义。
    """
    level = resolve_level(level, "sfw")
    rng = random.Random(seed if seed is not None else random.randrange(1 << 30))
    pools = scene_pool()

    num = NUM_OF.get(level, 1)
    ok_privacy = {p for p in PRIVACY_MAX_NUM if privacy_allows(p, level, allow_public)}

    cands = [p for p in pools["places"] if p["privacy"] in ok_privacy]
    if place_hint:
        cands = [p for p in cands if place_hint in p["label"]] or cands
    if duo_daily:
        daily = [p for p in cands if p["label"] in DUO_SFW_PLACES]
        if daily:
            cands = daily
    if not cands:
        cands = pools["places"]
    place = rng.choice(cands)

    out = list(place["tags"])

    # 天气/时辰：室内只取时辰类，且跳过命中 Skill 禁光表的标签
    amb = [a for a in pools["ambience"]
           if not (place["indoor"] and any(WEATHER_RE.search(t) for t in a["tags"]))]
    if amb and rng.random() < 0.75:
        a = rng.choice(amb)
        warn = set(a.get("warn") or [])
        safe = [t for t in a["tags"] if t not in warn]
        out += safe[:1]

    # 场景细节：3 级细节只在高等级出现，第 5 级还有专属细节池
    det_groups = list(pools["details"])
    if num >= NSFW_MAX:
        det_groups += list(pools.get("extreme_details", []))
    rng.shuffle(det_groups)
    for g in det_groups:
        if rng.random() > 0.35:
            continue
        tags = list(g["tags"])
        if num < 2:
            tags = [t for t in tags if t not in EXPLICIT_DETAILS]
        if num < NSFW_MAX:
            tags = [t for t in tags if t.lower() not in EXTREME_DETAILS]
        if tags:
            out.append(rng.choice(tags))
        break

    # 修复：室内去掉天气描写；低等级去掉超出承载力的场景细节
    if place["indoor"]:
        out = [t for t in out if not WEATHER_RE.search(t)]
    if num < 3:
        out = [t for t in out if t.lower() not in EXPLICIT_DETAILS]
    if num < NSFW_MAX:
        out = [t for t in out if t.lower() not in EXTREME_DETAILS]
    # ⚠️ 场景词表里混着被 NSFW 命中的词（chains / exposed / outdoors / recording），
    #    这里按等级上限统一过滤，否则 sfw 档会被地牢的 chains 顶到过激级
    limit = TARGET_NUM.get(level, 99)
    if limit < 99:
        out = [t for t in out if grade_nsfw([t])["num"] <= limit]

    bad = validate_scene(out, outfit_level=level, privacy=place["privacy"],
                         allow_public=allow_public, indoor=place["indoor"])
    return {
        "tags": out, "text": ", ".join(out),
        "place": place["label"], "privacy": place["privacy"],
        "indoor": place["indoor"], "level": level, "num": num,
        "label_zh": LEVEL_ZH.get(level, level),
        "violations": bad, "seed": seed,
    }


# ============================================================================
# 姿势池：随机姿势（按 NSFW 等级分层）
# ============================================================================

# 姿势组 → 该组内容的最低 NSFW 等级（数值）。
# 数据源 = anima3-prompt skill 50-pose-action.md 的组名；「SFM 日常姿势」是扩充内容。
# 抽取时只从 min_level <= 目标等级 的组里选，保证「擦边档不抽性行为姿势」。
POSE_GROUP_MIN_LEVEL = {
    "诱惑姿态": 1,
    "暴露/露出": 2,
    "隐秘处展示/自拍/直播": 2,
    "自慰": 3,
    "口交": 3,
    "足交": 3,
    "素股": 3,
    "手交": 3,
    "乳交": 3,
    "调戏猥亵": 3,
    "正身位（传教士）": 3,
    "站立位": 3,
    "坐身位": 3,
    "后入位": 3,
    "火车便当": 3,
    "种付位": 3,
    "骑乘位": 3,
    "睡奸": 3,
    "催眠": 3,
    "攻守反转/Femdom": 3,
    "过激": 4,
    "多人": 4,
    "百合": 3,
    "SFM 日常姿势（扩充）": 0,
}


def pose_pool():
    """姿势池：[{group, label, tags, min_level}]（memoized）。"""
    _ensure()
    return _CACHE["pose_pool"]


def _build_pose_pool():
    sec = (presets().get("sections") or {}).get("pose") or {}
    pool = []
    for g in sec.get("groups", []):
        min_lv = POSE_GROUP_MIN_LEVEL.get(g["group"], 1)
        for it in g.get("items", []):
            warn = set(it.get("warn") or [])
            tags = [t for t in it.get("tags", []) if t not in warn]
            if tags:
                pool.append({"group": g["group"], "label": it.get("label") or "",
                             "tags": tags, "min_level": min_lv})
    return pool


# 按等级过滤后的姿势候选缓存（避免每次抽取都对 500 个条目重复 grade_nsfw）
_POSE_BY_LEVEL = {}


def _pose_cands(level):
    """按等级过滤后的姿势候选（memoized）。

    两级过滤：① 组级 min_level ≤ 目标等级；② 标签级 _pool_allowed（
    挡掉 SFM 姿势里混进的「性暗示手势」如 cunnilingus gesture）。
    """
    if level in _POSE_BY_LEVEL:
        return _POSE_BY_LEVEL[level]
    num = NUM_OF.get(level, 1)
    pool = pose_pool()
    cands = [p for p in pool if p["min_level"] <= num]
    if not cands:
        cands = [p for p in pool if p["min_level"] <= 1] or pool
    filtered = []
    for p in cands:
        ok = [t for t in p["tags"] if _pool_allowed(t, level)]
        if ok:
            filtered.append({"group": p["group"], "label": p["label"],
                             "tags": ok, "min_level": p["min_level"]})
    result = filtered or cands
    _POSE_BY_LEVEL[level] = result
    return result


def roll_pose(level="suggestive", seed=None):
    """按等级抽一个姿势（从允许的组里选 1 个条目，取 1~3 个标签）。

    · 等级只做上限 + 保底：min_level 高于目标的组直接排除
    · 每个条目是一组相关标签（如「骑乘」含 cowgirl / reverse cowgirl …），
      抽 1~3 个，避免把整组同义标签全堆上去
    """
    level = resolve_level(level, "suggestive")
    num = NUM_OF.get(level, 1)
    rng = random.Random(seed if seed is not None else random.randrange(1 << 30))
    cands = _pose_cands(level)
    # 加权：min_level 越接近目标等级，权重越高（差 0 档=4，差 1 档=2，差 2=1，差 3=0.5）。
    # 这样 hardcore/extreme 档主要抽性行为姿势，偶尔落到日常姿势保持多样性；
    # sfw/suggestive 档天然只剩日常/诱惑，不会超档。
    weights = [2.0 ** (2 - (num - p["min_level"])) for p in cands]
    item = rng.choices(cands, weights=weights, k=1)[0]
    tags = list(item["tags"])
    rng.shuffle(tags)
    picked = tags[:rng.randint(1, min(3, len(tags)))]
    return {"tags": picked, "text": ", ".join(picked),
            "group": item["group"], "label": item["label"],
            "level": level, "num": num,
            "seed": seed}


# ============================================================================
# 表情系统：表情随「当前所做的事」自动匹配
# ============================================================================
#
# 数据源 = anima3-prompt skill 60-expression.md（§10.1 表情维度 / §10.2 强度映射）。
# 三层决策，规则清晰可复用：
#   ① 动作语义识别：从已抽出的姿势 / 服装 / 关系标签里匹配触发关键词 → 命中情绪类型
#   ② 强度定档：由 NSFW 等级决定（sfw→Lv1 平静 / suggestive→Lv1-2 诱惑·羞耻 /
#      explicit→Lv2 愉悦 / hardcore→Lv3 失神 / extreme→Lv4 崩坏）
#   ③ 输出：表情标签 ≤3 个；Lv3 起强制叠加 ≥1 个身体反应标签

# 动作语义 → 情绪类型（按优先级从高到低，先命中先胜）
EXPRESSION_TRIGGERS = [
    ("reluctant", ("forced", "reluctant", "rape", "bondage", "ropes", "shackles",
                   "gagged", "blindfold", "struggling", "held down", "restrained")),
    ("ahegao", ("orgasm", "ahegao", "climax", "convulsing", "squirting",
                "mind break", "fucked silly", "torogao")),
    ("pleasured", ("masturbation", "masturbating", "fingering", "vibrator", "dildo",
                   "blowjob", "deepthroat", "fellatio", "irrumatio", "cunnilingus",
                   "paizuri", "footjob", "sex", "insertion", "cowgirl", "mating press",
                   "doggy style", "tribadism", "scissoring", "paizuri")),
    ("embarrassed", ("exposed", "exhibitionism", "public", "caught", "humiliated",
                     "ashamed", "upskirt", "peeping", "embarrassed")),
    ("dominant", ("dominant", "assertive", "femdom", "sadistic", "riding",
                  "smug", "smirk")),
    ("submissive", ("submissive", "devoted", "obedient", "kneeling", "worship")),
    ("neutral", ("sleep", "sleeping", "hypnosis", "unconscious", "asleep",
                 "expressionless", "bored", "sleepy")),
    ("seductive", ("seduce", "tease", "tempt", "invite", "flirt", "provocative",
                   "seductive")),
]

# 情绪类型 → 表情标签池（从 60-expression.md §10.1 提取）
EXPRESSION_TAGS = {
    "seductive": ["seductive smile", "half-closed eyes", "looking at viewer",
                  "heavy-lidded eyes", "licking lips", "parted lips", "naughty face"],
    "submissive": ["submissive", "devoted", "obedient", "looking up", "closed eyes",
                   "peaceful", "light smile"],
    "dominant": ["smug", "smirk", "confident", "dominant", "evil smile",
                 "looking down", "sadistic"],
    "reluctant": ["scared", "reluctant", "crying", "tears", "struggling", "frown",
                  "clenched teeth", "pain"],
    "ahegao": ["ahegao", "tongue out", "drooling", "rolling eyes", "fucked silly",
               "mind break", "heart-shaped pupils", "torogao", "cross-eyed", "empty eyes"],
    "embarrassed": ["embarrassed", "blush", "ashamed", "guilty", "humiliated",
                    "covering face", "looking away", "nervous"],
    "neutral": ["expressionless", "emotionless", "bored", "sleepy", "closed eyes",
                "calm"],
    "pleasured": ["moaning", "panting", "blush", "heavy breathing", "ecstasy",
                  "eyes closed", "sweating", "seductive smile"],
}

# 身体反应标签（60-expression.md §10.3），Lv3 起强制叠加
EXPRESSION_REACTIONS = {
    "light": ["slight trembling", "sweat", "flush"],
    "heavy": ["trembling", "sweat", "arched back", "toes curling", "legs shaking",
              "shaking", "body blush"],
    "extreme": ["convulsing", "limp body", "squirting", "female ejaculation",
                "foaming at the mouth"],
}

# NSFW 等级 → (默认情绪, 强度档)
#   num 0 → neutral/平静；1 → seductive/诱惑；2 → pleasured/愉悦；
#   3 → ahegao/失神(重反应)；4 → ahegao/崩坏(极限反应)
EXPRESSION_BY_LEVEL = {
    0: ("neutral", "light"),
    1: ("seductive", "light"),
    2: ("pleasured", "light"),
    3: ("ahegao", "heavy"),
    4: ("ahegao", "extreme"),
}


def expression_pool():
    """表情池：presets 里的 expression section 展平成标签集合（memoized）。"""
    _ensure()
    return _CACHE["expression_pool"]


def _build_expression_pool():
    sec = (presets().get("sections") or {}).get("expression") or {}
    tags = []
    for g in sec.get("groups", []):
        for it in g.get("items", []):
            warn = set(it.get("warn") or [])
            for t in it.get("tags", []):
                if t not in warn:
                    tags.append(t)
    return sorted(set(tags))


def _classify_expression(tags):
    """① 动作语义识别：从标签里找第一个命中的情绪类型。

    返回情绪 key；无命中返回 None（交给 NSFW 等级定默认情绪）。
    """
    low = [str(t).strip().lower() for t in (tags or []) if str(t).strip()]
    text = " ".join(low)
    for key, words in EXPRESSION_TRIGGERS:
        if any(w in text for w in words):
            return key
    return None


def match_expression(nsfw_num, tags, rng=None):
    """表情自动匹配（核心，可复用）。

    输入：
      nsfw_num  当前画面的 NSFW 等级（0-4，来自 grade_nsfw）
      tags      已抽出的姿势 / 服装 / 关系等标签（用于识别「在做什么」）
    输出：
      {emotion, expressions, reactions, level}
    """
    rng = rng or random.Random()
    nsfw_num = int(min(max(nsfw_num, 0), NSFW_MAX))

    # ① 动作语义 → 情绪
    emotion = _classify_expression(tags)
    # ② 未命中动作语义时，用 NSFW 等级给默认情绪；命中则叠加等级强度
    if emotion is None:
        emotion, _intensity = EXPRESSION_BY_LEVEL[nsfw_num]

    # 强度档：抗拒/羞耻等「被动」情绪不受等级抬升，保持自身强度
    intensity = "light"
    if emotion == "ahegao":
        intensity = EXPRESSION_BY_LEVEL[max(nsfw_num, 3)][1]  # 至少 heavy
    elif emotion == "reluctant":
        intensity = "heavy" if nsfw_num >= 3 else "light"
    elif emotion in ("pleasured", "dominant", "submissive"):
        intensity = "heavy" if nsfw_num >= 3 else "light"
    elif emotion == "embarrassed":
        intensity = "light"

    # ③ 输出：表情 ≤3 + 身体反应（Lv3 起必有）
    # ⚠️ 表情标签同样不超档：过滤掉 grade_nsfw > nsfw_num 的词，
    #    否则 hardcore 档表情抽到 4 级词（fucked silly 等）会把整条提示词顶超档。
    pool = [t for t in EXPRESSION_TAGS.get(emotion, EXPRESSION_TAGS["neutral"])
            if grade_nsfw([t])["num"] <= nsfw_num]
    if not pool:
        pool = list(EXPRESSION_TAGS["neutral"])
    rng.shuffle(pool)
    expressions = pool[:rng.randint(1, 3)]

    reactions = []
    if nsfw_num >= 3 or emotion == "ahegao":
        rp = [t for t in EXPRESSION_REACTIONS.get(intensity, EXPRESSION_REACTIONS["heavy"])
              if grade_nsfw([t])["num"] <= nsfw_num]
        if not rp:
            rp = list(EXPRESSION_REACTIONS["heavy"])
        rng.shuffle(rp)
        reactions = rp[:rng.randint(1, 2)]

    return {"emotion": emotion, "expressions": expressions,
            "reactions": reactions, "intensity": intensity,
            "tags": expressions + reactions,
            "text": ", ".join(expressions + reactions)}


def roll_expression(level="suggestive", seed=None, context_tags=None):
    """按等级 + 上下文抽表情（自动匹配，也支持纯随机兜底）。

    context_tags 传已抽出的姿势/服装/关系标签；为空时走纯随机（从表情池抽）。
    """
    level = resolve_level(level, "suggestive")
    num = NUM_OF.get(level, 1)
    rng = random.Random(seed if seed is not None else random.randrange(1 << 30))

    if context_tags:
        return match_expression(num, context_tags, rng)

    # 纯随机：从表情池抽 1~3 个，再按等级补身体反应（同样不超档）
    pool = expression_pool() or [t for v in EXPRESSION_TAGS.values() for t in v]
    pool = [t for t in pool if _pool_allowed(t, level)]
    rng.shuffle(pool)
    expressions = pool[:rng.randint(1, 3)]
    reactions = []
    if num >= 3:
        rp = [t for t in EXPRESSION_REACTIONS["heavy"]
              if _pool_allowed(t, level)]
        rng.shuffle(rp)
        reactions = rp[:rng.randint(1, 2)]
    return {"emotion": "random", "expressions": expressions,
            "reactions": reactions, "intensity": "light",
            "tags": expressions + reactions,
            "text": ", ".join(expressions + reactions)}


# ---------------------------------------------------------------------------
# 抽取：一键随机（服装 + 场景 + 姿势 + 表情，互相协调）
# ---------------------------------------------------------------------------

def roll_all(level="suggestive", seed=None, allow_public=False, duo_context=""):
    """一键随机：服装 + 场景 + 姿势 + 表情（+ 双女性时的关系标签）。

    duo_context 传上下文文本（角色槽里的角色标签）。识别到两位女性时：
      · NSFW ≤ 1 → 正常互动（friends / 牵手 / 一起吃饭）+ 优先日常场所
      · NSFW ≥ 2 → 百合（yuri + 具体行为）
    姿势按同一等级分层抽取；表情自动匹配「当前在做什么」（姿势+服装+关系）。
    """
    level = resolve_level(level, "suggestive")
    base = seed if seed is not None else random.randrange(1 << 30)
    outfit = roll_outfit(level=level, seed=base, allow_public=allow_public)
    nsfw_num = grade_nsfw(outfit["tags"])["num"]
    duo = detect_female_duo(duo_context) if duo_context else {"duo": False, "count": 0}

    relation = (duo_relation_tags(nsfw_num, random.Random(base + 2), duo["count"])
                if duo["duo"] else [])
    scene = roll_scene(level=outfit["level"], seed=base + 1,
                       allow_public=allow_public,
                       duo_daily=duo["duo"] and nsfw_num <= 1)
    pose = roll_pose(level=outfit["level"], seed=base + 3)
    # 表情自动匹配：喂「姿势 + 服装 + 关系」作为上下文，让它识别当前在做什么
    ctx = pose["tags"] + outfit["tags"] + relation
    expression = roll_expression(level=outfit["level"], seed=base + 4,
                                 context_tags=ctx)

    parts = [outfit["text"], ", ".join(relation), pose["text"],
             expression["text"], scene["text"]]
    text = ", ".join(p for p in parts if p)
    return {"outfit": outfit, "scene": scene, "pose": pose,
            "expression": expression, "relation": relation, "duo": duo,
            "level": outfit["level"],
            "text": text,
            "violations": outfit["violations"] + scene["violations"],
            "nsfw": grade_nsfw(outfit["tags"] + relation + scene["tags"]
                               + pose["tags"] + expression["tags"]),
            "seed": base}


ROLL_SECTIONS = ("outfit", "scene", "pose", "expression", "all")
# （LEVELS 定义见文件上半部分的「等级词汇表」，此处不再重复定义）


def roll(section="all", level="suggestive", seed=None, allow_public=False,
         place_hint=None, duo_context=""):
    level = resolve_level(level)
    if section == "outfit":
        return roll_outfit(level=level, seed=seed, allow_public=allow_public)
    if section == "scene":
        duo = detect_female_duo(duo_context) if duo_context else {"duo": False}
        return roll_scene(level=level, seed=seed, allow_public=allow_public,
                          place_hint=place_hint,
                          duo_daily=duo["duo"] and LEVEL_RANK.get(level, 1) <= 1)
    if section == "pose":
        return roll_pose(level=level, seed=seed)
    if section == "expression":
        # 单独抽表情时，把 duo_context 当上下文做自动匹配；为空则纯随机
        ctx = [t.strip() for t in (duo_context or "").split(",") if t.strip()] or None
        return roll_expression(level=level, seed=seed, context_tags=ctx)
    return roll_all(level=level, seed=seed, allow_public=allow_public,
                    duo_context=duo_context)


# ============================================================================
# 七、双女性识别 → 关系标签
# ============================================================================

# SFW：两位女性的**正常互动**（日常、非性）
DUO_SFW_TAGS = [
    "friends", "holding hands", "looking at each other", "smiling",
    "walking together", "eating together", "sharing umbrella", "sitting together",
    "talking", "shopping together", "picnic", "hugging", "headpat",
    "leaning on person", "feeding", "playing together", "arm around waist",
]
# NSFW：两位女性的**百合内容**（按露骨度分两档，避免「成人档」抽出过激内容）
DUO_YURI_SOFT = [      # 必须是 NSFW 2 级的词
    "kiss", "french kiss", "hugging", "nude", "eye contact", "holding hands",
]
DUO_YURI_HARD = [      # 必须是 NSFW 3 级的词
    "tribadism", "scissoring", "cunnilingus", "fingering", "breast sucking",
]
DUO_YURI_EXTREME = [   # 必须是 NSFW 4 级的词（第 5 级专属）
    "fisting", "vaginal fisting", "anal fisting", "large insertion", "gaping",
]
# SFW 双人优先落在这几个日常场所
DUO_SFW_PLACES = {"教室", "公园/长椅", "客厅/沙发", "厨房", "餐厅/居酒屋",
                  "海滩/泳池", "卧室", "试衣间", "电影院", "办公室"}

FEMALE_COUNT_RE = re.compile(r"^(\d+)\s*girls?$")
MALE_MARKERS = ("1boy", "2boys", "3boys", "multiple boys", "1other", "hetero")
DUO_MARKERS = ("yuri", "girls love", "lesbian", "2girls", "multiple girls")


def detect_female_duo(tags):
    """识别"是否出现两位（或以上）女性角色"。

    判定依据（任一命中即为双女，且画面里没有男性）：
      · 人数词 `2girls` / `3girls` / `multiple girls`
      · `1girl` 出现两次及以上
      · 关系词 `yuri` / `girls love` / `lesbian`
    """
    low = [str(t).strip().lower() for t in (tags or []) if str(t).strip()]
    if isinstance(tags, str):
        low = [t.strip().lower() for t in tags.split(",") if t.strip()]

    male = any(m in low for m in MALE_MARKERS)
    count = 0
    for t in low:
        m = FEMALE_COUNT_RE.match(t)
        if m:
            count = max(count, int(m.group(1)))
    ones = low.count("1girl")
    if ones >= 2:
        count = max(count, ones)
    explicit_duo = any(t in DUO_MARKERS for t in low)
    if explicit_duo:
        count = max(count, 2)
    return {"duo": count >= 2 and not male, "count": count,
            "male_present": male, "explicit_duo_marker": explicit_duo}


def duo_relation_tags(nsfw_num, rng=None, count=2):
    """按 NSFW 等级给两位女性配关系标签。

    · NSFW ≤ 1（全年龄 / 擦边）→ **正常互动**（friends / 牵手 / 一起吃饭…）
    · NSFW 2/3（成人 / 过激）  → **百合**（必带 yuri，再叠 2 个具体行为）
    · NSFW 4（极端，新增）     → **极端百合**（yuri + 4 级专属行为）
    """
    rng = rng or random.Random()
    if nsfw_num >= NSFW_MAX:
        return ["yuri"] + rng.sample(DUO_YURI_EXTREME,
                                     min(2, len(DUO_YURI_EXTREME)))
    if nsfw_num >= 3:
        return ["yuri"] + rng.sample(DUO_YURI_HARD, min(2, len(DUO_YURI_HARD)))
    if nsfw_num >= 2:
        return ["yuri"] + rng.sample(DUO_YURI_SOFT, min(2, len(DUO_YURI_SOFT)))
    pool = [t for t in DUO_SFW_TAGS if t != "friends"]
    return ["friends"] + rng.sample(pool, min(2, len(pool)))


def duo_relation_for_level(level, count=2, rng=None):
    """按等级 key（sfw / … / extreme）给关系标签。"""
    key = resolve_level(level, "suggestive")
    num = NUM_OF.get(key, 1)
    return duo_relation_tags(num, rng, count)


# ============================================================================
# 八、随机女性角色
# ============================================================================

CHAR_PATH = os.path.join(PRESET_DIR, "character_pool.json")
RECENT_PATH = os.path.join(PRESET_DIR, ".recent_girls.json")

# 可选池（中文，直接给控件用）
CHAR_POOLS = ("热门 TOP200", "热门 TOP1000", "全部女性",
              "按作品", "按发色", "按瞳色")
POOL_SIZES = {"热门 TOP200": 200, "热门 TOP1000": 1000}

_CHAR = {"key": None, "list": []}


def character_pool():
    """女性角色池（memoized）。字段：n 角色名 / c 作品 / t 官方 trigger /
    g 性别 / p 同人作品数 / h 发色 / e 瞳色 / f 官方特征 tags。"""
    key = _mt(CHAR_PATH)
    if _CHAR["key"] != key:
        try:
            with open(CHAR_PATH, encoding="utf-8") as f:
                data = json.load(f)
            _CHAR["list"] = [c for c in data.get("characters", []) if c.get("g") == "1girl"]
        except OSError:
            _CHAR["list"] = []
        _CHAR["key"] = key
    return _CHAR["list"]


def _load_recent():
    try:
        with open(RECENT_PATH, encoding="utf-8") as f:
            return list(json.load(f))
    except OSError:
        return []


def _save_recent(names, keep=40):
    try:
        with open(RECENT_PATH, "w", encoding="utf-8") as f:
            json.dump(names[-keep:], f, ensure_ascii=False)
    except OSError:
        pass


def filter_characters(pool="热门 TOP200", keyword="", min_post=0):
    """按池 / 关键词 / 热度筛出候选，返回列表。"""
    chars = character_pool()
    kw = (keyword or "").strip().lower()

    if pool == "按作品":
        chars = [c for c in chars if kw and kw in (c.get("c") or "").lower()]
    elif pool == "按发色":
        chars = [c for c in chars if kw and kw == (c.get("h") or "").lower()]
    elif pool == "按瞳色":
        chars = [c for c in chars if kw and kw == (c.get("e") or "").lower()]
    elif pool in POOL_SIZES:
        chars = chars[:POOL_SIZES[pool]]          # 池已按热度降序

    if min_post > 0:
        chars = [c for c in chars if c.get("p", 0) >= min_post]
    elif pool in ("按作品", "按发色", "按瞳色"):
        chars = [c for c in chars if c.get("p", 0) >= 50]   # 冷门池兜个底，避免出太生僻的
    return chars


# 巨乳开关要替换掉的体型词（含所有胸部尺寸 + 平胸类），替换后统一补 `huge breasts`
BREAST_SIZE_TAGS = {
    "small breasts", "medium breasts", "large breasts", "huge breasts",
    "gigantic breasts", "flat chest", "flat breasts", "breasts",
}


def roll_girl(pool="热门 TOP200", keyword="", min_post=0, count=1,
              include_features=True, seed=None, avoid_repeat=True,
              feature_limit=0, huge_breasts=False):
    """随机抽女性角色 + 她的官方标签特征。

    · 按 sqrt(同人作品数) 加权 —— 热门角色更容易抽到，但不至于永远只有那几个
    · avoid_repeat 默认开：最近 40 次抽过的会避开（候选不足时才允许重复）
    · huge_breasts=True：强制把体型词替换为巨乳（`huge breasts`），
      保证随机出来的人物必为巨乳体型（用户 2026-09-29 需求）
    · 返回的 tags_all 已按「性别词 / 身份 / 特征」拆好槽位，供声明通道使用
    """
    rng = random.Random(seed if seed is not None else random.randrange(1 << 30))
    cands = filter_characters(pool, keyword, min_post)

    if avoid_repeat:
        recent = set(_load_recent())
        fresh = [c for c in cands if c["n"] not in recent]
        if len(fresh) >= max(count, 1):
            cands = fresh

    if not cands:
        return {"ok": False, "error": f"池「{pool}」在关键词「{keyword}」下没有候选角色",
                "candidates": 0, "picks": []}

    count = max(1, min(int(count or 1), 3))
    weights = [(c.get("p", 0) + 1) ** 0.5 for c in cands]
    picks, used = [], set()
    for _ in range(count):
        pool_c = [(c, w) for c, w in zip(cands, weights) if c["n"] not in used]
        if not pool_c:
            break
        cs, ws = zip(*pool_c)
        picked = rng.choices(cs, weights=ws, k=1)[0]
        used.add(picked["n"])
        picks.append(picked)

    out = []
    for c in picks:
        raw_feats = [t for t in (c.get("f") or [])
                     if t.lower() not in ("1girl", "1boy", "solo")]
        seen_f, feats = set(), []          # 源数据里有重复项，这里去一次
        for t in raw_feats:
            if t.lower() not in seen_f:
                seen_f.add(t.lower())
                feats.append(t)
        if feature_limit and feature_limit > 0:
            feats = feats[:feature_limit]
        # 巨乳开关：强制替换体型词，保证随机人物必为巨乳
        if huge_breasts:
            feats = [t for t in feats
                     if t.lower() not in BREAST_SIZE_TAGS]
            feats.append("huge breasts")
        identity = [c["t"]]                       # 官方 trigger = 角色名 + 作品名
        tags_all = ["1girl"] + identity + (feats if include_features else [])
        out.append({
            "name": c["n"], "copyright": c["c"], "trigger": c["t"],
            "post_count": c.get("p", 0), "hair": c.get("h", ""), "eye": c.get("e", ""),
            "features": feats if include_features else [],
            "gender": "1girl",
            "identity_tags": identity,
            "text": ", ".join(tags_all),
            "tags_all": tags_all,
        })

    if avoid_repeat:
        _save_recent(_load_recent() + [c["n"] for c in picks])

    all_tags = [t for p in out for t in p["tags_all"]]
    return {"ok": True, "candidates": len(cands), "pool": pool, "count": len(out),
            "picks": out, "text": ", ".join(p["text"] for p in out),
            "nsfw": grade_nsfw(all_tags), "seed": seed}
