import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

/* ==========================================================================
 * Comfyui-Anima-Wardrobe —— 前端面板
 *  · 角色造型台：从 anima3-prompt（阿尼玛）Skill 预设里点选 服装 / 姿势 / 场景
 *  · 仲裁器 / 检查器：显示槽位仲裁报告（谁赢了、谁被遮挡）
 *  ========================================================================== */

const PRESET_URL = "/anima-wardrobe/presets";
const CONTRACT_URL = "/anima-wardrobe/contract";

const studioStates = new WeakMap();

let presetsCache = null;
let presetsPromise = null;
let contractCache = null;

function fetchPresets() {
    if (presetsCache) return Promise.resolve(presetsCache);
    if (!presetsPromise) {
        presetsPromise = fetch(PRESET_URL)
            .then(r => r.json())
            .then(d => { presetsCache = d; return d; })
            .catch(e => { presetsPromise = null; throw e; });
    }
    return presetsPromise;
}

function fetchContract() {
    if (contractCache) return Promise.resolve(contractCache);
    return fetch(CONTRACT_URL).then(r => r.json())
        .then(d => { contractCache = d; return d; })
        .catch(() => null);
}

/* ---------- 小工具 ---------- */

function makeBtn(label, bg, fg) {
    const b = document.createElement("button");
    b.textContent = label;
    b.style.cssText = `flex:1;min-width:0;height:26px;border-radius:6px;cursor:pointer;
        border:1px solid ${bg};background:${bg};color:${fg};font-size:12px;
        font-family:inherit;padding:0 4px;white-space:nowrap;overflow:hidden;
        text-overflow:ellipsis;`;
    b.onmouseenter = () => { b.style.filter = "brightness(0.93)"; };
    b.onmouseleave = () => { b.style.filter = ""; };
    return b;
}

function splitTags(value) {
    return String(value || "").split(",").map(s => s.trim()).filter(Boolean);
}

function setWidgetValue(node, name, value) {
    const w = node.widgets?.find(x => x.name === name);
    if (!w) return;
    w.value = value;
    if (w.inputEl) {
        w.inputEl.value = value;
        w.inputEl.dispatchEvent(new Event("input", { bubbles: true }));
    }
    w.callback?.(value);
    node.graph?.setDirtyCanvas(true, true);
}

/* ---------- 预设选择弹窗 ---------- */

let modalEl = null;

function closeModal() {
    if (modalEl) { modalEl.remove(); modalEl = null; }
}

function openPicker(node, widgetName, sectionKey, title, accent) {
    closeModal();
    fetchPresets().then(data => {
        const section = data?.sections?.[sectionKey];
        if (!section) {
            alert(`预设库里没有「${title}」这一节，请先运行 tools/sync_presets.py`);
            return;
        }
        buildModal(node, widgetName, title, accent, section);
    }).catch(e => {
        alert("读取预设失败：" + e);
    });
}

