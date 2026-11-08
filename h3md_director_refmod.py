"""H3MD Director + RefMod - adds saved RefMods (ComfyUI-MiniMaxH3Mod .safetensors) as a 4th reference kind.

New nodes only; h3md_director.py / h3md_loop*.py and every existing node are imported, never modified:
  H3MDDirectorRefMod       "H3MD Director + RefMod"      plan_json (+ media.refmods / segment.refmods) -> H3MD_PLAN
  H3MDLoopSamplerRefMod    "H3MD Loop Sampler + RefMod"  H3MD Loop Sampler (H3MDLoopSampler2) that encodes each segment
                                                         with H3MDSegmentEncodeRefMod (same inputs / outputs / cache)
  H3MDSegmentEncodeRefMod  "H3MD Segment Encode + RefMod" (manual chains)

plan_json additions (everything else is plan v1 exactly as H3MD Director reads it):
  "media":    {"refmods": [{"id": "eve", "file": "character/eve"}]}
              file = RefMod name as the RefMod Loader lists it (models/refmods roots incl. extra_model_paths), or a
              path ending in .safetensors (absolute, or relative to ComfyUI's input folder, e.g. uploads in input/refmods)
  "segments": [{..., "refmods": ["eve"] | [{"id": "eve"}]}]  (old strength / copies keys are ignored)

Encoding (per segment, mirrors ComfyUI-MiniMaxH3Mod 517e8c4 prompt.py MiniMaxH3RefModTextEncode + nodes.py Apply):
  * block = H3RefMod.ref_block() from the plugin itself (used as is, full strength),
    block["refmod"] = True; DiT: added to minimax_refs once, by default AFTER the segment's own images /
    videos / audios (Apply-style append; see Position); the negative is zero_out(positive) as in H3MD Segment Encode
    (keeps refs).
    Position (2026-10-24): segment.refmods[].at = n (int >= 0, picture / video receiver only; the editor sets it when
    a RefMod is dragged from the shot card's RefMod row onto a picture / video) inserts the RefMod
    before the shot's own picture / video n+1 instead (block and its text-encoder item at that place, so the
    labels follow the position and the shot's later own pictures / videos are renumbered); no `at` or at >= the
    shot's own count = after them, exactly as before.  Audio RefMods ignore `at`.
  * text encoder (always on; the refmod_to_text_encoder toggle was removed 2026-10-24): the block latent is
    VAE-decoded (image: first frame, video: 2 fps at the fixed 24 fps playback rate, refmod_reference_fps is a
    read-only display) and added to the core tokenizer's minimax_ref_items at the same position as its block, ONCE
    per RefMod, so Qwen sees it under the matching <Picture k> / <Video k> / <Audio k> (numbered by that position).
  * receiver (2026-10-24): segment.refmods[].slot = "image" | "video" (set when the RefMod is dropped on the shot's
    images / videos receiver) presents a visual RefMod as that kind (text-encoder item type + DiT block kind) under
    a <Picture k> / <Video k> (numbered by position, see above); no slot = the RefMod's own kind (as before).
    A shape that does not fit (e.g. a video RefMod in the images receiver) is left to H3's own checks.
    Audio RefMods ignore slot.
  * prompt: "RefMod j" / "<RefMod j>" (j = position in the shot's refmods list = the editor's RefMod row
    order) is rewritten to that core label, e.g. shot with 1 real video + a video-kind RefMod without `at`:
    "RefMod 1" -> "<Video 2>".  The plan_json text itself is
    never changed (the rewrite is applied to the parsed plan and again at encode).
"""

from __future__ import annotations

import copy
import inspect
import json
import logging
import math
import os
import re
import sys
from collections import OrderedDict

try:
    from . import h3md_director as D
    from . import h3md_loop as L
    from . import h3md_loop_split as LS
    from . import h3md_cache as C
except ImportError:  # flat import (tests)
    import h3md_director as D
    import h3md_loop as L
    import h3md_loop_split as LS
    import h3md_cache as C

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD RefMod]"
PLAN_TYPE = "H3MD_PLAN"
REFERENCE_FPS = 24.0   # fixed H3 playback fps of decoded video RefMods (refmod_reference_fps is read-only)
_META_KEYS = ("refmod_meta", "audio_refmod_meta")
_LABEL = {"image": "Picture", "video": "Video", "audio": "Audio"}
# "<RefMod 2>", "RefMod 2", "refmod2" (not inside a longer ASCII word, so "参考RefMod 1" works)
REFMOD_RE = re.compile(r"<\s*RefMod\s*(\d+)\s*>|(?<![A-Za-z0-9_])RefMod\s*(\d+)(?![0-9])", re.IGNORECASE)


# ----------------------------------------------------------------------------
# RefMod plugin access (ComfyUI-MiniMaxH3Mod loads after ComfyUI-H3MD: always looked up lazily)
# ----------------------------------------------------------------------------

