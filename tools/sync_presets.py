# -*- coding: utf-8 -*-
"""把 anima3-prompt（阿尼玛）Skill 的标签库解析成插件用的结构化预设。

用法：
    python tools/sync_presets.py                 # 自动探测 Skill 目录
    python tools/sync_presets.py --skill <path>  # 手动指定
    python tools/sync_presets.py --dry-run       # 只看统计，不写文件

输入：<skill>/references/40-clothing.md、80-scene.md、50-pose-action.md
输出：presets/anima_presets.json

设计要点：
- `40-clothing.md` 用 `### / ####` 分节 + 反引号标签，`80-scene.md` 用 `#### 分类` + 表格行。
  两种结构都要能吃下，所以按「最近标题」归组、表格首列当条目名。
- 反引号里的内容是混合的：有真标签，也有公式（`原服装 → 改造方向 → 最终效果`）。
  用「只允许 ascii 小写标签字符」的正则做硬过滤，比黑名单稳。
- **不静默删违规标签**：若某个标签命中 Skill 自己的禁用表（光线/质量词），
  只给它打 `warn` 标记交给前端显示，不直接丢掉——因为 Skill 自身存在矛盾
  （§12.3 明确「允许环境天气和时辰描写」并列出 sunlight/moonlight，
   但校验脚本的 LIGHTING_WORDS 又把它们列为禁止）。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
OUT_PATH = os.path.join(PLUGIN_DIR, "presets", "anima_presets.json")
ATTIRE_OUT = os.path.join(PLUGIN_DIR, "presets", "attire_slots.json")
CHAR_OUT = os.path.join(PLUGIN_DIR, "presets", "character_pool.json")

# Anima-Tools 插件目录（同步 SFM 姿势/服装扩充与角色数据用）。
# 默认取同级 custom_nodes/ 下的 Comfyui-Anima-Tools-main，可用环境变量覆盖。
TOOLS_DIR = os.environ.get(
    "ANIMA_TOOLS_DIR",
    os.path.join(os.path.dirname(PLUGIN_DIR), "Comfyui-Anima-Tools-main"),
)
ATTIRE_SRC_CANDIDATES = [os.path.join(TOOLS_DIR, r"js\danbooru_attire_data.json")]
# 角色数据源：官方 trigger/tags（JSON） + 性别/发色/瞳色/热度（JS 数组）
CHAR_OFFICIAL_SRC = os.path.join(TOOLS_DIR, r"js\character_official_data.json")
CHAR_INDEX_SRC = os.path.join(TOOLS_DIR, r"js\character_data.js")
# 该文件的组名 → 本插件的服装槽位
ATTIRE_GROUP_MAP = {
    "top": "top", "bottom": "bottom",
    "uniform": "set", "traditional": "set",
    "decoration": "accessory", "socks": "socks", "shoes": "shoes",
}

DEFAULT_SKILL_CANDIDATES = [
    os.path.expanduser(r"~/.workbuddy/skills/anima3-prompt"),
]

SOURCES = [
    ("clothing", "40-clothing.md", "服装"),
    ("scene", "80-scene.md", "场景"),
    ("pose", "50-pose-action.md", "姿势"),
    ("expression", "60-expression.md", "表情"),
]

# SFM 扩充数据源：Anima-Tools 的姿势 / 服装选择器数据
# （pose_data.js = 日常/手势/动作姿势；clothing_data.js = 成套服装搭配）
SFM_POSE_SRC = os.path.join(TOOLS_DIR, r"js\pose_data.js")
SFM_CLOTHING_SRC = os.path.join(TOOLS_DIR, r"js\clothing_data.js")

# 只接受纯 ascii 小写标签（含 - _ ( ) ' . 和空格），长度 ≤ 42
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-()'\. ]{0,41}$")
BACKTICK_RE = re.compile(r"`([^`]+)`")
SPLIT_RE = re.compile(r"\s*[|/,+]\s*")
HEAD_NUM_RE = re.compile(r"^\d+(\.\d+)*\s*")

# Skill 自己的禁用表（与 scripts/validate_prompt.py 保持一致的关键子集）
LIGHTING_WORDS = {
    'sunlight', 'moonlight', 'rim light', 'rim lighting', 'backlight', 'backlighting',
    'warm lighting', 'cold lighting', 'soft lighting', 'hard lighting', 'dramatic lighting',
    'volumetric lighting', 'god rays', 'sunbeams', 'candlelight', 'neon light',
    'cinematic lighting', 'studio lighting', 'glowing', 'lens flare', 'light rays',
    'colorful lighting', 'warm tone', 'cool tone', 'sepia', 'color grading',
    'dim lighting', 'ambient light',
}
QUALITY_WORDS = {
    'masterpiece', 'best quality', 'high quality', 'ultra detailed', 'highly detailed',
    'amazing quality', 'absurdres', 'highres', 'lowres', 'normal quality', 'worst quality',
    'very aesthetic', 'aesthetic', 'perfect anatomy', 'detailed face', 'ultra-detailed',
}
FORBIDDEN = LIGHTING_WORDS | QUALITY_WORDS

# 明显是标签但被正则误收的噪声词
STOP_TAGS = {
    'score_x', 'score_1', 'score_7', 'score_8', 'score_9',
    'tag', 'tags', 'prompt', 'lora', 'anima', 'anima3',
    'content', 'value', 'name', 'type', 'mode',
}


def clean_heading(text):
    text = text.strip().lstrip('#').strip()
    text = HEAD_NUM_RE.sub('', text)
    return text.strip(' 　:：')


def clean_label(text):
    text = text.strip().strip('|').strip()
    text = text.replace('　', ' ').strip()
    if text.endswith('→'):
        text = text[:-1].strip()
    if text.endswith('：') or text.endswith(':'):
        text = text[:-1].strip()
    return text


def extract_tags(text):
    """从一段 markdown 里抽出所有像标签的反引号片段。"""
    out, seen = [], set()
    for blob in BACKTICK_RE.findall(text):
        for part in SPLIT_RE.split(blob):
            part = part.strip().strip('.').strip()
            if not part or part in STOP_TAGS:
                continue
            if not TAG_RE.match(part):
                continue
            if part in seen:
                continue
            seen.add(part)
            out.append(part)
    return out


def parse_md(path):
    """返回 [{group, label, tags, warn}] 的扁平条目列表。"""
    entries = []
    h2 = h3 = h4 = ''
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()

    def group_name():
        return h4 or h3 or h2 or '其他'

    def add(label, tags):
        if not tags:
            return
        warn = [t for t in tags if t in FORBIDDEN]
        entries.append({
            'group': group_name(),
            'label': label or '',
            'tags': tags,
            'warn': warn,
        })

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#### '):
            h4 = clean_heading(stripped[5:]); continue
        if stripped.startswith('### '):
            h4 = ''; h3 = clean_heading(stripped[4:]); continue
        if stripped.startswith('## '):
            h4 = h3 = ''; h2 = clean_heading(stripped[3:]); continue
        if stripped.startswith('# '):
            h4 = h3 = ''; h2 = clean_heading(stripped[2:]); continue
        if stripped.startswith('>') or stripped.startswith('<!--'):
            continue
        if stripped.startswith('---'):
            continue
        if stripped.startswith('|'):
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if len(cells) < 2:
                continue
            label = clean_label(cells[0])
            tags = extract_tags(cells[1])
            # 表头行
            if label in ('场所', '梯度', '部位', '类型', '类别', '标签A', '反差类型', '方案'):
                continue
            if tags:
                add(label if label and not label.startswith('---') else '', tags)
            continue
        tags = extract_tags(stripped)
        add('', tags)
    return entries


def parse_expression_md(path):
    """解析 60-expression.md（表情与反应标签库）。

    它的表格比服装/场景复杂：
      · 10.1 情绪类型表  2 列  → label=情绪类型, tags=核心标签
      · 10.2 强度映射表  4 列  → label=关键词, tags=表情标签+身体反应（自动匹配数据）
      · 10.3/10.4/10.5   2~3 列 → label=类型, tags=核心标签
    """
    entries = []
    h3 = ''
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith('### '):
            h3 = clean_heading(stripped[4:])
            continue
        if stripped.startswith('#'):
            continue
        if stripped.startswith('>') or stripped.startswith('<!--') or stripped.startswith('---'):
            continue
        if not stripped.startswith('|'):
            continue
        cells = [c.strip() for c in stripped.strip('|').split('|')]
        if len(cells) < 2:
            continue
        label = clean_label(cells[0])
        if label in ('情绪类型', '类型', '层级', '用户描述关键词', '强度'):
            continue  # 表头
        if '强度映射' in h3 and len(cells) >= 4:
            tags = extract_tags(cells[2]) + extract_tags(cells[3])
            label = f"{label}｜{clean_label(cells[1])}"
        else:
            tags = extract_tags(cells[1])
        if tags:
            warn = [t for t in tags if t in FORBIDDEN]
            entries.append({'group': h3 or '表情', 'label': label,
                            'tags': tags, 'warn': warn})
    return entries


def parse_sfm_js(path, kind):
    """解析 Anima-Tools 的 pose_data.js / clothing_data.js。

    文件形如 `const xxxData = [ {name, name_zh, tags, ...}, ... ]`。
    返回扁平条目列表：{group, label, tags}。
    """
    if not os.path.isfile(path):
        return []
    raw = open(path, encoding='utf-8').read()
    a, b = raw.find('['), raw.rfind(']')
    if a < 0 or b <= a:
        return []
    try:
        data = json.loads(raw[a:b + 1])
    except Exception as exc:
        print(f'  [警告] {os.path.basename(path)} 解析失败: {exc}')
        return []
    grp = 'SFM 日常姿势（扩充）' if kind == 'pose' else 'SFM 服装套装（扩充）'
    out = []
    for it in data:
        label = (it.get('name_zh') or it.get('name') or '').strip()
        tags = [t.strip().lower() for t in str(it.get('tags') or '').split(',') if t.strip()]
        # 只用纯 ascii 小写标签（与 Skill 标签同口径），过滤中文 tags_zh 与脏数据
        tags = [t for t in tags if TAG_RE.match(t)]
        if label and tags:
            out.append({'group': grp, 'label': label, 'tags': tags})
    return out


def compact(entries, max_per_group=200):
    """按 group 归并，保持出现顺序；同名 group 合并。"""
    groups, index = [], {}
    for e in entries:
        g = e['group']
        if g not in index:
            index[g] = len(groups)
            groups.append({'group': g, 'items': []})
        bucket = groups[index[g]]['items']
        if len(bucket) >= max_per_group:
            continue
        bucket.append({'label': e['label'], 'tags': e['tags'], **({'warn': e['warn']} if e['warn'] else {})})
    return groups


def find_skill_dir(explicit=None):
    if explicit:
        return explicit
    env = os.environ.get('ANIMA_SKILL_DIR')
    if env and os.path.isdir(env):
        return env
    for c in DEFAULT_SKILL_CANDIDATES:
        if os.path.isdir(os.path.join(c, 'references')):
            return c
    return None


def build_attire_slots():
    """把 Danbooru attire 分组固化成插件自带的槽位表，保证本插件可独立运行。"""
    src = next((p for p in ATTIRE_SRC_CANDIDATES if os.path.isfile(p)), None)
    if not src:
        print('  [跳过] 未找到 danbooru_attire_data.json，运行时将退回关键词判槽')
        return None
    with open(src, encoding='utf-8') as f:
        raw = json.load(f)

    slots, owner = {}, {}
    for group, slot in ATTIRE_GROUP_MAP.items():
        for t in raw.get(group, []):
            t = t.strip().lower()
            if not t:
                continue
            if t in owner:            # 组间重复 → 记下来，先到者胜
                continue
            owner[t] = group
            slots.setdefault(slot, []).append(t)

    payload = {
        'schema': 1,
        'source': os.path.basename(src),
        'source_path': src,
        'note': '源自 Danbooru tag group:attire 的分组快照，作为服装槽位判定的权威表',
        'group_map': ATTIRE_GROUP_MAP,
        'synced_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'slots': {k: sorted(set(v)) for k, v in slots.items()},
    }
    with open(ATTIRE_OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    total = sum(len(v) for v in payload['slots'].values())
    dist = '  '.join(f'{k}={len(v)}' for k, v in sorted(payload['slots'].items()))
    print(f'  槽位表 {total} 词  |  {dist}')
    print('  已写入:', ATTIRE_OUT)
    return payload


def build_character_pool():
    """把 Anima-Tools 的角色库固化成插件自带的角色池（含官方 trigger + 特征 tags）。

    两份数据合并：
      · character_official_data.json  key = "name||copyright" → {trigger, tags}
      · character_data.js             [{name, copyright, post_count, gender, hair, eye}]
    只用后者能补上热度/发色/瞳色，用来做筛选与加权；缺索引的角色仍保留（热度记 0）。
    """
    if not os.path.isfile(CHAR_OFFICIAL_SRC):
        print('  [跳过] 未找到 character_official_data.json')
        return None

    with open(CHAR_OFFICIAL_SRC, encoding='utf-8') as f:
        official = json.load(f)

    index = {}
    if os.path.isfile(CHAR_INDEX_SRC):
        raw = open(CHAR_INDEX_SRC, encoding='utf-8').read()
        a, b = raw.find('['), raw.rfind(']')
        try:
            for it in json.loads(raw[a:b + 1]):
                key = f"{it.get('name', '')}||{it.get('copyright', '')}".lower()
                index[key] = it
        except Exception as exc:
            print(f'  [警告] character_data.js 解析失败（仅影响热度/发色筛选）: {exc}')

    chars, female, male, other = [], 0, 0, 0
    for key, val in official.items():
        if '||' not in key:
            continue
        name, copyright_ = key.split('||', 1)
        tags = [t for t in (val.get('tags') or []) if t]
        meta = index.get(key.lower(), {})
        gender = meta.get('gender') or ('1girl' if '1girl' in tags else
                                        '1boy' if '1boy' in tags else '')
        if gender == '1girl':
            female += 1
        elif gender == '1boy':
            male += 1
        else:
            other += 1
        chars.append({
            'n': name, 'c': copyright_,
            't': val.get('trigger') or ', '.join(x for x in (name, copyright_) if x),
            'g': gender,
            'p': int(meta.get('post_count') or 0),
            'h': meta.get('hair') or '',
            'e': meta.get('eye') or '',
            'f': tags,
        })

    payload = {
        'schema': 1,
        'source': [os.path.basename(CHAR_OFFICIAL_SRC), os.path.basename(CHAR_INDEX_SRC)],
        'synced_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'fields': {'n': '角色名', 'c': '作品', 't': '官方 trigger',
                   'g': '性别', 'p': '同人作品数', 'h': '发色', 'e': '瞳色', 'f': '官方特征 tags'},
        'count': len(chars),
        'counts_by_gender': {'1girl': female, '1boy': male, 'other': other},
        'characters': sorted(chars, key=lambda x: -x['p']),
    }
    with open(CHAR_OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
    print(f"  角色池 {len(chars)} 个（女性 {female} / 男性 {male} / 其他 {other}）")
    print(f"  已写入: {CHAR_OUT}  ({os.path.getsize(CHAR_OUT)/1024/1024:.1f} MB)")
    top = ', '.join(f"{c['n']}({c['p']})" for c in payload['characters'][:5])
    print(f"  热度前五: {top}")
    return payload


def report_coverage():
    """用 Skill 的服装标签测一遍判槽覆盖率 —— misc 占比高就说明规则要补。"""
    sys.path.insert(0, PLUGIN_DIR)
    import wardrobe_rules as wr

    sec = (json.load(open(OUT_PATH, encoding='utf-8'))
           .get('sections', {}).get('clothing') or {})
    dist, misc = {}, []
    for g in sec.get('groups', []):
        for it in g.get('items', []):
            for t in it.get('tags', []):
                s = wr.classify_wear(t)
                dist[s] = dist.get(s, 0) + 1
                if s == 'misc':
                    misc.append(t)
    total = sum(dist.values())
    print('\n  判槽覆盖（服装 ' + str(total) + ' 个标签）:')
    for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f'    {k:10s} {v:4d}  {v * 100 // max(total, 1)}%')
    if misc:
        print(f'  未归类 {len(misc)} 个，样例: {misc[:12]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skill', help='anima3-prompt Skill 根目录')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    skill = find_skill_dir(args.skill)
    if not skill:
        sys.exit('找不到 anima3-prompt Skill 目录，请用 --skill 指定')
    refs = os.path.join(skill, 'references')
    print('Skill 目录:', skill)

    payload = {
        'schema': 1,
        'source': 'anima3-prompt skill',
        'source_dir': os.path.basename(skill),
        'synced_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'sections': {},
    }

    total_tags = 0
    for key, fname, label in SOURCES:
        path = os.path.join(refs, fname)
        if not os.path.isfile(path):
            print(f'  [跳过] {fname} 不存在')
            continue
        entries = parse_expression_md(path) if key == 'expression' else parse_md(path)
        groups = compact(entries)
        payload['sections'][key] = {'label': label, 'file': fname, 'groups': groups}
        n = sum(len(it['tags']) for g in groups for it in g['items'])
        w = sum(1 for g in groups for it in g['items'] for _ in it.get('warn', []))
        total_tags += n
        print(f'  {label:4s} {fname:20s} 分组 {len(groups):3d}  条目 '
              f'{sum(len(g["items"]) for g in groups):4d}  标签 {n:5d}  命中禁用表 {w}')

    # ---- SFM 扩充：从 Anima-Tools 的姿势/服装数据追加条目（第六项需求）----
    for kind, src, key, sec_label in (
            ("pose", SFM_POSE_SRC, "pose", "姿势"),
            ("clothing", SFM_CLOTHING_SRC, "clothing", "服装"),
    ):
        sfm = parse_sfm_js(src, kind)
        if not sfm:
            print(f'  [跳过] SFM {sec_label} 数据源不存在或无内容: {src}')
            continue
        sec = payload['sections'].get(key)
        if not sec:
            print(f'  [跳过] 无 {key} section，SFM {sec_label} 未追加')
            continue
        # 追加一个「SFM 扩充」组，不与现有组合并
        sfm_group = {'group': sfm[0]['group'], 'items': sfm}
        sec['groups'] = sec['groups'] + [sfm_group]
        n = sum(len(it['tags']) for it in sfm)
        total_tags += n
        print(f'  SFM {sec_label} 扩充: {len(sfm)} 条目 / {n} 标签 → {key}')

    payload['total_tags'] = total_tags
    print(f'合计标签 {total_tags}')

    if args.dry_run:
        print('[dry-run] 未写文件')
        return

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print('已写入:', OUT_PATH, f'({os.path.getsize(OUT_PATH)/1024:.0f} KB)')

    print('\n服装槽位表:')
    build_attire_slots()
    print('\n角色池:')
    build_character_pool()
    report_coverage()


if __name__ == '__main__':
    main()