function buildModal(node, widgetName, title, accent, section) {
    const overlay = document.createElement("div");
    overlay.style.cssText = `position:fixed;inset:0;background:rgba(0,0,0,.45);
        z-index:10000;display:flex;align-items:center;justify-content:center;`;

    const box = document.createElement("div");
    box.style.cssText = `width:min(1040px,92vw);height:min(700px,86vh);background:#1e1e1e;
        color:#ddd;border-radius:12px;display:flex;flex-direction:column;
        box-shadow:0 12px 40px rgba(0,0,0,.5);overflow:hidden;
        font-family:system-ui,-apple-system,"Segoe UI",sans-serif;`;

    /* 头部 */
    const head = document.createElement("div");
    head.style.cssText = `display:flex;align-items:center;gap:10px;padding:12px 16px;
        border-bottom:1px solid #333;flex:0 0 auto;`;
    const h = document.createElement("div");
    h.textContent = `阿尼玛预设 · ${title}`;
    h.style.cssText = `font-size:14px;font-weight:600;color:${accent};`;
    const search = document.createElement("input");
    search.placeholder = "搜索标签 / 条目名…";
    search.style.cssText = `flex:1;height:30px;border-radius:6px;border:1px solid #444;
        background:#141414;color:#ddd;padding:0 10px;font-size:13px;outline:none;`;
    const closeBtn = makeBtn("关闭", "#3a3a3a", "#ddd");
    closeBtn.style.flex = "0 0 64px";
    closeBtn.onclick = closeModal;
    head.append(h, search, closeBtn);

    /* 主体 */
    const body = document.createElement("div");
    body.style.cssText = "flex:1 1 auto;display:flex;min-height:0;";

    const nav = document.createElement("div");
    nav.style.cssText = `width:230px;flex:0 0 230px;overflow-y:auto;border-right:1px solid #333;
        padding:8px;background:#191919;`;
    const list = document.createElement("div");
    list.style.cssText = "flex:1 1 auto;overflow-y:auto;padding:12px;";

    /* 底部：当前已选 */
    const foot = document.createElement("div");
    foot.style.cssText = `flex:0 0 auto;border-top:1px solid #333;padding:10px 14px;
        max-height:132px;overflow-y:auto;background:#191919;`;
    const footTitle = document.createElement("div");
    footTitle.textContent = "当前已选（点击 × 删除）";
    footTitle.style.cssText = "font-size:12px;color:#888;margin-bottom:6px;";
    const chips = document.createElement("div");
    chips.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;";
    foot.append(footTitle, chips);

    body.append(nav, list);
    box.append(head, body, foot);
    overlay.append(box);
    document.body.append(overlay);
    modalEl = overlay;

    overlay.addEventListener("mousedown", e => { if (e.target === overlay) closeModal(); });
    document.addEventListener("keydown", function esc(e) {
        if (e.key === "Escape") { closeModal(); document.removeEventListener("keydown", esc); }
    });

    /* 渲染当前选中 chips */
    function renderChips() {
        const w = node.widgets?.find(x => x.name === widgetName);
        const tags = splitTags(w?.value);
        chips.innerHTML = "";
        if (!tags.length) {
            const empty = document.createElement("span");
            empty.textContent = "（空）";
            empty.style.cssText = "font-size:12px;color:#666;";
            chips.append(empty);
            return;
        }
        tags.forEach(t => {
            const c = document.createElement("span");
            c.style.cssText = `display:inline-flex;align-items:center;gap:6px;font-size:12px;
                background:${accent}22;border:1px solid ${accent}66;color:${accent};
                border-radius:999px;padding:3px 8px;cursor:pointer;`;
            c.textContent = t;
            const x = document.createElement("span");
            x.textContent = "×";
            x.style.cssText = "opacity:.7;font-size:13px;";
            c.append(x);
            c.onclick = () => {
                const now = splitTags(node.widgets?.find(y => y.name === widgetName)?.value)
                    .filter(v => v.toLowerCase() !== t.toLowerCase());
                setWidgetValue(node, widgetName, now.join(", "));
                renderChips();
            };
            chips.append(c);
        });
    }

    function addTag(tag) {
        const w = node.widgets?.find(x => x.name === widgetName);
        const now = splitTags(w?.value);
        if (now.some(v => v.toLowerCase() === tag.toLowerCase())) return;
        now.push(tag);
        setWidgetValue(node, widgetName, now.join(", "));
        renderChips();
    }

    /* 渲染条目 */
    function renderGroups(filter) {
        nav.innerHTML = "";
        const q = (filter || "").trim().toLowerCase();
        let groupOffsetY = 0;

        section.groups.forEach(g => {
            const anchor = document.createElement("div");
            anchor.id = "";
            const navItem = document.createElement("div");
            const count = g.items.reduce((n, it) => n + it.tags.length, 0);
            navItem.textContent = `${g.group}（${count}）`;
            navItem.style.cssText = `font-size:12px;padding:7px 9px;border-radius:6px;
                cursor:pointer;color:#bbb;margin-bottom:2px;`;
            navItem.onmouseenter = () => { navItem.style.background = "#262626"; };
            navItem.onmouseleave = () => { navItem.style.background = "transparent"; };
            navItem.onclick = () => {
                const target = list.querySelector(`[data-group="${CSS.escape(g.group)}"]`);
                if (target) list.scrollTop = target.offsetTop - 12;
            };

            const block = document.createElement("div");
            block.dataset.group = g.group;
            block.style.cssText = "margin-bottom:20px;";
            const gt = document.createElement("div");
            gt.textContent = g.group;
            gt.style.cssText = `font-size:13px;font-weight:600;color:${accent};
                margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid #2e2e2e;`;
            block.append(gt);

            const cards = document.createElement("div");
            cards.style.cssText = "display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:8px;";

            g.items.forEach(item => {
                const hit = !q
                    || (item.label || "").toLowerCase().includes(q)
                    || item.tags.some(t => t.toLowerCase().includes(q));
                if (!hit) return;

                const card = document.createElement("div");
                card.style.cssText = `border:1px solid #383838;border-radius:8px;padding:8px 10px;
                    background:#232323;cursor:pointer;transition:border-color .12s;`;
                if (item.label) {
                    const lab = document.createElement("div");
                    lab.textContent = item.label;
                    lab.style.cssText = "font-size:12px;color:#eee;margin-bottom:5px;font-weight:500;";
                    card.append(lab);
                }
                const tags = document.createElement("div");
                tags.style.cssText = "display:flex;flex-wrap:wrap;gap:4px;";
                const warnSet = new Set(item.warn || []);
                item.tags.forEach(t => {
                    const chip = document.createElement("span");
                    chip.textContent = t;
                    const isWarn = warnSet.has(t);
                    chip.style.cssText = `font-size:11px;padding:2px 6px;border-radius:4px;
                        background:${isWarn ? "#4a2b12" : "#2c2c2c"};
                        color:${isWarn ? "#f0a35e" : "#c8c8c8"};
                        border:1px solid ${isWarn ? "#8a4a18" : "#3a3a3a"};`;
                    if (!isWarn) {
                        chip.onmouseenter = () => { chip.style.background = "#3a3a3a"; chip.style.color = "#fff"; };
                        chip.onmouseleave = () => { chip.style.background = "#2c2c2c"; chip.style.color = "#c8c8c8"; };
                    }
                    chip.title = isWarn
                        ? "该标签命中阿尼玛 Skill 的「禁止输出」表（光线/质量词），加之前请确认"
                        : "点击加入";
                    chip.onclick = ev => { ev.stopPropagation(); addTag(t); };
                    tags.append(chip);
                });
                card.append(tags);
                card.onclick = () => addTag(item.tags.join(", "));
                card.onmouseenter = () => { card.style.borderColor = accent; };
                card.onmouseleave = () => { card.style.borderColor = "#383838"; };
                cards.append(card);
            });

            if (cards.children.length) {
                block.append(cards);
                list.append(block);
                nav.append(navItem);
            }
        });

        if (!list.children.length) {
            const none = document.createElement("div");
            none.textContent = "没有匹配的条目";
            none.style.cssText = "color:#777;font-size:13px;";
            list.append(none);
        }
        groupOffsetY = 0;
        void groupOffsetY;
    }

    search.oninput = () => renderGroups(search.value);
    renderGroups("");
    renderChips();
    search.focus();
    void api;
}