def plugin_nodes():
    """The plugin's nodes module (has H3RefMod, _find_mod_path), or None. _list_mod_names / _find_mod_path are
    private helpers of that plugin: every use falls back to our own scan of models/refmods if they are missing or fail."""
    try:
        import nodes as comfy_nodes
        cls = comfy_nodes.NODE_CLASS_MAPPINGS.get("MiniMaxH3RefModsLoader")
        mod = sys.modules.get(cls.__module__) if cls is not None else None
        if mod is not None and hasattr(mod, "H3RefMod") and hasattr(mod, "_find_mod_path"):
            return mod
    except Exception:
        pass
    for mod in list(sys.modules.values()):
        f = str(getattr(mod, "__file__", "") or "")
        if "MiniMaxH3Mod" in f and f.endswith("nodes.py") and hasattr(mod, "H3RefMod") and hasattr(mod, "_find_mod_path"):
            return mod
    return None


def _need_plugin():
    pn = plugin_nodes()
    if pn is None:
        raise RuntimeError(f"{_TAG} the RefMod plugin (custom_nodes/ComfyUI-MiniMaxH3Mod) is not loaded; "
                           "H3MD Director + RefMod uses its H3RefMod loader / ref_block")
    return pn


def _refmod_roots() -> list:
    try:
        import folder_paths
        return list(folder_paths.get_folder_paths("refmods"))
    except Exception:
        return []


def resolve_refmod_file(file: str) -> str:
    """plan file -> path WITHOUT .safetensors (what the plugin's H3RefMod.load takes)."""
    f = str(file or "").strip()
    if not f:
        raise ValueError(f"{_TAG} empty RefMod file")
    if f.lower().endswith(".safetensors"):
        if os.path.isabs(f):
            if not (D.PATHS.may_read(f) or D.PATHS.inside(f, _refmod_roots())):   # v0.3.7: allowed folders only
                raise PermissionError(f"{_TAG} RefMod file '{f}' is outside models/refmods and ComfyUI's input / "
                                      "output / temp folders")
            if os.path.isfile(f):
                return f[: -len(".safetensors")]
            raise FileNotFoundError(f"{_TAG} RefMod file not found: {f}")
        return D.resolve_media_path(f)[: -len(".safetensors")]   # relative: ComfyUI input folder
    name = f.replace("\\", "/")
    if os.path.isabs(name) or ".." in name.split("/"):             # v0.3.7: a name stays inside models/refmods
        raise PermissionError(f"{_TAG} RefMod name '{name}' must be a name inside models/refmods")
    pn = plugin_nodes()
    find = getattr(pn, "_find_mod_path", None) if pn is not None else None
    if callable(find):
        try:
            return find(name)
        except Exception as exc:                                   # plugin renamed / changed its private helper
            LOG.warning("%s RefMod plugin lookup failed for '%s' (%s); scanning models/refmods instead", _TAG, name, exc)
    for root in _refmod_roots():                                   # own lookup, first root wins
        p = D.PATHS.under(root, os.path.join(*name.split("/")))
        if p and os.path.isfile(p + ".safetensors"):
            return os.path.abspath(p)
    raise FileNotFoundError(f"{_TAG} RefMod '{name}' not found in models/refmods ({_refmod_roots()})")


