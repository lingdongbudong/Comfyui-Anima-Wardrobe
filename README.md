# Comfyui-Anima-Wardrobe

Anima **角色造型台**（服装 / 姿势 / 场景）+ **跨插件标签仲裁器**。
零第三方依赖，纯 Python + 原生前端 JS。

## 它解决什么

在 ComfyUI 里，多个插件都能往同一条提示词里写标签，而 `STRING` 连线不带任何元数据 ——
下游分不清一个 `black leotard` 是用户手动挑的，还是某个插件顺手补的。结果要么**静默覆盖**，
要么**过度保护**（换了角色发色却没变）。

本插件用「槽位契约 + 声明序号」把这件事变确定：

| 槽位 | 覆盖策略 |
|---|---|
| 画师 / **服装** / 姿势 / 场景 | **sticky —— 已写入即锁定，后来者只登记不写入** |
| 人数性别 / 角色身份 / **角色特征** / 自由词 | replaceable —— 后写覆盖 |

**一句话**：服装这类"挑过一次就不想被改"的槽，先写者锁定；
角色特征这类"跟着主体走"的槽，不受保护，换角色即整体替换。

完整协议见 [`docs/COORDINATION.md`](docs/COORDINATION.md)。

## 节点

| 节点 | 作用 |
|---|---|
| `AnimaWardrobeStudio`<br>**Anima 角色造型台** | 保留角色槽，新增 **服装 / 姿势 / 表情 / 场景** 四个槽 + **三排一键按钮**（服装 / 场景 / 随机搭配 / 姿势 / 表情 / 随机角色）。输出 `text` + 结构化声明 `wardrobe`。带 **巨乳开关**。 |
| `AnimaRandomGirl`<br>**Anima 随机女性角色** | 从 **5981 个女性角色**里按热度加权抽 1~3 个，连官方 trigger 与特征标签一起输出。支持按作品 / 发色 / 瞳色筛选，默认避开最近抽过的。**抽到 2 位时按等级自动补关系标签**（低等级正常互动 / 高等级百合 / 第 5 级极端百合）。带 **巨乳开关**。 |
| `AnimaNSFWGrader`<br>**Anima NSFW 分级判定** | 判定 **0 全年龄 / 1 擦边 / 2 成人 / 3 过激 / 4 极端** 五级 + 内容分类。可选把超标标签剔掉。**已从工作流移除**，节点类保留在菜单里。 |
| `AnimaWardrobeRoll`<br>**Anima 一键随机搭配** | 服务端版：**每次运行工作流自动随机**一套合理的服装+姿势+表情+场景。`seed=-1` 每次重抽，固定值可复现。 |
| `AnimaTagArbiter`<br>**Anima 标签仲裁器** | 最多汇总 4 路来源（本插件声明 / 任意插件的裸文本），按契约裁决。**放在链路末端即可保护上游所有插件。** |
| `AnimaWardrobeInspect`<br>**Anima 槽位归属检查器** | 只读：把输入文本的每个标签判槽结果打出来，用于排查某个标签为什么没被保护。 |

## 🎲 一键搭配

造型台节点上有一排按钮：

| 按钮 | 做什么 |
|---|---|
| **一键服装** | 抽一套**搭配合理**的服装（主体六选一 + 顺势叠袜/鞋/配饰/材质/状态/道具） |
| **一键场景** | 抽一个**与服装曝光度相容**的场景（场所 + 天气时辰 + 场景细节） |
| **一键随机搭配** | 同时抽服装 + 姿势 + 表情 + 场景，**保证互相合理** |
| **一键姿势** | 按等级抽一个姿势（SFW 档只出日常姿势，高等级才出性行为姿势） |
| **一键表情** | 表情**随当前姿势/服装自动匹配**（害羞 / 高潮 / 失神 / 抗拒…），也可纯随机 |
| **随机角色** / **随机角色 ×2** | 从女性角色库抽角色，连官方特征一起填进**角色槽**（不占服装槽）；抽 2 位时自动配关系标签 |
| 清空 | 清掉角色 / 服装 / 姿势 / 表情 / 场景 |

### ⭐ 表情自动匹配（新增）

