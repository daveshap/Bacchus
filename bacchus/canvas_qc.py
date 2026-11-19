"""Automated BFG / Warm-Editorial visual & temporal QC on headless Chrome Canvas."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re

from . import browser_cdp, canvas_engine


STAGE_W = 1920
STAGE_H = 1080

_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_STOP = frozenset(
    "a an the of to in on and is it for that this at by as be are was were or so than its with from "
    "no not you we i can do does will would what why how all any have has just your our they them their "
    "he she his her there here when which who if but about".split()
)


def _say(n: int) -> list[str]:
    if n < 0:
        return _say(-n)
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        q, r = divmod(n, 10)
        return [_TENS[q], *([_ONES[r]] if r else [])]
    if n < 1000:
        q, r = divmod(n, 100)
        return [_ONES[q], "hundred", *(_say(r) if r else [])]
    if n < 1_000_000:
        q, r = divmod(n, 1000)
        return [*_say(q), "thousand", *(_say(r) if r else [])]
    return [str(n)]


def _label_tokens(text: str) -> list[str]:
    cleaned = text.lower().replace("’", "'").replace("‘", "'")
    out = []
    for raw in re.split(r"[^a-z0-9'%]+", cleaned):
        if not raw:
            continue
        percent = raw.endswith("%")
        core = raw[:-1] if percent else raw
        if core.isdigit() and int(core) < 1_000_000:
            out.extend(_say(int(core)))
            if percent:
                out.extend(("per", "cent", "percent"))
            continue
        core = core.removesuffix("'s")
        if core and core not in _STOP:
            out.append(core)
    return out


def _same_stem(a: str, b: str) -> bool:
    if a == b:
        return True
    prefix = 0
    limit = min(len(a), len(b))
    while prefix < limit and a[prefix] == b[prefix]:
        prefix += 1
    return prefix >= 4 and prefix >= limit - 1


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a, b) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def analyze_snapshots(
    snapshots: list[dict],
    occlusion: list[dict],
    blank_frames: list[dict],
    words: list[dict],
    scenes: list[dict],
    *,
    step: float = 0.25,
    jumps_report: dict | None = None,
    page_errors: list[str] | None = None,
) -> dict:
    """Pure Python analyzer implementing BFG audit.mjs + cues.mjs + jumps.mjs + warm-checks.mjs."""
    findings: list[dict] = []
    seen_issue_keys: set[str] = set()
    seen_labels: dict[str, dict] = {}
    first_seen: dict[str, dict] = {}
    label_sightings = 0

    def add_finding(level: str, kind: str, scene: str, t: float | None, message: str, dedup_key: str):
        key = f"{scene}|{kind}|{dedup_key}"
        if key in seen_issue_keys:
            return
        seen_issue_keys.add(key)
        findings.append({
            "level": level,
            "kind": kind,
            "scene": scene,
            "time": round(float(t), 2) if t is not None else None,
            "message": message,
        })

    for err in page_errors or ():
        add_finding("error", "runtime", "film", None, f"Canvas runtime error: {err}", err)

    for snap in snapshots:
        t = float(snap["t"])
        items = snap.get("items") or []
        for item in items:
            if item.get("kind") in ("text", "pill") and float(item.get("a", 0)) >= 0.05:
                key = f"{item['id']}|{item['str']}"
                if key not in first_seen or first_seen[key]["t"] > t:
                    first_seen[key] = {"t": t, "scene": item["id"], "str": item["str"]}
        visible = [item for item in items if float(item.get("a", 0)) >= 0.6 and not item.get("inTransition")]
        labels = [item for item in visible if item.get("kind") in ("text", "pill")]
        label_sightings += len(labels)

        for label in labels:
            box = label["box"]
            x0, y0, x1, y1 = box
            short = " ".join(label["str"].split())[:80]
            if x0 < -6 or y0 < -6 or x1 > STAGE_W + 6 or y1 > STAGE_H + 6:
                add_finding(
                    "error", "cut", label["id"], t,
                    f"Canvas audit [{label['id']} at {t:.2f}s]: label \"{short}\" is clipped by the 16:9 frame ({box}).",
                    short,
                )
            for other in visible:
                if other["id"] != label["id"]:
                    continue
                if other.get("kind") == "card":
                    ob = other["box"]
                    if _intersection(box, ob) >= 0.45 * max(1.0, _area(box)):
                        if x0 < ob[0] - 8 or y0 < ob[1] - 8 or x1 > ob[2] + 8 or y1 > ob[3] + 8:
                            add_finding(
                                "warning", "card", label["id"], t,
                                f"Canvas audit [{label['id']} at {t:.2f}s]: \"{short}\" extends beyond its card.",
                                short,
                            )
            lkey = f"{label['id']}|{label['str']}"
            if lkey not in seen_labels:
                seen_labels[lkey] = {
                    "scene": label["id"],
                    "str": label["str"],
                    "words": len(label["str"].split()),
                    "times": [],
                }
            seen_labels[lkey]["times"].append(t)

        # Label-over-label overlap check
        for i in range(len(labels)):
            a = labels[i]
            for j in range(i + 1, len(labels)):
                b = labels[j]
                if a["id"] != b["id"]:
                    continue
                inter = _intersection(a["box"], b["box"])
                if a["str"] == b["str"] and inter > 0.85 * max(1.0, _area(a["box"])):
                    continue
                min_area = min(_area(a["box"]), _area(b["box"]))
                if min_area > 0 and inter > 0.12 * min_area:
                    sa = " ".join(a["str"].split())[:42]
                    sb = " ".join(b["str"].split())[:42]
                    add_finding(
                        "error", "overlap", a["id"], t,
                        f"Canvas audit [{a['id']} at {t:.2f}s]: \"{sa}\" overlaps \"{sb}\".",
                        f"{sa}|{sb}",
                    )

        # Artwork frame clipping check
        for art in visible:
            if art.get("kind") != "art":
                continue
            bx0, by0, bx1, by1 = art["box"]
            if bx0 < -14 or by0 < -14 or bx1 > STAGE_W + 14 or by1 > STAGE_H + 14:
                add_finding(
                    "error", "cut", art["id"], t,
                    f"Canvas audit [{art['id']} at {t:.2f}s]: artwork '{art['str']}' is clipped by the frame.",
                    f"art|{art['str']}",
                )

    # A label occupying the entire shot can still leave too little reading time.
    for entry in seen_labels.values():
        times = sorted(set(entry["times"]))
        needed = max(0.85, 0.24 * entry["words"] + 0.3)
        run = step
        best = step
        for idx in range(1, len(times)):
            if times[idx] - times[idx - 1] <= step * 1.6:
                run += times[idx] - times[idx - 1]
            else:
                run = step
            best = max(best, run)
        if best + 0.05 < needed:
            short = " ".join(entry["str"].split())[:64]
            add_finding(
                "warning", "dwell", entry["scene"], times[0],
                f"Canvas audit [{entry['scene']}]: \"{short}\" stays readable for {best:.1f}s (recommended {needed:.1f}s).",
                short,
            )

    # Text-over-artwork dark-pixel occlusion (from BFG audit.mjs)
    for occ in occlusion or ():
        short = " ".join(str(occ.get("str", "")).split())[:64]
        frac = float(occ.get("fraction", 0))
        if frac > 0.14:
            add_finding(
                "warning", "occlusion", occ["scene"], occ["t"],
                f"Canvas audit [{occ['scene']} at {occ['t']:.2f}s]: \"{short}\" sits over high-contrast background ({round(frac * 100)}% dark pixels).",
                short,
            )

    # Temporal contiguity / cue alignment check (from BFG cues.mjs)
    spoken = [
        (re.sub(r"^[^a-z0-9']+|[^a-z0-9']+$", "", str(wd.get("text", "")).lower()).removesuffix("'s"), float(wd["start"]))
        for wd in words or ()
    ]
    cue_matches = 0
    cue_issues = 0
    if spoken:
        for entry in first_seen.values():
            tokens = _label_tokens(entry["str"])
            if not tokens:
                continue
            best = None
            for tok, at in spoken:
                if not tok or abs(at - entry["t"]) > 4.0:
                    continue
                if any(_same_stem(w_tok, tok) for w_tok in tokens):
                    dist = abs(at - (entry["t"] + 0.2))
                    if best is None or dist < best[0]:
                        best = (dist, tok, at)
            if best is None:
                continue
            cue_matches += 1
            _, matched_word, word_at = best
            lead = word_at - entry["t"]
            short = " ".join(entry["str"].split())[:64]
            if lead < -0.35:
                cue_issues += 1
                add_finding(
                    "warning", "cue-late", entry["scene"], entry["t"],
                    f"Cue audit [{entry['scene']}]: \"{short}\" enters {-lead:.2f}s after \"{matched_word}\" ({word_at:.2f}s).",
                    short,
                )
            elif lead > 1.8:
                cue_issues += 1
                add_finding(
                    "warning", "cue-early", entry["scene"], entry["t"],
                    f"Cue audit [{entry['scene']}]: \"{short}\" enters {lead:.2f}s before \"{matched_word}\" ({word_at:.2f}s).",
                    short,
                )

    # Blank frame sweep
    for bf in blank_frames or ():
        add_finding(
            "warning", "blank", bf["scene"], bf["t"],
            f"Frame sweep [{bf['scene']} at {bf['t']:.2f}s]: frame appears blank or uniform.",
            f"blank|{bf['scene']}",
        )

    # Single-frame jumps / flicker sweep (from BFG jumps.mjs)
    jumps = (jumps_report or {}).get("jumps") or []
    for jump in jumps[:10]:
        add_finding(
            "warning", "jump", jump["scene"], jump["t"],
            f"Motion jump [{jump['scene']} at {jump['t']:.2f}s]: sudden single-frame change ({jump['changed']} px vs {jump['baseline']} baseline).",
            f"{jump['t']:.2f}",
        )

    error_count = sum(1 for f in findings if f["level"] == "error")
    warning_count = sum(1 for f in findings if f["level"] == "warning")
    return {
        "findings": findings,
        "summary": {
            "framesSampled": len(snapshots),
            "labelsChecked": len(seen_labels),
            "labelSightings": label_sightings,
            "cueMatches": cue_matches,
            "cueIssues": cue_issues,
            "jumpsDetected": len(jumps),
            "blankFrames": len(blank_frames or ()),
            "errors": error_count,
            "warnings": warning_count,
        },
    }


def run_canvas_qc(
    project: dict,
    folder: Path,
    *,
    palette_dict: dict,
    preset: str,
    run_jumps: bool = True,
    jumps_fps: int = 30,
    step: float = 0.25,
) -> dict:
    """Run full BFG automated Canvas QC (audit, cues, occlusion, blank sweep, jumps) in one Chrome tab."""
    html_bytes, assets, build_hash = canvas_engine.build_film(
        project, folder, palette_dict=palette_dict, preset=preset,
    )

    def _inspect(browser, session_id, _ready, page_errors):
        sweep = browser.eval_js(
            session_id,
            f"window.__film.sweepAudit({json.dumps(float(step))}, 135)",
            timeout=180.0,
        )
        jumps = None
        if run_jumps:
            jumps = browser.eval_js(
                session_id,
                f"window.__film.jumpsSweep({json.dumps(int(jumps_fps))}, 90)",
                timeout=180.0,
            )
        return sweep, jumps, list(page_errors)

    sweep, jumps, page_errors = browser_cdp.run_with_film(html_bytes, assets, 0.5, _inspect)
    report = analyze_snapshots(
        sweep.get("snapshots", []),
        sweep.get("occlusion", []),
        sweep.get("blankFrames", []),
        project.get("input", {}).get("words") or [],
        sweep.get("scenes", []),
        step=step,
        jumps_report=jumps,
        page_errors=page_errors,
    )
    report["buildHash"] = build_hash
    return report


def render_animation_proofs_batch(
    project: dict,
    folder: Path,
    checks: list[dict],
    *,
    palette_dict: dict,
    preset: str,
) -> dict[str, bytes]:
    """Render contact-sheet PNG proofs for multiple scenes inside one Chrome session."""
    if not checks:
        return {}
    html_bytes, assets, _ = canvas_engine.build_film(
        project, folder, palette_dict=palette_dict, preset=preset,
    )

    def _sheets(browser, session_id, _ready, page_errors):
        results: dict[str, bytes] = {}
        for check in checks:
            samples = check.get("samples") or []
            if not samples:
                continue
            detailed = len(samples) > 10
            cols = min(3 if detailed else 2, len(samples))
            tw = 544 if detailed else 768
            b64 = browser.eval_js(
                session_id,
                f"window.__film.sheet({json.dumps(samples)}, {json.dumps(check['title'])}, {cols}, {tw})",
                timeout=120.0,
            )
            results[check["id"]] = base64.b64decode(b64)
        if page_errors:
            raise browser_cdp.BrowserError("; ".join(page_errors[:5]))
        return results

    return browser_cdp.run_with_film(html_bytes, assets, 0.5, _sheets)
