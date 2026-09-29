# NSFW 分级判定 · 随机女性角色

> 版本 v1 ｜ 2026-09-29 ｜ 引擎在 `wardrobe_rules.py`，本文件与代码同步维护

---

## 一、NSFW 等级判定

### 五级标度（0–4）

| 等级 | key（**接口合法值**） | 中文 | 含义 |
|---|---|---|---|
| 0 | `sfw` | 全年龄 | 无任何暴露向标签 |
| 1 | `suggestive` | 擦边 | 轻度暗示：裁剪镂空 / 露肩 / 短裙 / 内衣轮廓 |
| 2 | `explicit` | 成人 | 明显裸露或透视：全裸 / 性器官 / 乳头 / 透视 / 半脱 / 内衣外露 |
| 3 | `hardcore` | 过激 | 性行为 / 性道具 / 束缚调教 / 体液事后 |
| **4** | **`extreme`** | **极端（新增）** | **群体多人 / 极端插入扩张 / 拘束装置 / 极限体液 / 主奴标记** |

外加一个非等级值 `any`（不限，不做等级约束）。

### ⭐ 等级词汇表（唯一真源）

`LEVELS = ("sfw", "suggestive", "explicit", "hardcore", "extreme", "any")`

| 位置 | 用什么 |
|---|---|
| 节点控件 / HTTP 的 `level` | **必须**是上面 6 个 key 之一 |
| 界面显示 | `locales/*/nodeDefs.json` 的 `values` 映射成中文（值不变，只改显示） |
| 老工作流 / 外部调用传中文标签、数字、旧 key | `resolve_level()` 归一后再用，**不会报错** |

`resolve_level(value, default)` 的契约：**永远不抛异常，返回值永远 ∈ LEVELS**。
认不出来的一律回落到 `default`（默认 `suggestive`）。

> 兼容表 `LEVEL_ALIAS` 收录了历史写法：`"1 · 擦边"` / `"擦边"` / `"1"` / `"ecchi"`
> → `suggestive`，`"4 · 极端"` / `"极端"` / `"4"` / `"extreme_nsfw"` → `extreme`，等等。

> ⚠️ **踩过的坑**：早期控件值是中文标签（`"1 · 擦边"`），而接口校验拿的是 `LEVELS`
> 这套英文 key，两边对不上 → 点一键按钮直接报「level 只能取 (…) 之一」。
> 现在**控件值就是合法 key**，中文只由语言包负责显示；
> 并且有回归测试盯着：`tools/selfcheck.py` 会断言所有节点的等级控件可选值 ⊆ `LEVELS`。

### 内容分类（回答"到底有什么成人向内容"）

| 分类 | 等级 | 典型标签 |
|---|---|---|
| **群体/多人** | **4** | `group sex` `threesome` `ffm/mf threesome` `foursome` `gangbang` `orgy` `spitroast` `bukkake` |
| **极端插入/扩张** | **4** | `double/triple penetration` `large insertion` `gaping` `stomach bulge` `fisting` `anal/vaginal fisting` `cervix` |
| **极端拘束/装置** | **4** | `suspension` `suspension bondage` `inverted suspension` `stocks` `pillory` `wooden horse` `bondage furniture` `sex machine` `fuck machine` `vacuum bed` `mummification` `restraint cage` |
| **极限体液** | **4** | `excessive cum` `cum inflation` `cum bath` `wet and messy` `peeing` `urination` `incontinence` `golden shower` `lactation` `milking` `squirt` |
| **主奴/标记** | **4** | `slave` `pet play` `branding` `collar and leash` `human pet` `leash in mouth` |
| 性行为 | 3 | `deepthroat` `fellatio` `cunnilingus` `handjob` `paizuri` `masturbation` `creampie` |
| 性道具 | 3 | `vibrator` `dildo` `butt plug` `anal beads` `onahole` `chastity belt` |
| 束缚/BDSM | 3 | `bondage` `shibari` `ball gag` `blindfold` `leash` `handcuffs` `ropes` |
| 体液/事后 | 3 | `cum` `creampie` `used condom` `pussy juice stain` `body writing` |
| 裸露 | 2 | `completely nude` `pussy cut` `nipples` `no panties` `bottomless` `pasties` `cameltoe` |
| 透视/湿身 | 2 | `see-through shirt` `transparent dress` `wet clothes` `lingerie` `no bra` `skintight` |
| 半脱/掀起 | 2 | `open shirt` `partially undressed` `pants down` `bra exposed` `upskirt` `open fly` |
| 百合 | 2 | `yuri` `girls love` `lesbian` `kiss` `french kiss` |
| 裁剪/镂空 | 1 | `micro skirt` `highleg` `cutout` 系列 `off shoulder` `backless` `sleeveless` |
| 暴露癖/情境 | 1 | `outdoors` `public` `webcam` `recording` `torn` `bloodstain` |
| 轻度暗示 | 1 | `nightgown` `babydoll` `corset` `sundress` `shrug` |