表情不是随机乱抽，而是**跟着「当前在做什么」走**（数据源 = anima3-prompt Skill 的 `60-expression.md`）：

1. **动作语义识别**：从已抽出的姿势 / 服装 / 关系标签里匹配触发词 → 命中情绪类型
2. **强度定档**：由 NSFW 等级决定（sfw→平静 / suggestive→诱惑·羞耻 / explicit→愉悦 / hardcore→失神 / extreme→崩坏）
3. **输出**：表情标签 ≤3 个；Lv3 起强制叠加 ≥1 个身体反应标签

| 触发动作 | 情绪 | 示例标签 |
|---|---|---|
| 自慰 / 口交 / 性交 | 愉悦 | `moaning, blush, heavy breathing` |
| 高潮 / 痉挛 | 失神 | `ahegao, tongue out, rolling eyes` |
| 束缚 / 被迫 | 抗拒 | `scared, reluctant, tears` |
| 露出 / 被撞见 | 羞耻 | `embarrassed, blush, looking away` |
| 骑乘 / 主导 | 得意 | `smug, confident, dominant` |
| 睡奸 / 催眠 | 无意识 | `closed eyes, expressionless` |

### ⭐ 巨乳开关（新增）

`AnimaRandomGirl` 与造型台各有一个 **巨乳开关**。开启后，随机抽出的角色体型强制改为 **`huge breasts`**，替换掉原本的贫乳 / 中乳 / 大胸等体型词。随机逻辑见 `wardrobe_rules.roll_girl(huge_breasts=True)`。

### ⭐ SFM 内容扩充（新增）

预设库从 Anima-Tools 的 `pose_data.js`（日常姿势）与 `clothing_data.js`（成套服装）同步了额外内容：

- 姿势：**+294 个日常/手势姿势**（举手、交叉手臂、脱帽致意…），归入「SFM 日常姿势（扩充）」组，供 SFW / 低等级档抽取
- 服装：**+429 套成套服装**（晚礼服、水手服…），归入「SFM 服装套装（扩充）」组，供面板点选与随机搭配

**抽取等级由 `roll_level` 决定，直接对应 NSFW 五级标度（0-4）**：

| 控件值（合法 key） | 界面显示 | 抽出来的内容 |
|---|---|---|
| `sfw` | 0 · 全年龄 | 无任何暴露向标签 |
| `suggestive` | 1 · 擦边（默认） | 裁剪镂空 / 露肩 / 短裙 / 内衣轮廓 |
| `explicit` | 2 · 成人 | 全裸 / 性器官 / 乳头 / 透视 / 半脱 / 内衣外露 |
| `hardcore` | 3 · 过激 | 性行为 / 性道具 / 束缚调教 / 体液事后 |
| **`extreme`** | **4 · 极端（新增）** | **群体多人 / 极端插入扩张 / 拘束装置 / 极限体液 / 主奴标记** |
| `any` | 不限 | 全池随机 |

等级是**上限 + 保底**：不会超档（擦边档绝不抽成人内容），偶尔往安全方向差一档。
`allow_public` 打开后，公共交通等场所也允许搭配暴露度更高的服装。

> ⭐ **控件值是合法英文 key，界面显示中文** —— 中文由 `locales/*/nodeDefs.json` 的
> `values` 映射提供，值本身保持合法，所以不会出现「参数校验未通过：level 只能取 …」。
> 老工作流里的中文标签（`"1 · 擦边"`）、数字、旧 key 调接口也照样能用（`resolve_level` 归一）。

### 🔥 第 5 级 `extreme`（极端）—— 真正新增的档位

不是给旧标签改名，是**新内容 + 新行为**：

- **3 个新槽位**：`act` 极端行为（24 标签）/ `device` 拘束装置（13）/ `mark` 极限体液标记（11）
- **新场景细节**：`sex machine` / `bondage furniture` / `suspension bar` / `dungeon` …
- **抽取行为不同**：主体收窄为 整套/连体/分体/全裸；强制「行为 + 装置」成对出现；
  60% 追加极限体液；场景**只落私密 / 特殊**场所
- **等级之间不污染**：0–3 级抽 240 次，第 5 级内容出现 **0 次**

