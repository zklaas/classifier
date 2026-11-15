"""Everything about one software's standing, worked out in one place."""

from pathlib import Path

from . import aiuse, licences, model, platforms, scanner

_fp_cache: dict[str, str] = {}


def refresh_fingerprint(sw: dict) -> str | None:
    p = Path(sw["path"])
    if not p.is_dir():
        return None
    fp = scanner.fingerprint(p)
    _fp_cache[sw["path"]] = fp
    return fp


def of(sw: dict, settings: dict, fresh: bool = False) -> dict:
    fp = refresh_fingerprint(sw) if fresh else _fp_cache.get(sw["path"])
    cleared, reason = model.clearance(sw, fp)
    grp, note = model.group(sw, cleared)
    lic = (licences.check(Path(sw["path"]), settings.get("ai_system", "Claude"))
           if Path(sw["path"]).is_dir() else {"status": "missing", "message": "Folder missing"})
    return {"cleared": cleared, "reason": reason, "group": grp, "group_note": note,
            "licence": lic["status"], "licence_info": lic,
            "unresolved": len(model.unresolved(sw)) if sw.get("scan") else None,
            "ai": aiuse.level(sw)}


def scan(sw: dict, settings_owner_names: list[str], settings: dict, progress=None):
    sw["scan"] = scanner.scan(sw["path"], settings_owner_names,
                              settings.get("ai_system", "Claude"),
                              settings.get("ai_maker", "Anthropic"), progress=progress)
    _fp_cache[sw["path"]] = sw["scan"]["fingerprint"]
    review_platforms(sw)
    sw["ai_review"] = aiuse.review(sw["path"])
    return sw["scan"]


def review_platforms(sw: dict, placements=None):
    if not Path(sw["path"]).is_dir():
        return
    if placements is None:
        placements = (sw.get("platform_review") or {}).get("placements", [])
    rev = platforms.review(sw["path"], (sw.get("scan") or {}).get("python_deps", []),
                           placements)
    rev["placements"] = [{"repo": l["repo"], "branches": l["branches"]} for l in placements]
    rev["at"] = model.now()
    sw["platform_review"] = rev