def read_header(path_no_ext: str) -> dict:
    """RefMod metadata + latent shape from the safetensors header (no tensor data is read)."""
    from safetensors import safe_open
    p = path_no_ext + ".safetensors"
    with safe_open(p, framework="pt") as f:
        meta_raw = f.metadata() or {}
        keys = list(f.keys())
        shape = list(f.get_slice("latent").get_shape()) if "latent" in keys else None
    meta = None
    for k in _META_KEYS:
        if k in meta_raw:
            meta = json.loads(meta_raw[k])
            break
    if meta is None and os.path.isfile(path_no_ext + ".json"):
        with open(path_no_ext + ".json", encoding="utf-8") as fh:
            meta = json.load(fh)
    if not isinstance(meta, dict):
        raise ValueError(f"{_TAG} {p} has no RefMod metadata (not a RefMod file?)")
    if meta.get("kind") == "bundle":
        raise ValueError(f"{_TAG} {p} is a RefMod bundle; H3MD Director + RefMod takes single RefMods "
                         "(load bundles with the plugin's Load H3 RefMods node)")
    if shape is None:
        raise ValueError(f"{_TAG} {p} has no 'latent' tensor")
    kind = "audio" if meta.get("kind") == "audio" else str(meta.get("kind") or "image")
    if kind not in _LABEL:
        raise ValueError(f"{_TAG} {p}: unknown RefMod kind {kind!r}")
    if kind == "audio":
        t, hh, ww = int(shape[-1]), 0, 0
        tokens = 2 * t
    else:
        t = 1 if kind == "image" else int(meta.get("latent_t", shape[2]))
        hh, ww = int(meta.get("latent_h", shape[3])), int(meta.get("latent_w", shape[4]))
        tokens = t * (hh // 2) * (ww // 2)                         # = H3RefMod.token_count
    st = os.stat(p)
    return {"name": str(meta.get("name") or os.path.basename(path_no_ext)), "kind": kind, "latent_t": t,
            "latent_h": hh, "latent_w": ww, "shape": shape, "tokens": tokens,
            "concept_type": str(meta.get("concept_type") or ""), "mode": str(meta.get("mode") or ""),
            "size": st.st_size, "mtime_ns": st.st_mtime_ns, "path": p}


def list_refmods() -> list:
    """Editor list: plugin names (all refmods roots) + .safetensors under input/refmods (uploads)."""
    out, seen = [], set()
    pn = plugin_nodes()
    names = []
    lister = getattr(pn, "_list_mod_names", None) if pn is not None else None
    if callable(lister):
        try:
            names = list(lister())
        except Exception as exc:                                   # plugin renamed / changed its private helper
            LOG.warning("%s plugin RefMod list failed (%s); scanning models/refmods instead", _TAG, exc)
    if not names:                                                  # no plugin helper, or it gave nothing: own scan
        for root in _refmod_roots():
            for dp, dn, fn in os.walk(root):
                dn[:] = [d for d in dn if d not in ("graph_presets", ".git", "__pycache__")]
                for f in fn:
                    if f.endswith(".safetensors"):
                        names.append(os.path.relpath(os.path.join(dp, f), root)[:-12].replace("\\", "/"))
    up = os.path.join(D._input_dir(), "refmods")
    if os.path.isdir(up):
        for dp, dn, fn in os.walk(up):
            for f in sorted(fn):
                if f.lower().endswith(".safetensors"):
                    names.append(os.path.relpath(os.path.join(dp, f), D._input_dir()).replace("\\", "/"))
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        try:
            h = read_header(resolve_refmod_file(name))
            out.append({"file": name, **{k: h[k] for k in ("name", "kind", "latent_t", "latent_h", "latent_w",
                                                           "tokens", "concept_type", "size", "mtime_ns", "path")}})
        except Exception:
            continue   # bundles / non-RefMod safetensors are not offered
    return out


# ----------------------------------------------------------------------------
# plan parsing
# ----------------------------------------------------------------------------

def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def rewrite_prompt(prompt: str, tags: list) -> tuple:
    """'RefMod j' / '<RefMod j>' -> tags[j-1] (a core label such as '<Video 2>'; None = keep plain 'RefMod j').
    Returns (new prompt, sorted list of unknown j)."""
    unknown = set()

    def sub(m):
        j = int(m.group(1) or m.group(2))
        if 1 <= j <= len(tags):
            return tags[j - 1] if tags[j - 1] else f"RefMod {j}"
        unknown.add(j)
        return m.group(0)

    return REFMOD_RE.sub(sub, prompt or ""), sorted(unknown)


def refmod_slot(e: dict, kind: str) -> str:
    """Receiver of a shot RefMod: "image" / "video" when it was dropped on the shot's images / videos receiver,
    otherwise its own kind.  Audio RefMods always stay audio."""
    slot = str(e.get("slot") or "")
    return slot if kind != "audio" and slot in ("image", "video") else kind


def refmod_tokens_by_shot(plan_json) -> dict:
    """H3MD autosplit: DiT tokens of each raw shot's RefMods {raw shot index: tokens} (header read only).
    Unreadable files count 0 (parse_refmods reports them)."""
    try:
        raw = json.loads(plan_json) if isinstance(plan_json, str) else (plan_json or {})
        lib = {}
        for item in ((raw.get("media") or {}).get("refmods") or []):
            if isinstance(item, dict) and item.get("id") and item.get("file"):
                try:
                    lib[str(item["id"])] = int(read_header(resolve_refmod_file(item["file"])).get("tokens") or 0)
                except Exception:
                    lib[str(item["id"])] = 0
        out = {}
        for i, s in enumerate(raw.get("segments") or []):
            if isinstance(s, dict):
                out[i] = sum(lib.get(str(e.get("id")) if isinstance(e, dict) else str(e), 0)
                             for e in s.get("refmods") or [])
        return out
    except Exception:
        return {}


def used_refmod_ids(raw) -> set:
    """RefMod ids referenced by some shot (segment.refmods); only these are resolved / read at run time."""
    used = set()
    for s in ((raw.get("segments") or []) if isinstance(raw, dict) else []):
        if isinstance(s, dict):
            for e in s.get("refmods") or []:
                used.add(str(e.get("id")) if isinstance(e, dict) else str(e))
    return used


def parse_refmods(raw: dict, plan: dict, options: dict) -> list:
    """Attach the plan's RefMods to the parsed H3MD plan in place.  Returns notes."""
    notes = []
    media_raw = (raw.get("media") or {}).get("refmods") or []
    lib = {}
    used, seen = used_refmod_ids(raw), set()
    if plan.get("windowMode"):   # YJ 11-07: RefMods are switched off by window mode (identity from the pictures)
        plan["media"]["refmods"] = {}
        if used:
            notes.append(f"window mode: RefMods off ({', '.join(sorted(used))}); identity comes from the reference "
                         f"pictures (RefMods do not go through the window-mode encode) - take the 'RefMod N' "
                         f"mentions out of the prompts, they are plain text now")
        return notes
    for item in media_raw:
        if not isinstance(item, dict) or not item.get("id") or not item.get("file"):
            raise ValueError("H3MD Director + RefMod: every media.refmods entry needs 'id' and 'file'")
        rid = str(item["id"])
        if rid in seen:
            raise ValueError(f"H3MD Director + RefMod: duplicate media id '{rid}' in refmods")
        seen.add(rid)
        if rid not in used:
            continue   # 2026-10-24: library RefMod no shot uses = ignored like other media (not resolved / read)
        path = resolve_refmod_file(item["file"])
        info = read_header(path)
        lib[rid] = {"id": rid, "file": str(item["file"]), "path_no_ext": path, **info}
    plan["media"]["refmods"] = lib
    segs_raw = raw.get("segments") or []
    for seg in plan["segments"]:
        ri = int(seg.get("shotIndex", seg["index"]))   # H3MD autosplit: a part takes its shot's RefMods
        s = segs_raw[ri] if ri < len(segs_raw) and isinstance(segs_raw[ri], dict) else {}
        n = seg["index"] + 1
        own = {k: [x for x in seg["labels"] if x["kind"] == k] for k in ("image", "video")}
        counters = {"audio": sum(1 for x in seg["labels"] if x["kind"] in ("audio", "video_soundtrack"))}
        entries = []
        for j, e in enumerate(s.get("refmods") or [], 1):
            e = {"id": e} if isinstance(e, str) else dict(e or {})
            rid = str(e.get("id"))
            if rid not in lib:
                raise ValueError(f"H3MD Director + RefMod: segment {n} references unknown RefMod '{rid}'")
            info = lib[rid]
            slot = refmod_slot(e, info["kind"])
            at = None   # position among the slot's own pictures / videos; None = after them (old behaviour)
            if slot in own and e.get("at") is not None:
                a = _num(e.get("at"), None)
                if a is None or not math.isfinite(a) or a < 0 or a != int(a):
                    raise ValueError(f"H3MD Director + RefMod: segment {n} RefMod '{rid}' at must be an integer >= 0")
                at = int(a) if int(a) < len(own[slot]) else None
            tag = None
            if slot == "audio":
                counters[slot] += 1
                tag = f"<{_LABEL[slot]} {counters[slot]}>"
            entries.append({"j": j, "id": rid, "file": info["file"], "path_no_ext": info["path_no_ext"],
                            "kind": info["kind"], "slot": slot,
                            "tokens": info["tokens"], "tag": tag, "alias": f"<RefMod {j}>", "at": at})
        # picture / video numbering by position: own refs in order, a RefMod with at = p right before own ref p+1,
        # RefMods without `at` after all own refs (= the old numbering when no RefMod has `at`)
        retag, active = {}, list(entries)
        for slot in ("image", "video"):
            num = 0
            for p in range(len(own[slot])):
                for e in active:
                    if e["slot"] == slot and e["at"] == p:
                        num += 1
                        e["tag"] = f"<{_LABEL[slot]} {num}>"
                num += 1
                if own[slot][p]["tag"] != f"<{_LABEL[slot]} {num}>":
                    retag[id(own[slot][p])] = f"<{_LABEL[slot]} {num}>"
            for e in active:
                if e["slot"] == slot and e["at"] is None:
                    num += 1
                    e["tag"] = f"<{_LABEL[slot]} {num}>"
            counters[slot] = num
        seg["refmods"] = entries
        rm_label = lambda e: {"tag": e["tag"], "kind": "refmod_" + e["slot"], "id": e["id"], "file": e["file"],
                              "alias": e["alias"]}
        labels, seen = [], {"image": 0, "video": 0}
        for lab in seg["labels"]:
            if lab["kind"] in seen:   # positioned RefMods are listed right before the own ref they precede
                labels.extend(rm_label(e) for e in active if e["slot"] == lab["kind"] and e["at"] == seen[lab["kind"]])
                seen[lab["kind"]] += 1
            labels.append(dict(lab, tag=retag[id(lab)]) if id(lab) in retag else lab)
        labels.extend(rm_label(e) for e in entries if e["tag"] and e["at"] is None)
        seg["labels"] = labels
        new, unknown = rewrite_prompt(seg["prompt"], [e["tag"] for e in entries])
        for j in unknown:
            notes.append(f"WARNING segment {n}: prompt mentions RefMod {j} but this shot has {len(entries)} RefMod(s)")
        seg["prompt"] = new
        if counters["video"] > D.MAX_VIDEOS:
            notes.append(f"segment {n}: {counters['video']} video references incl. RefMods (official max {D.MAX_VIDEOS})")
    plan["refmod"] = {"to_text_encoder": True, "reference_fps": REFERENCE_FPS}   # same dict as before: RefMod cache keys unchanged
    return notes


def refmod_summary(plan: dict) -> str:
    lines = []
    for seg in plan["segments"]:
        for e in seg.get("refmods") or []:
            slot = e.get("slot", e["kind"])
            lines.append(f"[{seg['index']}] RefMod {e['j']} '{e['id']}' ({e['kind']}{'' if slot == e['kind'] else ' as ' + slot}) "
                         f"-> {e['tag']}")
    return "\n".join(lines)


def plan_has_refmods(plan) -> bool:
    return isinstance(plan, dict) and any(s.get("refmods") for s in plan.get("segments", []))


def refmod_cache_sig(plan: dict) -> list:
    out = [plan.get("refmod")]
    for seg in plan["segments"]:
        rows = []
        for e in seg.get("refmods") or []:
            try:
                st = os.stat(e["path_no_ext"] + ".safetensors")
                stat = [st.st_size, st.st_mtime_ns]
            except OSError:
                stat = [None, None]
            slot = e.get("slot", e["kind"])
            rows.append([e["id"], e["file"], stat] + ([slot] if slot != e["kind"] else [])
                        + (["at", e["at"]] if e.get("at") is not None else []))
        out.append(rows)
    return out


# ----------------------------------------------------------------------------
# encode
# ----------------------------------------------------------------------------

_MOD_CACHE = OrderedDict()    # (path, size, mtime) -> H3RefMod
_ITEM_CACHE = OrderedDict()   # (path, size, mtime, fps, slot) -> Qwen item (CPU)


def _lru_put(cache, key, value, cap):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > cap:
        cache.popitem(last=False)


def load_refmod(path_no_ext: str):
    st = os.stat(path_no_ext + ".safetensors")
    key = (path_no_ext, st.st_size, st.st_mtime_ns)
    if key in _MOD_CACHE:
        _MOD_CACHE.move_to_end(key)
        return _MOD_CACHE[key], key
    mod = _need_plugin().H3RefMod.load(path_no_ext)
    _lru_put(_MOD_CACHE, key, mod, 8)
    return mod, key


def qwen_item(block: dict, vae, key, reference_fps: float, kind: str = None) -> dict:
    """prompt.py MiniMaxH3RefModTextEncode: decode the (weakened) block latent; image = first frame,
    video = 2 fps by timestamps.  Audio refs only carry their <Audio k> label.  kind = receiver (slot)."""
    kind = kind or block["kind"]
    if kind == "audio":
        return {"type": "audio"}
    if key in _ITEM_CACHE:
        _ITEM_CACHE.move_to_end(key)
        return _ITEM_CACHE[key]
    pixels = vae.decode(block["latent"])
    if pixels.ndim == 5 and pixels.shape[0] == 1:
        pixels = pixels[0]
    if pixels.ndim != 4 or pixels.shape[-1] != 3 or pixels.shape[0] < 1:
        raise ValueError(f"{_TAG} H3 video VAE must decode to [frames, height, width, 3]; got {tuple(pixels.shape)}")
    item = {"type": kind}
    if kind == "image":
        item["data"] = pixels[:1].cpu().clone()
    else:
        times = [i / 2 for i in range(math.ceil(pixels.shape[0] * 2 / reference_fps))]
        idx = [min(round(t * reference_fps), pixels.shape[0] - 1) for t in times]
        item["data"] = pixels[idx].cpu()
        item["timestamps"] = times
    del pixels
    _lru_put(_ITEM_CACHE, key, item, 6)
    return item


def build_refmod_payload(entries: list, vae, options: dict, where: str = "", groups: list = None):
    """-> (qwen items, DiT blocks, log rows) in plan order (one block per RefMod).
    groups (optional list) receives (position key, n items, n blocks) per used entry; key ("image" | "video", at)
    or None (= appended after the own refs)."""
    items, blocks, rows = [], [], []
    fps = REFERENCE_FPS   # fixed; an old plan's reference_fps is ignored
    for e in entries:
        mod, key = load_refmod(e["path_no_ext"])
        block = mod.ref_block()
        if block is None:
            continue
        block["refmod"] = True
        slot = e.get("slot", block["kind"])
        if slot != block["kind"]:   # dropped on the other receiver: presented as that kind (H3 checks the shape)
            block["kind"] = slot
            if slot == "video":
                block.setdefault("latent_t", mod.latent_t)
                block.setdefault("ref_audio_t", 0)
                block.setdefault("audio_latent", None)
        items.append(qwen_item(block, vae, key + (fps, slot), fps, slot))
        blocks.append(block)
        if groups is not None:
            groups.append(((slot, e["at"]) if e.get("at") is not None else None, 1, 1))
        tok = int(mod.token_count)
        rows.append(f"RefMod {e['j']} '{e['id']}' {mod.kind}{'' if slot == mod.kind else ' as ' + slot} {mod.latent_t}x{mod.latent_h}x{mod.latent_w} "
                    f"= {tok} tok -> {e['tag'] or 'DiT only'}")
    return items, blocks, rows


def _interleave(base: list, extras: list, groups: list, anchors: dict) -> list:
    """extras (flat, plan order) split by groups [(key, n)]: key with an anchor goes right before base[anchors[key]],
    key None / without anchor is appended after base (old behaviour)."""
    before, tail, k = {}, [], 0
    for key, n in groups:
        part, k = extras[k:k + n], k + n
        idx = anchors.get(key) if key is not None else None
        (tail if idx is None else before.setdefault(idx, [])).extend(part)
    tail.extend(extras[k:])
    out = []
    for i, x in enumerate(base):
        out.extend(before.get(i, ()))
        out.append(x)
    return out + tail


def _anchors(base: list, kind_of) -> dict:
    """("image" | "video", p) -> index of the p-th own picture / video in the core's list; a video's soundtrack
    item (type audio right before it, core order) stays in front of its video."""
    out, cnt = {}, {"image": 0, "video": 0}
    for i, x in enumerate(base):
        kd = kind_of(x)
        if kd not in cnt:
            continue
        start = i - 1 if kd == "video" and i > 0 and kind_of(base[i - 1]) == "audio" else i
        out[(kd, cnt[kd])] = start
        cnt[kd] += 1
    return out


def _item_kind(x):
    return x.get("type") if isinstance(x, dict) else None


def _block_kind(x):
    kd = x.get("kind") if isinstance(x, dict) else None
    return "video" if kd in ("video", "video_audio") else kd


class _RefModClip:
    """CLIP view that adds the RefMod items to the core encoder's minimax_ref_items (after its own refs, or at their
    `at` position when groups are given)."""

    def __init__(self, clip, items, groups=None):
        self._clip, self._items = clip, list(items)
        self._groups = [(key, ni) for key, ni, _nb in groups] if groups else None

    def tokenize(self, text, *args, **kwargs):
        base = list(kwargs.pop("minimax_ref_items", None) or [])
        refs = (_interleave(base, self._items, self._groups, _anchors(base, _item_kind)) if self._groups
                else base + self._items)
        return self._clip.tokenize(text, *args, minimax_ref_items=refs, **kwargs)

    def __getattr__(self, name):
        return getattr(self._clip, name)


def append_refs(conditioning, blocks, groups=None):
    """RefMod Apply (nodes.py): minimax_refs = existing + blocks, on every conditioning entry; with groups a
    positioned RefMod's blocks go before the own picture / video it precedes (same order as the text encoder)."""
    out = []
    bg = [(key, nb) for key, _ni, nb in groups] if groups else None
    for t in conditioning:
        d = dict(t[1])
        base = list(d.get("minimax_refs", []))
        d["minimax_refs"] = _interleave(base, list(blocks), bg, _anchors(base, _block_kind)) if bg else base + list(blocks)
        out.append([t[0], d])
    return out


class H3MDSegmentEncodeRefMod(D.H3MDSegmentEncode):
    """H3MD Segment Encode + the shot's RefMods.  A segment without active RefMods runs the original encode."""

    @classmethod
    def INPUT_TYPES(cls):
        spec = copy.copy(D.H3MDSegmentEncode.INPUT_TYPES())
        spec["required"] = dict(spec["required"], plan=(PLAN_TYPE,))
        return spec

    def encode(self, plan, segment_index, clip, vae, audio_vae, ref_image_size="match", prompt_override=""):
        seg = D.get_segment(plan, segment_index)
        entries = list(seg.get("refmods") or [])
        if not entries:
            return super().encode(plan, segment_index, clip, vae, audio_vae, ref_image_size, prompt_override)
        where = f"segment {seg['index'] + 1}"
        opts = plan.get("refmod") or {}
        picked = D.pick_segment_media(plan, segment_index, ref_image_size,
                                      sum(int(e.get("tokens") or 0) for e in entries))
        prompt = (prompt_override or "").strip() or seg["prompt"]
        prompt, _ = rewrite_prompt(prompt, [e["tag"] for e in seg.get("refmods") or []])
        groups = []
        items, blocks, rows = build_refmod_payload(entries, vae, opts, where, groups)
        if not any(key is not None for key, _ni, _nb in groups):
            groups = None   # no positioned RefMod: plain append, exactly as before
        core = D._core_h3()
        out = core.MiniMaxH3ReferenceToVideo.execute(
            clip=_RefModClip(clip, items, groups) if items else clip, prompt=prompt, width=plan["width"],
            height=plan["height"], length=seg["length"], ref_image_size=ref_image_size, vae=vae,
            audio_vae=audio_vae, **D.build_core_kwargs(picked))
        positive, latent = D._unwrap_node_output(out)[:2]
        positive = append_refs(positive, blocks, groups)
        latent = {"samples": latent["samples"]}
        negative = D.zero_out_conditioning(positive)
        n_refs = len(positive[0][1].get("minimax_refs") or [])
        LOG.info("%s %s: %d RefMod block(s) %s %d own ref block(s), %d to the text encoder | %s",
                 _TAG, where, len(blocks), "placed by position among" if groups else "appended after",
                 n_refs - len(blocks), len(items), " | ".join(rows))
        LOG.info("[H3MD Encode] segment %d: %dx%d len %d overlapIn %d refs %s video windows %s",
                 seg["index"], plan["width"], plan["height"], seg["length"], seg["overlapIn"],
                 " ".join(lab["tag"] for lab in seg["labels"]),
                 [(tuple(v.shape[1:3]), v.shape[0]) for v in picked["videos"]])
        return (positive, negative, latent, prompt, json.dumps(seg["labels"], ensure_ascii=False), seg["length"],
                seg["overlapIn"], plan["width"], plan["height"], picked["locked_audio"])


# ----------------------------------------------------------------------------
# nodes
# ----------------------------------------------------------------------------

class H3MDDirectorRefMod(D.H3MDDirector):
    @classmethod
    def INPUT_TYPES(cls):
        spec = copy.deepcopy(D.H3MDDirector.INPUT_TYPES())
        spec["required"]["plan_json"][1]["tooltip"] = ("H3MD plan v1 JSON + media.refmods / segment.refmods "
                                                       "(edited in the visual editor)")
        spec["optional"].update({
            "refmod_reference_fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0, "step": 0.5, "tooltip":
                "Fixed at 24 (H3 standard): playback fps of a decoded video RefMod for the text encoder's 2 fps "
                "sampling. Read-only display; any other saved value is ignored."}),
        })
        return spec

    RETURN_TYPES = (PLAN_TYPE, "INT", "INT", "STRING", "STRING")
    RETURN_NAMES = ("plan", "segment_count", "total_frames", "summary", "plan_info")
    FUNCTION = "build_refmod"
    CATEGORY = D.CATEGORY
    DESCRIPTION = ("H3MD Director with a 4th reference kind: saved RefMods (ComfyUI-MiniMaxH3Mod). "
                   "Connect to H3MD Loop Sampler + RefMod.")

    @classmethod
    def IS_CHANGED(cls, plan_json="", **kwargs):
        """Re-run when a referenced RefMod file is re-saved under the same name (plan_json unchanged)."""
        stamps = []
        try:
            raw = json.loads(plan_json) if isinstance(plan_json, str) else (plan_json or {})
            used = used_refmod_ids(raw)
            for item in ((raw.get("media") or {}).get("refmods") or []):
                if not isinstance(item, dict) or str(item.get("id")) not in used:
                    continue   # 2026-10-24: library RefMods no shot uses do not affect re-runs
                try:
                    st = os.stat(resolve_refmod_file(item.get("file")) + ".safetensors")
                    stamps.append([item.get("file"), st.st_size, st.st_mtime_ns])
                except Exception:
                    stamps.append([item.get("file") if isinstance(item, dict) else None, "missing"])
        except Exception:
            pass
        return json.dumps(stamps)

    def build_refmod(self, plan_json, width=0, height=0, continuity_prompt=True,
                     refmod_reference_fps=REFERENCE_FPS, **_ignored):
        _ignored.pop("refmod_to_text_encoder", None)   # toggle removed 2026-10-24 (old workflows may still pass it): always on
        refmod_reference_fps = REFERENCE_FPS            # read-only widget: hard-locked at 24 whatever a workflow saved
        plan, count, total, summary, info = self.build(plan_json, width, height, continuity_prompt,
                                                       extra_tokens=refmod_tokens_by_shot(plan_json))
        try:
            raw = json.loads(plan_json) if isinstance(plan_json, str) else (plan_json or {})
        except json.JSONDecodeError:
            raw = {}
        notes = parse_refmods(raw if isinstance(raw, dict) else {}, plan,
                              {"reference_fps": refmod_reference_fps})
        plan["notes"] = list(plan.get("notes", [])) + notes
        summary = D.plan_summary(plan)
        rs = refmod_summary(plan)
        if rs:
            summary += "\n" + rs
        LOG.info("%s %s", _TAG, rs.replace("\n", " | ") if rs else "no RefMods in this plan")
        try:
            info = D.build_plan_info(plan)
        except Exception as exc:
            info = f"OK plan valid (plan_info check failed: {exc})"
        return (plan, count, total, summary, info)


