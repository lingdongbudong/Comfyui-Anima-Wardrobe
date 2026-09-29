# -*- coding: utf-8 -*-
"""Comfyui-Anima-Wardrobe

Anima 角色造型台（服装 / 姿势 / 场景）+ 跨插件标签仲裁器。
零第三方依赖，纯 Python + 原生前端 JS。
"""
from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, presets_payload

WEB_DIRECTORY = "./js"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]


def _register_routes():
    """把预设与槽位契约暴露给前端面板。"""
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception as exc:  # pragma: no cover
        print(f"[AnimaWardrobe] 无法注册 HTTP 路由: {exc}")
        return

    routes = PromptServer.instance.routes

    @routes.get("/anima-wardrobe/presets")
    async def _presets(request):
        return web.json_response(presets_payload())

    @routes.get("/anima-wardrobe/contract")
    async def _contract(request):
        from .nodes import SLOT_META, SLOT_ORDER, TIER_RANK
        return web.json_response({
            "slot_order": SLOT_ORDER,
            "slots": SLOT_META,
            "tiers": TIER_RANK,
        })

    @routes.post("/anima-wardrobe/roll")
    async def _roll(request):
        """一键随机搭配。前端按钮调用，与后端节点共用同一套搭配引擎。"""
        from . import wardrobe_rules as wr
        try:
            body = await request.json()
        except Exception:
            body = {}
        section = body.get("section", "all")
        seed = body.get("seed", None)
        allow_public = bool(body.get("allow_public", False))
        place_hint = body.get("place_hint") or None
        duo_context = str(body.get("context", "") or "")

        if section not in wr.ROLL_SECTIONS:
            return web.json_response({"error": f"section 必须是 {wr.ROLL_SECTIONS}"},
                                     status=400)
        if seed is not None:
            try:
                seed = int(seed)
            except (TypeError, ValueError):
                seed = None
        # ⭐ 先归一、再校验：任何写法（中文标签 / 数字 / 旧 key / 大小写）都先
        #    经过 resolve_level 收敛到合法 key；收敛结果必然 ∈ LEVELS，
        #    所以这里只剩「默认值兜底」一条路，不会再因为前端值写法而 400。
        level = wr.resolve_level(body.get("level"), "suggestive")
        if level not in wr.LEVELS:                      # 理论上不可达，保留兜底
            return web.json_response(
                {"error": f"level 必须是 {wr.LEVELS} 之一（收到 {body.get('level')!r}）"},
                status=400)
        result = wr.roll(section=section, level=level, seed=seed,
                         allow_public=allow_public, place_hint=place_hint,
                         duo_context=duo_context)
        # 回显归一后的等级，方便前端用同一套词汇显示
        result["level"] = level
        result["level_label"] = wr.level_label(level)
        return web.json_response(result)

    @routes.get("/anima-wardrobe/roll-options")
    async def _roll_options(request):
        from . import wardrobe_rules as wr
        pools = wr.clothing_pool()
        sc = wr.scene_pool()
        return web.json_response({
            "levels": list(wr.LEVELS),                 # 合法值 = 控件值
            "level_labels": wr.LEVEL_ZH,               # 中文显示名
            "level_choices_legacy": list(wr.LEVEL_CHOICES),
            "level_alias": wr.LEVEL_ALIAS,
            "target_num": wr.TARGET_NUM,
            "num_of": wr.NUM_OF,
            "nsfw_max": wr.NSFW_MAX,
            "levels_internal": list(wr.LEVELS),
            "sections": list(wr.ROLL_SECTIONS),
            "slots": {k: len(v) for k, v in pools.items()},
            "extreme_slots": {s: len(pools.get(s, [])) for s in wr.EXTREME_SLOTS},
            "extreme_details": sorted(wr.EXTREME_DETAILS),
            "places": [{"label": p["label"], "privacy": p["privacy"],
                        "indoor": p["indoor"]} for p in sc["places"]],
            "privacy_allows": {k: sorted(v) for k, v in wr.PRIVACY_ALLOWS.items()},
            "privacy_max_num": wr.PRIVACY_MAX_NUM,
            "slot_limits": wr.SLOT_LIMITS,
            "slot_labels": wr.SLOT_LABEL,
        })

    @routes.post("/anima-wardrobe/girl")
    async def _girl(request):
        """随机女性角色：抽角色 + 官方标签特征。"""
        from . import wardrobe_rules as wr
        try:
            body = await request.json()
        except Exception:
            body = {}
        pool = body.get("pool", "热门 TOP200")
        if pool not in wr.CHAR_POOLS:
            return web.json_response({"error": f"pool 必须是 {wr.CHAR_POOLS}"},
                                     status=400)
        seed = body.get("seed", None)
        try:
            seed = int(seed) if seed is not None and int(seed) >= 0 else None
        except (TypeError, ValueError):
            seed = None
        result = wr.roll_girl(
            pool=pool,
            keyword=str(body.get("keyword", "") or ""),
            min_post=int(body.get("min_post", 0) or 0),
            count=int(body.get("count", 1) or 1),
            include_features=bool(body.get("include_features", True)),
            seed=seed,
            avoid_repeat=bool(body.get("avoid_repeat", True)),
            feature_limit=int(body.get("feature_limit", 0) or 0),
            huge_breasts=bool(body.get("huge_breasts", False)),
        )
        # 双女性 → 按等级补关系标签（SFW 正常互动 / NSFW 百合；第 5 级为极端百合）
        relation = []
        if result.get("ok") and result.get("count", 0) >= 2:
            mode = wr.resolve_level(body.get("level"), "suggestive")
            relation = wr.duo_relation_for_level(mode)
            result["relation"] = relation
            result["level"] = mode
            result["level_label"] = wr.level_label(mode)
            result["text"] = result["text"] + (", " + ", ".join(relation) if relation else "")
            result["nsfw"] = wr.grade_nsfw(
                [t for p in result["picks"] for t in p["tags_all"]] + relation)
        return web.json_response(result)

    @routes.get("/anima-wardrobe/character-options")
    async def _char_options(request):
        from . import wardrobe_rules as wr
        chars = wr.character_pool()
        hairs, eyes, works = {}, {}, {}
        for c in chars:
            if c.get("h"):
                hairs[c["h"]] = hairs.get(c["h"], 0) + 1
            if c.get("e"):
                eyes[c["e"]] = eyes.get(c["e"], 0) + 1
            if c.get("c"):
                works[c["c"]] = works.get(c["c"], 0) + 1
        top = lambda d, n=60: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:n]]
        return web.json_response({
            "pools": list(wr.CHAR_POOLS),
            "total": len(chars),
            "top_works": top(works),
            "hair_colors": top(hairs, 30),
            "eye_colors": top(eyes, 30),
            "examples": [{"name": c["n"], "copyright": c["c"], "post_count": c.get("p", 0)}
                         for c in chars[:40]],
        })

    @routes.post("/anima-wardrobe/nsfw")
    async def _nsfw(request):
        """NSFW 分级判定：返回 0-4 级 + 内容分类明细（第 5 级为新增的 extreme）。"""
        from . import wardrobe_rules as wr
        try:
            body = await request.json()
        except Exception:
            body = {}
        text = str(body.get("text", "") or "")
        rep = wr.grade_nsfw([t.strip() for t in text.split(",") if t.strip()])
        return web.json_response(rep)

    @routes.get("/anima-wardrobe/nsfw-scale")
    async def _nsfw_scale(request):
        from . import wardrobe_rules as wr
        return web.json_response({
            "max": wr.NSFW_MAX,
            "levels": list(wr.LEVELS),
            "scale": [{"num": n, "key": k, "label": l, "desc": d}
                      for n, k, l, d in wr.NSFW_SCALE],
            "categories": {c: {"label": s["label"], "level": s["level"],
                               "size": len(s["tags"])}
                           for c, s in wr.NSFW_TABLES.items()},
            "category_order": list(wr.CATEGORY_ORDER),
            "hardcore_categories": list(wr.HARDCORE_CATEGORIES),
            "extreme_categories": list(wr.EXTREME_CATEGORIES),
            "extreme_pack": {slot: {label: tags for label, tags in groups}
                             for slot, groups in wr.EXTREME_GROUPS.items()},
        })

    print("[AnimaWardrobe] 路由已注册: presets / contract / roll / roll-options "
          "/ girl / character-options / nsfw / nsfw-scale")


try:
    _register_routes()
except Exception as exc:  # pragma: no cover
    print(f"[AnimaWardrobe] 路由注册失败（不影响节点使用）: {exc}")