### 👭 双女性识别

识别到画面里有**两位及以上女性角色**时自动配关系标签：

| NSFW 级别 | 关系 | 标签 |
|---|---|---|
| 0 / 1 | **正常互动** | `friends` + 牵手 / 一起吃饭 / 逛街…；**场景优先落日常场所** |
| 2 / 3 | **百合** | `yuri` + 轻度或重度（`kiss` / `hugging` / `tribadism`…） |
| **4** | **极端百合** | `yuri` + `fisting` / `gaping` / `large insertion`… |

关系标签进 `relation` 槽（replaceable：随角色数与等级重算）。

三个判定标准（服装搭配合理性 / 场景合理性 / 成人裸露向分级）的完整规则见
**[`docs/ROLL_RULES.md`](docs/ROLL_RULES.md)**；NSFW 五级标度、第 5 级内容包与随机角色的抽取规则见
**[`docs/NSFW_AND_CHARACTER.md`](docs/NSFW_AND_CHARACTER.md)**。

**实测**：2240 次批量审计（五档 × 公开/非公开 × 单女/双女），
搭配/场景/等级 **违规 0 次**、档位命中 **99.82%**、**超档 0 次**；
NSFW **15 组**分级用例全中（含第 5 级 6 组）；双女识别 6 组用例全中、
SFW 场景 40/40 落日常场所；连续 40 抽角色不重复。自检 **73 项全过**。

> ⭐ **界面全中文**：节点名、控件名、提示、下拉选项都走 `locales/`。
> `locales/en/nodeDefs.json` 也写了中文 —— 因为 ComfyUI 默认 locale 是 `en`，
> 只给 `zh` 的话在默认设置下会显示原始英文控件名。

### 造型台控件

| 控件 | 默认 | 说明 |
|---|---|---|
| `character_tags` / `clothing_tags` / `pose_tags` / `scene_tags` | 空 | 四个槽的内容 |
| `clothing_policy` / `pose_policy` / `scene_policy` | `respect` | `respect` = 不覆盖上游插件已写的；`override` = 显式提权，本节点胜出 |
| `character_feature_policy` | `override` | `override` = 换角色即换特征（默认）；`protect` = 锁住上游角色特征不让下游改 |
| `order_mode` | `preserve` | `preserve` 不打乱原有词序；`normalize` 按规范槽位顺序重排 |
| `roll_level` | `suggestive` | 一键按钮抽什么级别（合法 key：`sfw`/`suggestive`/`explicit`/`hardcore`/`extreme`/`any`，界面显示中文） |
| `allow_public` | `false` | 是否允许公共/半公开场所承载高暴露服装 |

> ⭐ **工作流改了要先校验**：`python tools/verify_workflow.py <workflow.json> [--fix]`
> 会比对工作流里每个控件的值与节点声明的候选表，并检查链路完整性 ——
> combo 值写成中文标签这类问题不用重启 ComfyUI 就能发现。
> `python tools/selfcheck.py` 跑全量契约自检（73 项，不需要启动 ComfyUI）。

> ⭐ **空节点是逐字节透传的**。四个槽都留空时，输出与输入完全一致，
> 所以可以先把节点接进工作流，确认出图没变化，再慢慢挑内容。
> 这一条有回归测试覆盖（`tools/selfcheck.py` 第 9 组）。

## 安装

**方式一：ComfyUI Manager 一键安装（推荐）**

1. 打开 ComfyUI → 右侧菜单 → `Manager` → `Install Custom Nodes`
2. 搜索 **Anima-Wardrobe**（或 `comfyui-anima-wardrobe`）
3. 点 `Install`，装完 **重启 ComfyUI**
4. 之后有新版本，Manager 里会直接显示可更新，点一下即可

**方式二：手动安装**

1. 把本目录整个放进 `ComfyUI/custom_nodes/`
2. **重启 ComfyUI**（新增节点包必须重启）
3. 节点出现在右键菜单 → `AnimaWardrobe` 分类

**依赖**：无。零第三方 Python 依赖，纯原生前端 JS。