_RUN_SIG = inspect.signature(L.H3MDLoopSampler._run)


def _run_arguments(args, kwargs) -> dict:
    """Map a call of H3MDLoopSampler._run (self first) to {parameter name: value}, defaults filled in.
    _run has only named parameters (no *args / **kwargs)."""
    names = list(_RUN_SIG.parameters)
    if len(args) > len(names):
        raise TypeError(f"_run takes {len(names)} arguments, got {len(args)}")
    out = dict(zip(names, args))
    for k, v in kwargs.items():
        if k not in _RUN_SIG.parameters:
            raise TypeError(f"_run got an unexpected argument {k!r}")
        if k in out:
            raise TypeError(f"_run got multiple values for {k!r}")
        out[k] = v
    for name, p in _RUN_SIG.parameters.items():
        if name not in out:
            if p.default is inspect.Parameter.empty:
                raise TypeError(f"_run missing argument {name!r}")
            out[name] = p.default
    return out


class H3MDLoopSamplerRefMod(LS.H3MDLoopSampler2):
    """H3MD Loop Sampler, segments encoded with H3MDSegmentEncodeRefMod (identical when no RefMod is active)."""

    @classmethod
    def INPUT_TYPES(cls):
        spec = LS.H3MDLoopSampler2.INPUT_TYPES()
        spec["required"] = {k: ((PLAN_TYPE,) if k == "plan" else v) for k, v in spec["required"].items()}
        return spec

    DESCRIPTION = ("H3MD Loop Sampler for H3MD Director + RefMod plans: every segment gets its RefMods "
                   "(DiT refs + text encoder). Same inputs/outputs as H3MD Loop Sampler.")

    def _run(self, *args, **kwargs):
        a = _run_arguments((self,) + args, kwargs)
        plan = a["plan"]
        if plan_has_refmods(plan):
            a["encoder"] = H3MDSegmentEncodeRefMod()
            sig = refmod_cache_sig(plan)
            old = a["settings_hash"]
            new = {"base": C._sha({"base": old["base"], "refmods": sig}),
                   "full": C._sha({"full": old["full"], "refmods": sig})}
            a["settings_hash"] = new
            cache = a.get("cache")
            if cache is not None and a.get("resume"):
                # own resume folder: a RefMod run never overwrites the RefMod-off resume cache
                a["cache"] = C.SegmentCache(os.path.dirname(cache.dir), new["base"], True, cache.dtype)
            LOG.info("%s loop: RefMod encode active (%d segment(s) with RefMods), cache key includes RefMods",
                     _TAG, sum(1 for s in plan["segments"] if s.get("refmods")))
        return L.H3MDLoopSampler._run(**a)