### 判定算法

```
1. 命中 群体 / 极端插入扩张 / 极端拘束装置 / 极限体液 / 主奴标记  → 极端 (4)
2. 命中 性行为 / 性道具 / 束缚 / 体液                            → 过激 (3)
3. 否则 取 max(基线等级, 命中分类的最高等级)                     → 0/1/2
   基线等级来自 grade_explicitness：
     explicit → 2 / suggestive → 1 / sfw → 0
4. 无任何命中 → 全年龄 (0)
```

> **不要让两套表各判一套。** 现在全插件只有**一个判定真源** `_nsfw_core()`：
> NSFW 分类表 + 基线，四个对外函数（`grade_nsfw` / `grade_explicitness` / `strip_nsfw` /
> `_pool_allowed`）全部由它派生。早期版本池过滤和分级用了两套不一致的匹配逻辑，
> 导致「sfw 档抽出 suggestive 内容」「擦边档抽出成人内容」反复出现。

### 第 5 级（extreme）的配套内容与行为

这是**真正新增的档位**，不是给已有标签改名 —— 从内容到行为都是新的：

**① 新增 3 个槽位，装载新标签**（`presets` 里原本没有这些标签）

| 槽位 | 中文 | 标签数 | 上限 |
|---|---|---|---|
| `act` | 极端行为 | 24 | 2 |
| `device` | 拘束/装置 | 13 | 2 |
| `mark` | 极限体液/标记 | 11 | 2 |

**② 新增一批场景细节**（`EXTREME_DETAILS`）：`sex machine` `bondage furniture`
`suspension bar` `dungeon` `many used condoms` —— 只有第 5 级场景才会出现。

**③ 抽取行为不一样**

- 主体形态收窄为 整套 / 连体 / 分体 / 全裸（泳装、内衣套装与「装置 + 行为」语义冲突，规则 E2 会判违规）
- 强制抽 1 个**极端行为**，再配 1–2 个**拘束装置**（装置必须有行为承载，规则 E1）
- 60% 概率追加 1 个极限体液/标记
- 场景只落**私密 / 特殊**场所（卧室 / 浴室 / 酒店 / 地牢 / 实验室…），实测 60/60
- 双女性 + 第 5 级 → **极端百合**（`yuri` + `fisting` / `gaping` / `large insertion`…）

**④ 等级之间互不污染**（有实测断言）
0–3 级抽 240 次，第 5 级标签出现 **0 次**；第 5 级场景细节漏到低等级 **0 次**。

### 三种使用方式

1. **一键抽取等级**（主要用法）：造型台的 `roll_level` 控件，值就是 6 个合法 key。
   每次一键抽取后，节点底部会显示本次的 NSFW 等级（0–4）与内容分类。
2. **`Anima NSFW 分级判定` 节点**（已从工作流移除，节点类仍在插件菜单里，需要时拖回来）
   - `只报告`：输出 `level` / `level_num` / `report`，不改文本
   - `过滤超标标签`：额外把超过 `max_level` 的标签从文本里剔除
   - 输出：`text`（可能已过滤）、`level`、`level_num`（**0–4**）、`report`（分类明细）
3. **HTTP**：
   ```http
   POST /anima-wardrobe/nsfw        {"text": "..."}
   GET  /anima-wardrobe/nsfw-scale  → 标度 + 分类 + 第 5 级内容包清单
   POST /anima-wardrobe/roll        {"section":"all","level":"extreme",...}
   ```

> 接口对 `level` 的处理顺序是**先归一、再校验**：任何写法都先过 `resolve_level()`，
> 收敛结果必然合法，所以不会再因为「前端值的写法」而返回 400。

### 已验证

15 组分级用例全部命中（含第 5 级 6 组：群体 / 插入扩张 / 拘束装置 / 极限体液 /
主奴标记 / 混合文案）；上限过滤正确（上限 2 时 `deepthroat` 被剔除，`see-through shirt` 保留）；
23 种 `level` 写法全部归一为合法值；四个节点的等级控件可选值与默认值全部合法。

---

## 二、随机女性角色

### 节点：`Anima 随机女性角色（含官方标签特征）`

数据源 = 角色库（**5981 个女性角色**），含官方 trigger、特征 tags、同人作品数、发色、瞳色。

| 输入 | 默认 | 说明 |
|---|---|---|
| `pool` | 热门 TOP200 | 热门 TOP200 / TOP1000 / 全部女性（5981）/ 按作品 / 按发色 / 按瞳色 |
| `level` | 1 · 擦边 | 抽取等级（NSFW 四级）—— 决定双女性时配什么关系标签 |
| `relation` | 自动（按等级） | 双女关系标签：自动 / 强制正常互动 / 强制百合向 / 不加 |
| `keyword` | 空 | 池选「按作品 / 按发色 / 按瞳色」时生效，例：`genshin`、`white`、`blue` |
| `min_post_count` | 0 | 过滤太冷门的角色 |
| `character_count` | 1 | 1–3 个 |
| `include_features` | 开 | 关掉则只出角色名与作品名 |
| `avoid_repeat` | 开 | 避开最近 40 次抽过的角色 |
| `seed` | -1 | -1 每次随机；固定值可复现 |

