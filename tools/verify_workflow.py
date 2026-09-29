# -*- coding: utf-8 -*-
"""工作流控件值校验器 —— 不需要启动 ComfyUI。

    python tools/verify_workflow.py "path/to/workflow.json"

为什么需要它：
    ComfyUI 提交 prompt 时会校验每个 combo 控件的值是否在节点声明的候选表里，
    不在就报「Value not in list / 参数校验未通过」。手写或脚本注入工作流时，
    很容易把值写成中文标签或旧等级名，重启前端才发现 —— 这个脚本提前拦住。

校验内容：
    1. 插件节点的每个 combo 控件值 ∈ 节点声明的候选表（核心）
    2. 每个节点的 required 控件数量与 widgets_values 数量是否对得上
    3. 链路完整性（links 双向登记、slot 越界、悬空链）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nodes  # noqa: E402

WIDGET_KINDS = ("STRING", "INT", "FLOAT", "BOOLEAN")
# 致命问题（会让 ComfyUI 拒绝提交）
PROBLEMS = []
# 无害残留（例如删节点后留在 outputs 里的旧链 id，前端加载时会自动丢弃）
WARNINGS = []


def widget_specs(cls):
    """按 ComfyUI 的顺序展开该节点的控件：required 先，optional 后（去掉 forceInput）。"""
    it = cls.INPUT_TYPES()
    out = []
    for kind in ("required", "optional"):
        for key, spec in (it.get(kind) or {}).items():
            t = spec[0]
            opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if opts.get("forceInput"):
                continue
            is_combo = isinstance(t, (list, tuple))
            if is_combo or (isinstance(t, str) and t in WIDGET_KINDS):
                out.append({"key": key, "combo": is_combo,
                            "values": list(t) if is_combo else None,
                            "kind": kind})
    return out


def strip_dangling_output_links(d):
    """清掉 outputs 里指向不存在链的残留 id。返回清理数量。"""
    alive = {l[0] for l in d.get("links", [])}
    n = 0
    for node in d.get("nodes", []):
        for o in node.get("outputs") or []:
            links = o.get("links")
            if not links:
                continue
            keep = [lid for lid in links if lid in alive]
            if len(keep) != len(links):
                n += len(links) - len(keep)
                o["links"] = keep
    return n


def check(path, fix=False):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if fix:
        k = strip_dangling_output_links(d)
        if k:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            print(f"已清理 {k} 条悬空链引用并回写")
    by_type = {}
    for name, cls in nodes.NODE_CLASS_MAPPINGS.items():
        by_type[name] = cls

    n_checked = n_combo = 0
    for n in d.get("nodes", []):
        cls = by_type.get(n.get("type"))
        if cls is None:
            continue
        specs = widget_specs(cls)
        wv = n.get("widgets_values")
        if wv is None:
            PROBLEMS.append(f"节点 {n['id']}（{n['type']}）没有 widgets_values")
            continue
        if isinstance(wv, dict):                 # 兼容 named 形式
            wv = [wv.get(s["key"]) for s in specs]
        if len(wv) != len(specs):
            PROBLEMS.append(f"节点 {n['id']}（{n['type']}）控件数量不符："
                            f"声明 {len(specs)} 个 {[s['key'] for s in specs]}，"
                            f"实际 {len(wv)} 个")
            continue
        n_checked += 1
        for s, v in zip(specs, wv):
            if not s["combo"]:
                continue
            n_combo += 1
            if v not in s["values"]:
                PROBLEMS.append(
                    f"节点 {n['id']}（{n['type']}）控件 {s['key']} 的值 {v!r} "
                    f"不在候选表 {s['values']} 内 —— ComfyUI 提交时会报参数校验失败")

    # 链路完整性
    node_ids = {n["id"] for n in d.get("nodes", [])}
    links = {}
    for l in d.get("links", []):
        links[l[0]] = l
        if l[1] not in node_ids:
            PROBLEMS.append(f"链 {l[0]} 起点节点 {l[1]} 不存在")
        if l[3] not in node_ids:
            PROBLEMS.append(f"链 {l[0]} 终点节点 {l[3]} 不存在")
    for n in d.get("nodes", []):
        for o in n.get("outputs") or []:
            for lid in (o.get("links") or []):
                if lid is not None and lid not in links:
                    WARNINGS.append(f"节点 {n['id']}（{n.get('type')}）的输出残留了"
                                    f"已不存在的链 {lid}（无害，可用 --fix 清掉）")
        for i in n.get("inputs") or []:
            lid = i.get("link")
            if lid is not None and lid not in links:
                PROBLEMS.append(f"节点 {n['id']} 的输入引用了不存在的链 {lid}")

    print(f"文件: {path}")
    print(f"节点 {len(d.get('nodes', []))} 个 / 链路 {len(d.get('links', []))} 条"
          f" / 插件节点 {n_checked} 个 / 校验 combo 值 {n_combo} 处")
    for w in WARNINGS:
        print("  ! ", w)
    if PROBLEMS:
        print(f"\n发现 {len(PROBLEMS)} 个致命问题：")
        for p in PROBLEMS:
            print("  ✗", p)
        return 1
    print("\n✓ 全部通过：控件值合法、控件数量匹配、链路完整"
          + (f"（另有 {len(WARNINGS)} 条无害残留）" if WARNINGS else ""))
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--fix"]
    if not args:
        sys.exit("用法: python tools/verify_workflow.py <workflow.json> [--fix]")
    sys.exit(check(args[0], fix="--fix" in sys.argv))