/* ---------- 一键随机搭配 ---------- */

function getWidgetValue(node, name, fallback = "") {
    const w = node.widgets?.find(x => x.name === name);
    return w ? w.value : fallback;
}

function reportTo(node, text) {
    const el = node.widgets?.find(w => w.name === "anima_wardrobe_report")?.element;
    if (el) el.textContent = text;
}

/* 等级 key → 中文显示名（与后端 wardrobe_rules.LEVEL_ZH 保持一致）。
   控件的「值」永远是合法 key，中文只用于报告显示。 */
const LEVEL_ZH = {
    sfw: "全年龄", suggestive: "擦边", explicit: "成人",
    hardcore: "过激", extreme: "极端", any: "不限",
};
const LEVEL_NUM = {
    sfw: 0, suggestive: 1, explicit: 2, hardcore: 3, extreme: 4, any: 4,
};
const levelText = (key) => (key in LEVEL_ZH
    ? `${LEVEL_NUM[key]} · ${LEVEL_ZH[key]}` : String(key ?? "-"));

function nsfwLine(nsfw) {
    if (!nsfw) return "";
    const cats = Object.entries(nsfw.categories || {})
        .map(([k, v]) => `${k}(${v.length})`).join("、");
    const max = nsfw.max ?? 4;
    return `NSFW 等级 : ${nsfw.num}/${max} · ${nsfw.label}`
        + (cats ? `\n成人向内容 : ${cats}` : "");
}