def register_routes() -> bool:
    try:
        import asyncio
        from aiohttp import web
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception:
        return False

    @routes.get("/h3md/refmods")
    async def h3md_refmods(request):
        try:
            items = await asyncio.get_running_loop().run_in_executor(None, list_refmods)
            return web.json_response({"refmods": items, "plugin": plugin_nodes() is not None})
        except Exception as exc:
            return web.json_response({"error": str(exc), "refmods": []}, status=500)

    @routes.get("/h3md/refmod_info")
    async def h3md_refmod_info(request):
        file = request.query.get("file", "")
        try:
            h = await asyncio.get_running_loop().run_in_executor(None, lambda: read_header(resolve_refmod_file(file)))
            h.pop("path", None)
            return web.json_response({"file": file, "exists": True, **h})
        except Exception as exc:
            return web.json_response({"file": file, "exists": False, "error": str(exc)})

    return True


try:
    register_routes()
except Exception as _exc:  # never blocks node loading
    LOG.warning("[H3MD] RefMod routes not registered: %s", _exc)

NODE_CLASS_MAPPINGS = {
    "H3MDDirectorRefMod": H3MDDirectorRefMod,
    "H3MDLoopSamplerRefMod": H3MDLoopSamplerRefMod,
    "H3MDSegmentEncodeRefMod": H3MDSegmentEncodeRefMod,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3MDDirectorRefMod": "H3MD Director + RefMod",
    "H3MDLoopSamplerRefMod": "H3MD Loop Sampler + RefMod",
    "H3MDSegmentEncodeRefMod": "H3MD Segment Encode + RefMod",
}
