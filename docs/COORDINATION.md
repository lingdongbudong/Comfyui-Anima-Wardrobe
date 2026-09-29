# 跨插件标签协调协议（Anima Wardrobe Coordination Protocol）

> 版本 v1 ｜ 2026-09-29 ｜ 适用于 ComfyUI 提示词类自定义节点

## 0. 要解决的问题

同一条提示词链上常有多个插件都能写标签：

```
Anima-Tools 服装选择器 ─┐
Anima-Tools 角色选择器 ─┤
本插件 角色造型台     ─┼─→ …… ─→ CLIPTextEncode
其他任意第三方节点     ─┘
```

ComfyUI 的 `STRING` 连线**不携带任何元数据**——下游只能看到一串字符，无从分辨
"这个 `black leotard` 是用户手动挑的，还是某个插件顺手补的"。
于是出现两种坏结果：

1. **静默覆盖**：用户已经挑好衣服，下游插件又写了一套，两套叠在一起，出图跑偏。
2. **过度保护**：把角色特征也一并锁死，换了角色却还是旧发色。

本协议要同时避免这两种情况。

---

## 1. 核心概念：槽位（slot）

提示词按语义拆成 8 个**互斥槽位**，每个 token 有且只有一个归属：

| 槽位 | 含义 | 覆盖策略 | 说明 |
|---|---|---|---|
| `artist` | 画师 | **sticky** | 已写入即锁定 |
| `count` | 人数/性别 | replaceable | 随角色数重算 |
| `character` | 角色身份（角色名 + 作品） | replaceable | 换角色必须生效 |
| `appearance` | **角色特征**（发色/瞳色/体型/兽耳…） | replaceable | ⭐ **不受防覆盖保护** |
| `clothing` | **服装** | **sticky** | ⭐ 已写入即锁定 |
| `pose` | 姿势 | **sticky** | 已写入即锁定 |
| `scene` | 场景/环境 | **sticky** | 已写入即锁定 |
| `free` | 其他自由词 | replaceable | 兜底，永不丢弃 |

> **sticky（粘性槽）** = 先写者锁定，后到者只登记不写入 → 这就是"已写入的服装不被其他插件自动覆盖"。
> **replaceable（派生槽）** = 后写者覆盖 → 这就是"角色特征标签不受覆盖保护"。

---

## 2. 声明（Claim）：让元数据跟着数据走

因为字符串带不了元数据，本协议引入一个**旁路类型** `ANIMA_WARDROBE`：

```json
{
  "v": 1,
  "source": "角色造型台",
  "entries": [
    {"slot": "clothing", "tags": ["black leotard"], "tier": "explicit",
     "force": false, "seq": 2, "source": "角色造型台"}
  ]
}
```

| 字段 | 作用 |
|---|---|
| `slot` | 槽位归属 |
| `tags` | 该来源在这个槽里写的标签 |
| `tier` | 层级：`locked`(3) > `explicit`(2) > `derived`(1) > `default`(0) |
| `force` | 用户显式提权，强制覆盖上游 |
| `seq` | **声明序号**，定义"谁先谁后" |
| `source` | 来源名，用于仲裁报告 |

### ⭐ 序号（seq）怎么定义"先写"

DAG 里没有时间轴，所以用**沿链路单调递增**来定义：

- 一条线上最上游的节点 `seq` 最小；
- 节点把自己的标签定为 `1 + 上游最大 seq`；
- 同一节点内多个输入端口，按**端口从左到右**递增。

于是"此前选择的"= `seq` 更小者，含义确定、可复现、可解释。

---

## 3. 仲裁算法

对每个槽位，把候选条目按下面 4 条规则依次收窄：

```
R1  提权优先      force=True 的条目独立成候选池（用户显式要求覆盖）
R2  层级优先      取 tier 最高的一档
R3  同层决胜      sticky 槽 → seq 最小者胜（先写锁定）
                  replaceable 槽 → seq 最大者胜（随主体重算）
R4  落选不丢弃    全部登记为 shadowed，节点 UI 上可见，可一键提权
```

### 一个例子

| 来源 | seq | 写的内容 |
|---|---|---|
| 上游文本 | 0 | `1girl, solo, long black hair, school uniform` |
| 本插件造型台 | 1 | `black leotard`（clothing_policy=respect） |

仲裁结果：

```
clothing   : 取 seq=0 的 school uniform   ← 先写锁定，black leotard 进 shadowed
appearance : long black hair              ← 原样保留
```

把 `clothing_policy` 改成 `override` → `force=True` → 命中 R1 → `black leotard` 胜出。

---

## 4. 怎么和"不认识本协议"的插件共存

这是关键：**第三方插件只会输出裸字符串**。仲裁器必须能读懂它。

做法是 **标签归属词典（tag → slot index）**：

- 启动时把 anima3-prompt（阿尼玛）Skill 的 `40-clothing.md` / `80-scene.md` / `50-pose-action.md`
  解析成约 **2700 个** 已索引标签，任一 token 命中即判槽；
- 另外用正则兜底识别 `@画师`、`1girl/solo` 这类人数词、`xx hair / xx eyes` 这类角色特征词；
- 判不出来的一律进 `free`，**按原顺序保留，永不丢弃**。

因此即使是完全不知道本协议的节点，只要它写的是丹波（Danbooru）风格标签，
就能被正确判槽，从而参与"先写锁定"的仲裁。

### 兼容性对照

| 上游插件类型 | 输出 | 本协议怎么处理 |
|---|---|---|
| 本插件节点 | `ANIMA_WARDROBE` 声明 | 直接读结构化槽位，最精确 |
| Anima-Tools 选择器 | 裸 `STRING` | 词典判槽，按 `explicit` 处理 |
| 任意第三方 / 手写文本 | 裸 `STRING` | 同上；判不出的进 `free` |
| 画师前缀 | `@choujiroo,` | 识别为 `artist` 槽并重排到最前 |

---

## 5. 输出顺序

`normalize` 模式按规范槽位顺序重排（与 Skill 的槽位规范一致）：

```
artist → count → character → appearance → clothing → pose → scene → free
```

`preserve` 模式**不打乱原有词序**，只删除被遮挡（losing）的 token，
新加入的标签追加在末尾。原句里没出现过、由本节点新增的 token 在两种模式下都会补进去。

---

## 6. 使用建议

1. **仲裁器放链路末端**。这样它能对上游所有插件的写入做一次统一裁决。
   如果只想覆盖单个节点，用造型台自带的仲裁能力即可，不必额外接仲裁器。
2. **粘性槽想换内容时用 `override`**，不要靠"把上游清空"——清空会丢掉上游其他有用的词。
3. **角色特征默认走 `override`**（换角色即换特征）。
   只有当上游角色特征是权威来源、不希望被下游改写时，才把造型台的
   `character_feature_policy` 设为 `protect`。
4. 运行一次后，节点底部会打印**仲裁报告**：哪个槽谁赢了、谁被遮挡、来源是谁。

---

## 7. 边界与已知取舍

- 词典判槽是**启发式**。未被索引的自由词（如 `eager sucking`）会落到 `free` 槽并在
  `normalize` 模式下被排到末尾。若你的提示词严重依赖原有词序，用 `preserve` 模式。
- `force` 提权只能在同一槽内生效，不会跨槽搬运标签。
- 本协议**不阻止**上游插件写标签，只是在下游做一次裁决——
  这是刻意的：不修改别人的节点行为，才能保证组合时不互相破坏。