async function doRoll(node, section) {
    const level = getWidgetValue(node, "roll_level", "suggestive");
    const allowPublic = !!getWidgetValue(node, "allow_public", false);
    try {
        const resp = await fetch("/anima-wardrobe/roll", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                section, level, allow_public: allowPublic,
                context: getWidgetValue(node, "character_tags", ""),
            }),
        });
        const data = await resp.json();
        if (data.error) { alert("抽取失败：" + data.error); return; }

        const lines = [];
        lines.push(`【目标等级】${levelText(data.level ?? level)}`);
        // 单独抽姿势/表情时顶层就是结果；一键随机时是嵌套的 pose/expression 字段
        const pose = data.pose || (section === "pose" ? data : null);
        const expression = data.expression || (section === "expression" ? data : null);
        if (data.outfit) {
            setWidgetValue(node, "clothing_tags", data.outfit.text);
            lines.push(`【服装】形态=${data.outfit.form}`
                + `  实得等级=${levelText(data.outfit.level)}`
                + `  标签数=${data.outfit.tags.length}`);
            lines.push("  " + data.outfit.text);
        }
        if (pose) {
            setWidgetValue(node, "pose_tags", pose.text);
            lines.push(`【姿势】${pose.group || ""}`
                + (pose.label ? `／${pose.label}` : ""));
            lines.push("  " + pose.text);
        }
        if (expression) {
            setWidgetValue(node, "expression_tags", expression.text);
            lines.push(`【表情】${expression.emotion || "random"}`
                + (expression.intensity ? ` · 强度 ${expression.intensity}` : ""));
            lines.push("  " + expression.text);
        }
        if (data.scene) {
            setWidgetValue(node, "scene_tags", data.scene.text);
            lines.push(`【场景】${data.scene.place}／${data.scene.privacy}`
                + `${data.scene.indoor ? " · 室内" : " · 室外"}`
                + `  等级=${levelText(data.scene.level)}`);
            lines.push("  " + data.scene.text);
        }
        if (data.duo && data.duo.duo) {
            lines.push(`【双女性识别】是（${data.duo.count} 位）→ 关系标签：`
                + (data.relation && data.relation.length
                    ? data.relation.join(", ") : "（无）"));
        }
        const viol = data.violations || [];
        lines.push(viol.length
            ? "⚠ 校验未通过：" + viol.join("；")
            : "✓ 服装搭配 / 姿势 / 表情 / 场景 / 裸露等级 校验全部通过");
        const nl = nsfwLine(data.nsfw);
        if (nl) lines.push(nl);
        if (data.seed !== undefined) lines.push(`seed=${data.seed}`);
        reportTo(node, lines.join("\n"));
        node.graph?.setDirtyCanvas(true, true);
    } catch (e) {
        alert("抽取失败：" + e);
    }
}