预设数据已随插件分发（`presets/anima_presets.json`，324 KB；
角色库 `presets/character_pool.json`，2.3 MB）。
阿尼玛 Skill 更新后想同步预设：

```bash
python tools/sync_presets.py                 # 自动探测 Skill 目录
python tools/sync_presets.py --skill <path>  # 手动指定
python tools/sync_presets.py --dry-run       # 只看统计
```

> 同步扩充数据（SFM 姿势 / 服装套装）需要 `Comfyui-Anima-Tools` 与本插件同级安装；
> 若放在别处，用环境变量指定：`ANIMA_TOOLS_DIR=<路径> python tools/sync_presets.py`

## 自检

```bash
python tools/selfcheck.py
```

覆盖 9 组共 20+ 项断言：节点 schema、槽位契约、三层权威、粘性槽先写锁定、
角色特征可覆盖、提权、词典判槽、以及**空节点透传回归**。
不需要启动 ComfyUI。

## 接口

| 路由 | 返回 |
|---|---|
| `GET /anima-wardrobe/presets` | 预设库（分组 → 条目 → 标签） |
| `GET /anima-wardrobe/contract` | 槽位契约（供前端与其他插件读取） |

## 给其他插件作者的扩展点

想让自己写的标签也受保护 / 参与仲裁，两条路：

1. **输出结构化声明**（推荐）：`RETURN_TYPES` 里加 `ANIMA_WARDROBE`，
   输出 `{"v":1,"source":"你的节点名","entries":[{"slot":"clothing","tags":[...],
   "tier":"explicit","force":false,"seq":N,"source":"你的节点名"}]}`。
   `seq` 取 `1 + 上游最大 seq` 即可保持"先写优先"语义。
2. **什么都不做**：只输出裸 `STRING` 也能被仲裁器识别 ——
   它会用 2697 个标签的归属词典判槽，判不出的进 `free` 保留。

## 目录

```
Comfyui-Anima-Wardrobe/
├── __init__.py                    节点注册 + HTTP 路由
├── nodes.py                       槽位契约 / 仲裁算法 / 六个节点
├── wardrobe_rules.py              服装 / 场景 / 角色 / 分级规则引擎
├── presets/anima_presets.json     从 Skill 提取的预设（324 KB）
├── presets/character_pool.json    5981 个女性角色库（2.3 MB）
├── js/anima_wardrobe_studio.js    预设面板 + 仲裁报告显示
├── locales/{en,zh}/nodeDefs.json  界面文案
├── tools/sync_presets.py          从 Skill 重新生成预设
├── tools/selfcheck.py             契约自检（无需启动 ComfyUI）
├── tools/verify_workflow.py       工作流控件值校验
├── docs/COORDINATION.md           跨插件协调协议
├── docs/ROLL_RULES.md             搭配 / 场景 / 分级完整规则
├── docs/NSFW_AND_CHARACTER.md     NSFW 五级标度 + 角色抽取规则
├── pyproject.toml                 ComfyUI Registry 发布元数据
└── README.md
```

## 与 Anima-Tools 的关系

本插件与 [`Comfyui-Anima-Tools`](https://github.com/nregret/Comfyui-Anima-Tools)
**不是同一个项目，也非同一作者**，两者互相独立、互不隶属。

但本插件确实受益于它 —— `presets/attire_slots.json` 的服装分组快照，
以及 SFM 扩充的姿势 / 成套服装数据，均取自 Anima-Tools 公开的
`danbooru_attire_data.json` / `pose_data.js` / `clothing_data.js`。
在此致谢原作者 **nregret**（MIT License）。

`tools/sync_presets.py` 需要本机装有 Anima-Tools 才能**重新同步**这部分扩充数据；
不装也不影响插件运行 —— 数据已随插件完整分发。

## 使用须知

本插件输出 **Danbooru 风格英文标签**，内容分级完全由你通过 `roll_level`
与 `allow_public` 控制。请自行确保生成内容符合你所在地区的法律法规与所用平台的政策，
不得用于制作涉及未成年人的性化内容。

`presets/character_pool.json` 里的角色均取自公开的 Danbooru 标签统计，
插件不随之提供任何图像。

## 许可

MIT License —— 详见 [LICENSE](LICENSE)。
