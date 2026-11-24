# Comfyui-Anima-Wardrobe

<p align="right">
  <a href="README.md">中文</a> ｜ <b>English</b>
</p>

> [!CAUTION]
> **⛔ Commercial use prohibited · No repackaging or redistribution** — Copyright © 2026 灵冻不冻 (lingdongbudong). All rights reserved.
> This repository is licensed under the **[PolyForm Strict License 1.0.0](LICENSE)** as of **2026-11-24**.
>
> - **❌ No commercial use** — you may not use this plugin (source, prompt pools, scene / character / outfit libraries, presets, or any derivative) for any commercial purpose: paid distribution, bundling, paid generation services, commercial courses, or as the technical base of a commercial service. Written permission is required.
> - **❌ No repackaging or redistribution** — do not repackage, rename, wrap, re-upload, or include this plugin in third-party bundles, mirror archives, net-disk collections or resource sites, **modified or not**.
> - **❌ Do not remove or alter** this notice, the author attribution, or the source link.
> - **✅ Permitted** — personal non-commercial use (research, study, hobby, generating your own images) and modifying the source for your own use; quoting screenshots / recordings for non-commercial sharing with full attribution and this notice intact.
>
> **Commercial / derivative-work / repost licensing** → [Bilibili DM](https://space.bilibili.com/516891894) for written permission.
> **Legacy versions** published before **2026-11-24** remain under the license they were published with (MIT); that grant is not withdrawn.
> Full terms: **[NOTICE.md](NOTICE.md)**.

---

## What it is

An Anima **character styling console** (outfit / pose / expression / scene slots) plus a
**cross-plugin tag arbiter**. Zero third-party dependencies — plain Python and vanilla JS.

The problem it solves: several ComfyUI plugins can all write into the same prompt string, and
a `STRING` connection carries no metadata — so downstream code cannot tell whether
`black leotard` was picked by the user or appended by some node. The result is either silent
overwriting or over-protection.

This plugin makes it deterministic with a **slot contract + declaration sequence number**:

| Slot | Policy |
| --- | --- |
| Artist / **outfit** / pose / scene | **sticky** — once written it is locked; later writers register but do not write |
| Person count & gender / character identity / **character traits** / free tags | replaceable — later writes win |

## Nodes

| Node | Purpose |
| --- | --- |
| `AnimaWardrobeStudio` | the styling console: outfit / pose / expression / scene slots + one-click buttons |
| `AnimaRandomGirl` | draws 1–3 female characters from a 5,981-entry pool, weighted by popularity |
| `AnimaNSFWGrader` | five-tier content grading (0–4) with optional over-level tag removal |
| `AnimaWardrobeRoll` | server-side one-click outfit + pose + expression + scene, seeded |
| `AnimaTagArbiter` | arbitrates up to 4 text sources by contract — put it at the end of the chain |
| `AnimaWardrobeInspect` | read-only: prints the slot verdict for every tag, for debugging |

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lingdongbudong/Comfyui-Anima-Wardrobe.git
```

Restart ComfyUI. No dependencies to install.

## Language

Full documentation is in Chinese: **[README.md](README.md)**.

## License

**[PolyForm Strict License 1.0.0](LICENSE)** © 2026 灵冻不冻 (lingdongbudong).

- ❌ **No commercial use** without prior written permission.
- ❌ **No repackaging or redistribution**, modified or not.
- ✅ Personal non-commercial use, and modifying the source for your own use.
- Legacy versions published before 2026-11-24 remain under their original license (MIT);
  that grant is not withdrawn.

Commercial / derivative licensing: contact the author via [Bilibili DM](https://space.bilibili.com/516891894).
Full terms: [NOTICE.md](NOTICE.md) · Chinese documentation: [README.md](README.md).