async function doGirlRoll(node, count) {
    const level = getWidgetValue(node, "roll_level", "suggestive");
    const hugeBreasts = !!getWidgetValue(node, "huge_breasts", false);
    try {
        const resp = await fetch("/anima-wardrobe/girl", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                pool: "热门 TOP200", count: count, level: level,
                include_features: true, avoid_repeat: true,
                huge_breasts: hugeBreasts,
            }),
        });
        const data = await resp.json();
        if (!data.ok) { alert("抽取失败：" + (data.error || "无候选")); return; }

        setWidgetValue(node, "character_tags", data.text);
        const lines = [`【随机女性角色】候选 ${data.candidates} 个 · 抽中 ${data.count} 个`];
        if (hugeBreasts) lines.push("  ⭐ 巨乳开关：已开启，体型强制为 huge breasts");
        data.picks.forEach(p => lines.push(
            `  · ${p.name}（${p.copyright}）  热度 ${p.post_count}`
            + `  发色 ${p.hair || "-"} / 瞳色 ${p.eye || "-"}  特征 ${p.features.length} 个`));
        if (data.relation && data.relation.length) {
            lines.push("  【双女性识别】" + data.count + " 位 → 关系标签："
                + data.relation.join(", "));
        }
        const nl = nsfwLine(data.nsfw);
        if (nl) lines.push(nl);
        lines.push("  角色标签属「角色槽」—— 不占服装槽，衣服仍然照常挑");
        reportTo(node, lines.join("\n"));
        node.graph?.setDirtyCanvas(true, true);
    } catch (e) {
        alert("抽取失败：" + e);
    }
}

function installRollRows(node) {
    const mk = (defs, name) => {
        const row = document.createElement("div");
        row.style.cssText = `display:flex;align-items:center;gap:5px;width:100%;
            height:30px;box-sizing:border-box;padding:1px 0;`;
        defs.forEach(([label, color, fn, tip, flex]) => {
            const b = makeBtn(label, color, "#fff");
            b.title = tip;
            if (flex) b.style.flex = flex;
            b.onclick = fn;
            row.append(b);
        });
        node.addDOMWidget(name, "buttons", row, {
            serialize: false, hideOnZoom: false,
        });
    };

    // 第一行：服装 / 场景 / 一起
    mk([
        ["🎲 一键服装", "#0b8ce9", () => doRoll(node, "outfit"), "抽一套搭配合理的服装"],
        ["🎲 一键场景", "#7a4fd0", () => doRoll(node, "scene"), "抽一个与服装曝光度相容的场景"],
        ["🎲 一键随机搭配", "#16a37b", () => doRoll(node, "all"),
            "同时抽服装 / 姿势 / 表情 / 场景，并保证互相搭配合理"],
    ], "anima_wardrobe_roll_row");

    // 第二行：姿势 / 表情
    mk([
        ["🎲 一键姿势", "#c2570b", () => doRoll(node, "pose"),
            "按等级随机抽一个姿势（SFW 档只出日常姿势，高等级才出性行为姿势）"],
        ["🎲 一键表情", "#d6458a", () => doRoll(node, "expression"),
            "表情随当前姿势/服装自动匹配（害羞、高潮、失神…），也可纯随机"],
    ], "anima_wardrobe_pose_row");

    // 第三行：随机角色 + 清空
    mk([
        ["🎲 随机角色", "#c2570b", () => doGirlRoll(node, 1),
            "从女性角色库随机抽 1 个，连官方标签特征一起填进角色槽"],
        ["🎲 随机角色 ×2", "#8a4a18", () => doGirlRoll(node, 2),
            "一次抽 2 个女性角色；识别到双女性会自动配关系标签（SFW 正常互动 / NSFW 百合）"],
        ["清空", "#4a4a4a", () => {
            ["character_tags", "clothing_tags", "pose_tags", "expression_tags", "scene_tags"]
                .forEach(n => setWidgetValue(node, n, ""));
            reportTo(node, "（已清空 角色 / 服装 / 姿势 / 表情 / 场景）");
            node.graph?.setDirtyCanvas(true, true);
        }, "清空角色 / 服装 / 姿势 / 表情 / 场景五个槽", "0 0 54px"],
    ], "anima_wardrobe_girl_row");
}

/* ---------- 节点扩展 ---------- */