输出：`text` / `wardrobe`（声明）/ `report`。

### 抽取规则

- **加权随机**：权重 = `sqrt(同人作品数 + 1)` —— 热门角色更容易抽到，但不至于翻来覆去只有那几个
- **避免重复**：最近 40 次抽过的角色排除（候选不足时才允许重复）。实测连续 40 抽**一个不重**
- **多角色**：抽 2–3 个时人数词自动变 `2girls` / `3girls`，标签**合并成一组**输出

> ⚠️ 多角色必须合并成**一条** entry。拆成多条的话，同层同序号在仲裁下只会活一个，另一个角色会被静默丢掉。

### 槽位归属（关键设计）

| 内容 | 归到哪个槽 | 为什么 |
|---|---|---|
| `1girl` / `2girls` | `count` 槽 | 人数性别词 |
| 官方 trigger（角色名 + 作品名） | `character` 槽 | 角色身份 |
| 官方特征（发色 / 瞳色 / 发型 / 兽耳 / 翅膀 / 标志性服饰…） | `character` 槽 | **刻意不占服装槽** |

角色的标志性装扮（`witch hat`、`detached sleeves`、`black wings`）**属于角色身份**，不是用户挑的衣服。
如果把它们塞进服装槽，服装槽是粘性槽 → 一旦角色带来一件"衣服"，用户就再也挑不了衣服了。

### 一键按钮

造型台节点第二排：**🎲 随机角色** / **🎲 随机角色 ×2**（用默认 TOP200 池，一键填充角色槽）。
需要按作品 / 发色筛选时用独立的 `Anima 随机女性角色` 节点。

### 已验证

池内 5981 个**全部为女性**且都有官方 trigger；连续 40 抽不重复；
一次抽 3 个互不重复；按作品筛选正确（`genshin` 命中 92 个候选）。

---

## 三、界面中文化

- 节点显示名、每个输入控件的名称与提示、下拉选项的中文标签，全部走 `locales/zh/nodeDefs.json`
- 前端 JS 里的按钮 / 报告文案本身就是中文
- ⚠️ **`locales/en/nodeDefs.json` 也写了同一份中文**。原因：ComfyUI 默认 locale 是 `en`，
  只提供 `zh` 的话在默认设置下会显示原始英文控件名（`clothing_tags` 这种）。
  本插件没有英文受众，所以两边都给中文，保证任何设置下都是中文界面。
  → 想改回英文界面时，把 `locales/en/nodeDefs.json` 换成英文即可。


---

## 四、双女性识别 → 关系标签

### 触发

识别到画面里有**两位及以上女性角色**时自动配关系标签。判定依据（任一命中，且画面里没有男性）：

- 人数词 `2girls` / `3girls` / `multiple girls`
- `1girl` 出现两次及以上
- 关系词 `yuri` / `girls love` / `lesbian`

### 关系标签怎么选

| NSFW 级别 | 关系 | 标签 |
|---|---|---|
| 0 全年龄 / 1 擦边 | **正常互动** | `friends` + 2 个日常互动（牵手 / 一起吃饭 / 逛街 / 聊天 / 打闹…） |
| 2 成人 | **百合** | `yuri` + 2 个轻度（`kiss` / `french kiss` / `hugging` / `nude`…） |
| 3 过激 | **百合** | `yuri` + 2 个重度（`tribadism` / `cunnilingus` / `fingering`…） |
| **4 极端（新增）** | **极端百合** | `yuri` + 2 个第 5 级专属（`fisting` / `vaginal fisting` / `anal fisting` / `large insertion` / `gaping`） |

**双女性 + SFW 时，场景还会优先落日常场所**（教室 / 公园 / 客厅 / 厨房 / 餐厅 / 海边…），
实测 40/40 全部落在日常场所。

节点的 `双女关系标签` 控件可强制指定：
`自动（按等级）` 按上表走；`正常互动` 强制 0 级；
**`百合向` = 至少百合但不主动升到第 5 级**（取 `min(max(等级,2),3)`）；`不加` 不生成。

### 两条防坑经验

1. **`holding hands` / `looking at each other` 这类通用互动词不能进「百合」分类表**。
   它们同时出现在 SFW 互动池里，收进百合表会把「SFW 双人正常互动」顶到 2 级。
2. **百合标签要按露骨度分两档**（`kiss` 一档、`tribadism` 一档）。
   混在一起的话「成人档」会随机抽到过激行为，导致超档。

### 关系标签进哪个槽

新增了一个 `relation` 槽（在 `appearance` 之后、`clothing` 之前），策略是 **replaceable**：
关系是随「角色数量 + 等级」重算的派生信息，所以**后写者覆盖**，不参与防覆盖保护。