function installActionRow(node, label, accent, onOpen) {
    const row = document.createElement("div");
    row.style.cssText = `display:flex;align-items:center;gap:6px;width:100%;
        height:30px;box-sizing:border-box;padding:1px 0;`;
    const btn = makeBtn(label, accent, "#fff");
    btn.onclick = onOpen;
    row.append(btn);
    const widget = node.addDOMWidget(`anima_wardrobe_${label}`, "button", row, {
        serialize: false, hideOnZoom: false,
    });
    return widget;
}

function ensureReportArea(node, accent) {
    const el = document.createElement("div");
    el.style.cssText = `width:100%;box-sizing:border-box;padding:6px 8px;font-size:11px;
        line-height:1.5;font-family:ui-monospace,Consolas,monospace;white-space:pre-wrap;
        background:#181818;color:#aaa;border-radius:6px;max-height:150px;overflow-y:auto;`;
    el.textContent = "（运行一次后在此显示仲裁结果）";
    node.addDOMWidget("anima_wardrobe_report", "report", el, {
        serialize: false, hideOnZoom: false,
    });
    return el;
}

function hookReport(nodeType, accent) {
    const orig = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
        orig?.apply(this, arguments);
        const payload = message?.anima_wardrobe_report
            || message?.output?.anima_wardrobe_report
            || message?.ui?.anima_wardrobe_report;
        const text = Array.isArray(payload) ? payload[0] : payload;
        const el = this.widgets?.find(w => w.name === "anima_wardrobe_report")?.element;
        if (el && typeof text === "string") el.textContent = text;
    };
}

app.registerExtension({
    name: "AnimaWardrobe.Studio",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "AnimaWardrobeStudio") return;

        const ACCENT = "#2f9e8f";
        const orig = nodeType.prototype.onNodeCreated;

        nodeType.prototype.onNodeCreated = function () {
            orig?.apply(this, arguments);
            if (studioStates.has(this)) return;
            studioStates.set(this, true);

            // 两排一键按钮（服装/场景/一起 + 随机角色/清空）—— 放在最上面，因为是动作类操作
            installRollRows(this);

            const cfg = [
                ["character_tags", "角色", "#6b6b6b", "角色（可选）"],
                ["clothing_tags", "服装", "#0b8ce9", "服装"],
                ["pose_tags", "姿势", "#c2570b", "姿势"],
                ["expression_tags", "表情", "#d6458a", "表情"],
                ["scene_tags", "场景", "#7a4fd0", "场景"],
            ];

            cfg.forEach(([widgetName, label, color, title]) => {
                if (widgetName !== "character_tags") {
                    installActionRow(this, `打开阿尼玛预设 · ${label}`, color,
                        () => openPicker(this, widgetName, widgetName.replace("_tags", ""),
                                         title, color));
                } else {
                    installActionRow(this, "角色：建议用上游「Anima 角色 Tag 选择器」", "#555",
                        () => {
                            alert("角色选择的图像图鉴仍由上游的「Anima 角色 Tag 选择器」提供，"
                                + "本节点保留该槽位用于手动填写或接收上游结果。");
                        });
                }
            });

            ensureReportArea(this, ACCENT);
            const need = 120 + this.widgets.length * 8;
            this.size[1] = Math.max(this.size[1], need, 320);
        };

        hookReport(nodeType, ACCENT);
    },
});

app.registerExtension({
    name: "AnimaWardrobe.Arbiter",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        const names = ["AnimaTagArbiter", "AnimaWardrobeInspect", "AnimaWardrobeRoll",
                       "AnimaRandomGirl", "AnimaNSFWGrader"];
        if (!names.includes(nodeData.name)) return;
        ensureReportText(nodeType);
        hookReport(nodeType, "#2f9e8f");
    },
});

function ensureReportText(nodeType) {
    const orig = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        orig?.apply(this, arguments);
        if (studioStates.has(this)) return;
        studioStates.set(this, true);
        ensureReportArea(this, "#2f9e8f");
        this.size[1] = Math.max(this.size[1], 260);
    };
}
