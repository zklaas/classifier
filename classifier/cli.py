"""Terminal commands: classifier list | scan | show | licence | github | publish | release |
catalogue | review."""

from __future__ import annotations

import argparse
import sys

from . import (aiuse, catalogue, github, publish, licences, model, platforms, releases, review,
               status)


def _reg() -> model.Registry:
    reg = model.Registry()
    if not reg.software:
        if not reg.settings.get("roots"):
            sys.exit("Classifier does not know where your software is yet: open the window "
                     "(classifier) and fill in Settings, or set \"roots\" in "
                     f"{reg.path}.")
        reg.discover()
        reg.save()
    return reg


def _pick(reg, name) -> dict:
    for n, sw in reg.software.items():
        if n.lower() == name.lower():
            return sw
    sys.exit(f"no software called {name!r}; see: classifier list")


def _auto_catalogue(reg):
    """Keep the public catalogue up to date after a change on GitHub."""
    if catalogue.auto_enabled(reg.settings):
        try:
            print(catalogue.refresh(reg, progress=print))
        except Exception as e:                     # noqa: BLE001 - say it, don't fail the command
            print(f"The catalogue could not be updated: {e}")


def cmd_list(a):
    reg = _reg()
    rows = []
    for n, sw in sorted(reg.software.items(), key=lambda x: x[0].lower()):
        st = status.of(sw, reg.settings, fresh=True)
        rows.append((n, st["group"], "yes" if st["cleared"] else "no", st["reason"],
                     licences.STATUS_SHORT[st["licence"]]))
    w = max(len(r[0]) for r in rows) if rows else 4
    print(f"{'Software':<{w}}  {'Development Group':<44} Cleared  Licence       Why")
    for r in rows:
        print(f"{r[0]:<{w}}  {r[1]:<44} {r[2]:<8} {r[4]:<13} {r[3]}")


def cmd_scan(a):
    reg = _reg()
    names = a.names or sorted(reg.software)
    for n in names:
        sw = _pick(reg, n)
        s = status.scan(sw, reg.owner_names(), reg.settings)
        left = len(model.unresolved(sw))
        print(f"{sw['name']}: {s['files']} files, {len(s['findings'])} items, {left} unresolved")
    reg.save()


def cmd_show(a):
    reg = _reg()
    sw = _pick(reg, a.name)
    st = status.of(sw, reg.settings, fresh=True)
    print(f"{sw['name']}  ({sw['path']})")
    print(f"  Stage: {model.STAGES.get(sw['stage'])}   Suite: {sw['suite'] or '-'}")
    print(f"  Development Group: {st['group']}  ({st['group_note']})")
    print(f"  Cleared: {'yes' if st['cleared'] else 'no'}  ({st['reason']})")
    print(f"  Licence: {st['licence_info']['message']}")
    ai_rev = sw.get("ai_review") or {}
    if ai_rev or (sw.get("ai") or {}).get("level"):
        print(f"  AI: {aiuse.describe(sw) or '?'}"
              + ("" if (sw.get("ai") or {}).get("level") else "  [suggested: "
                 + ai_rev.get("why", "") + "]"))
    rev = sw.get("platform_review") or {}
    print("  Platforms:")
    for p in platforms.PLATFORMS:
        t = platforms.TRACKS[sw.get("platforms", {}).get(p, {}).get("track", "")]
        ev, bl = rev.get("evidence", {}).get(p, []), rev.get("blockers", {}).get(p, [])
        print(f"    {p:<11} {t:<20} evidence: {len(ev)}  blockers: {len(bl)}"
              + (f"  ({ev[0]})" if ev else ""))
    for w in platforms.warnings(sw):
        print(f"    ! {w}")
    print("  Downloads:")
    for line in releases.describe(sw):
        print(f"    {line}")
    langs = (sw.get("scan") or {}).get("languages", {})
    if langs:
        print("  Languages (lines): " + ", ".join(f"{k} {v:,}" for k, v in langs.items()))
    if sw.get("scan"):
        print(f"  Last scan {sw['scan']['at']}:")
        for f in model.findings(sw):
            r = model.resolution_of(sw, f)
            res = model.RESOLUTIONS.get(r.get("kind", ""), "?")
            if r.get("licence"):
                res += f" ({r['licence']})"
            if r.get("by") == "auto":
                res += " [auto]"
            print(f"    - {f['title']}: {res}")


def cmd_licence(a):
    reg = _reg()
    sw = _pick(reg, a.name)
    info = licences.check(model.Path(sw["path"]), reg.settings["ai_system"])
    print(info["message"])
    spdx, why = licences.recommend(sw)
    print(f"Recommended: {spdx} ({why})")
    if a.write:
        st = status.of(sw, reg.settings, fresh=True)
        if not st["cleared"]:
            sys.exit(f"{sw['name']} is not Cleared ({st['reason']}); no licence written.")
        if not reg.settings.get("licensor"):
            sys.exit("Set your name as licensor first (in the window: Settings).")
        for p in licences.write(sw, reg.settings, a.write):
            print(f"Wrote {p}")


def cmd_github(a):
    reg = _reg()
    owner = reg.settings.get("github_owner") or model.gh_login()
    if not owner:
        sys.exit("gh is not logged in (gh auth login).")
    print(f"Reading every repository of {owner} ...")
    snap = github.snapshot(owner)
    statuses = {n: status.of(sw, reg.settings, fresh=True) for n, sw in reg.software.items()}
    issues, placed = github.audit(reg, snap, statuses)
    for n, sw in reg.software.items():
        if sw.get("scan"):
            status.review_platforms(sw, placed.get(n, []))
    print("Looking at releases (downloads) ...")
    issues += releases.audit(reg, statuses, placed, snap)
    reg.save()
    if not issues:
        print("GitHub matches every Development Group.")
        if catalogue.auto_enabled(reg.settings):
            print(catalogue.refresh(reg, progress=print, snap=snap))
        return
    for i in issues:
        print(f"[{i['level'].upper()}] {i['message']}")
    if not a.fix:
        acts = [i for i in issues if i["action"]]
        if acts:
            print(f"\n{len(acts)} change(s) needed. Run `classifier github --fix` to make them.")
        elif catalogue.auto_enabled(reg.settings):
            print(catalogue.refresh(reg, progress=print, snap=snap))
        return
    for i in issues:
        if not i["action"]:
            continue
        print(f"\n{i['message']}")
        if i["action"] == "make_private":
            if not reg.settings.get("auto_private") and input("Make it private? [y/N] ").lower() != "y":
                continue
        else:
            typed = input(f"This deletes from GitHub. Type {i['repo']} to go ahead: ")
            if typed.strip() != i["repo"]:
                print("Skipped.")
                continue
        try:
            print(github.apply(i))
        except github.GhError as e:
            print(f"FAILED: {e}")
    _auto_catalogue(reg)


def cmd_platform(a):
    reg = _reg()
    sw = _pick(reg, a.name)
    match = [p for p in platforms.PLATFORMS if p.lower().startswith(a.platform.lower())]
    if not match:
        sys.exit("platform: " + ", ".join(platforms.PLATFORMS))
    track = {"none": "", "planned": "planned", "progress": "progress",
             "untested": "untested", "supported": "supported"}[a.track]
    sw.setdefault("platforms", {}).setdefault(match[0], {})["track"] = track
    reg.save()
    print(f"{sw['name']} on {match[0]}: {platforms.TRACKS[track]}")


def cmd_catalogue(a):
    reg = _reg()
    if a.publish:
        print(catalogue.refresh(reg, progress=print))
        return
    statuses = {n: status.of(sw, reg.settings, fresh=True) for n, sw in reg.software.items()}
    owner = reg.settings.get("github_owner") or model.gh_login()
    if not owner:
        sys.exit("gh is not logged in (gh auth login): the catalogue lists only software "
                 "in public repositories, so GitHub must be read.")
    _, placed = github.audit(reg, github.snapshot(owner), statuses)
    text = catalogue.page(reg, statuses, placed,
                          catalogue.find_downloads(catalogue.listed(reg, statuses, placed), placed))
    if a.out:
        model.Path(a.out).write_text(text, encoding="utf-8")
        print(f"Wrote {a.out} ({len(catalogue.listed(reg, statuses, placed))} software listed)")
    else:
        print(text)


def cmd_publish(a):
    reg = _reg()
    sw = _pick(reg, a.name)
    owner = reg.settings.get("github_owner") or model.gh_login()
    if not owner:
        sys.exit("gh is not logged in (gh auth login).")
    st = status.of(sw, reg.settings, fresh=True)
    print("Reading GitHub ...")
    snap = github.snapshot(owner)
    statuses = {n: status.of(s, reg.settings) for n, s in reg.software.items()}
    _, placed = github.audit(reg, snap, statuses)
    dest = publish.destination(sw, placed, owner)
    if a.repo:
        info = snap.get(a.repo)
        dest = {"repo": a.repo, "path": a.path or "", "exists": info is not None,
                "visibility": info["visibility"] if info else "",
                "branches": list(info["branches"]) if info else [], "how": "given"}
    pl = publish.plan(sw)
    private_only = publish.must_be_private(st)
    print(f"{sw['name']}: {st['group']}"
          + (" (Private: private repository only)" if private_only else ""))
    print(f"Destination: {dest['repo']}{'/' + dest['path'] if dest['path'] else ''} "
          + (f"({dest['visibility'].lower()}, exists)" if dest['exists'] else "(new)"))
    print(f"Will publish {len(pl['include'])} files.")
    for rel, why in pl["excluded"]:
        print(f"  excluded: {rel}  <- {why}")
    for rel, why in pl["always_out"]:
        print(f"  left out: {rel}  <- {why}")
    probs = publish.blockers(sw, st, dest)
    for p in probs:
        print(f"  CANNOT: {p}")
    if probs or a.dry_run:
        return
    vis = "private" if private_only or not a.public else "public"
    merges = [b for b, _ in publish.platform_branches(
        sw, dest["branches"], "main" if "main" in dest["branches"] else
        (dest["branches"][0] if dest["branches"] else "main"))]
    q = (f"{'Create the ' + vis.upper() + ' repository and ' if not dest['exists'] else ''}"
         f"commit and push to {dest['repo']}"
         + (f" (and merge into {', '.join(merges)})" if merges else "") + "? [y/N] ")
    if input(q).strip().lower() != "y":
        print("Nothing done.")
        return
    if a.make_public and (private_only or publish.other_private_software(
            reg, statuses, placed, dest["repo"], sw["name"])):
        sys.exit("It cannot be made public: Private software lives in it.")
    print(publish.publish(sw, reg.settings, dest, visibility=vis, private_only=private_only,
                          merge_branches=merges, progress=print, sync_folder=a.sync,
                          make_public=a.make_public))
    if a.sync:
        status.scan(sw, reg.owner_names(), reg.settings)   # the folder changed: keep it Cleared
        reg.save()
    _auto_catalogue(reg)
    if a.release:
        public = (a.make_public or (dest["exists"] and dest.get("visibility") == "PUBLIC")
                  or (not dest["exists"] and vis == "public"))
        if dest["path"] or not public:
            print("No release built: downloads are made for software that is a whole public "
                  "repository.")
            return
        _build(reg, sw, dest["repo"], a.version)


def _build(reg, sw, repo, version=None):
    rel = releases.latest_release(repo)
    version = version or releases.next_version(rel["tag"] if rel else None)
    print(releases.build_and_catalogue(reg, sw, repo, version, progress=print))
    reg.save()


def _public_repo(reg, sw) -> str:
    owner = reg.settings.get("github_owner") or model.gh_login()
    if not owner:
        sys.exit("gh is not logged in (gh auth login).")
    print("Reading GitHub ...")
    snap = github.snapshot(owner)
    statuses = {n: status.of(s, reg.settings) for n, s in reg.software.items()}
    _, placed = github.audit(reg, snap, statuses)
    loc = next((l for l in placed.get(sw["name"], []) if not l["path"]), None)
    return loc["repo"] if loc else publish.destination(sw, placed, owner)["repo"]


def cmd_release(a):
    reg = _reg()
    sw = _pick(reg, a.name)
    if a.setup:
        repo = a.repo or _public_repo(reg, sw)
        plan = releases.plan_setup(sw, reg.settings, repo, entry=a.entry, force=a.force)
        i = plan["info"]
        if i:
            print(f"{i['title']}: starts with {i['entry'] or '?'}; downloads for "
                  f"{', '.join(i['platforms'])} from {repo}")
            print(f"  packages: {' '.join(i['pip'])}")
            print(f"  bundled beside it: {', '.join(i['data']) or 'nothing'}")
            for p, n in i["assets"].items():
                print(f"  {p}: {n}")
        for w in plan["warnings"]:
            print(f"  ! {w}")
        for p in plan["problems"]:
            print(f"  CANNOT: {p}")
        if plan["problems"]:
            sys.exit(1)
        names = list(plan["files"]) + (["README (adds a Download section)"]
                                       if plan["readme"] is not None else [])
        print("Will write: " + ", ".join(names))
        if a.dry_run:
            return
        if input("Write these files into the software's folder? [y/N] ").strip().lower() != "y":
            print("Nothing written.")
            return
        for rel in releases.write_setup(sw, plan):
            print(f"Wrote {rel}")
        status.scan(sw, reg.owner_names(), reg.settings)
        reg.save()
        print(f"Next: classifier publish {sw['name']} --release   (pushes, builds the "
              "downloads, then updates the catalogue)")
        return
    if a.build:
        repo = a.repo or _public_repo(reg, sw)
        if not releases.setup_info(sw["path"]):
            sys.exit(f"{sw['name']} has no release build set up: classifier release "
                     f"{sw['name']} --setup")
        rel = releases.latest_release(repo)
        version = a.version or releases.next_version(rel["tag"] if rel else None)
        if input(f"Build release {version} of {repo} on GitHub (about 10-20 minutes), then "
                 "update the catalogue? [y/N] ").strip().lower() != "y":
            print("Nothing done.")
            return
        _build(reg, sw, repo, version)
        return
    for line in releases.describe(sw):
        print(line)
    for p in releases.problems_when_packaged(sw):
        print(f"! {p}")


def cmd_review(a):
    reg = _reg()
    sw = _pick(reg, a.name)
    if not sw.get("scan"):
        status.scan(sw, reg.owner_names(), reg.settings)
        reg.save()
    proc, log = review.launch(sw, reg.settings)
    print(f"Opening Claude's review window (log: {log}). When it is done: "
          f"classifier review {sw['name']} --import")


def cmd_review_import(a):
    reg = _reg()
    sw = _pick(reg, a.name)
    data = review.load(sw)
    if not data:
        sys.exit("No review.json yet.")
    sw["claude_review"] = data
    reg.save()
    print(f"Imported {len(data['items'])} verdict(s). Accept them in the window "
          "(Provenance tab), or: classifier review NAME --accept")


def main(argv):
    ap = argparse.ArgumentParser(prog="classifier", description="Development Groups, "
                                 "clearance and licences for your software.")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("scan"); p.add_argument("names", nargs="*"); p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("show"); p.add_argument("name"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("licence", aliases=["license"]); p.add_argument("name")
    p.add_argument("--write", metavar="SPDX", choices=list(licences.WRITABLE))
    p.set_defaults(fn=cmd_licence)
    p = sub.add_parser("github"); p.add_argument("--fix", action="store_true")
    p.set_defaults(fn=cmd_github)
    p = sub.add_parser("platform", help="set a platform track")
    p.add_argument("name"); p.add_argument("platform")
    p.add_argument("track", choices=["none", "planned", "progress", "untested", "supported"])
    p.set_defaults(fn=cmd_platform)
    p = sub.add_parser("publish", help="commit and push a software to GitHub")
    p.add_argument("name")
    p.add_argument("--dry-run", action="store_true", help="only show what would happen")
    p.add_argument("--repo", help="owner/name instead of the one Classifier picks")
    p.add_argument("--path", help="folder inside --repo")
    p.add_argument("--public", action="store_true",
                   help="create a new repository public (Releasable software only)")
    p.add_argument("--make-public", action="store_true",
                   help="make the existing private repository public (history checked first)")
    p.add_argument("--sync", action="store_true",
                   help="bring the new commit into the folder's own git copy")
    p.add_argument("--release", action="store_true",
                   help="then build a new release (downloads) and update the catalogue "
                        "when it is done")
    p.add_argument("--version", help="version number for --release (default: the next one)")
    p.set_defaults(fn=cmd_publish)
    p = sub.add_parser("release", help="downloadable packages: show, set up or build")
    p.add_argument("name")
    p.add_argument("--setup", action="store_true",
                   help="write a release build (GitHub Actions + PyInstaller) into the folder")
    p.add_argument("--entry", help="with --setup: the script that starts the program")
    p.add_argument("--force", action="store_true",
                   help="with --setup: replace a release build that is already there")
    p.add_argument("--dry-run", action="store_true", help="with --setup: only show the plan")
    p.add_argument("--build", action="store_true",
                   help="build a new release on GitHub, wait, then update the catalogue")
    p.add_argument("--version", help="with --build: version number (default: the next one)")
    p.add_argument("--repo", help="owner/name instead of the one Classifier finds")
    p.set_defaults(fn=cmd_release)
    p = sub.add_parser("catalogue", help="the public page of releasable software")
    p.add_argument("--out", help="write it to this file instead of printing it")
    p.add_argument("--publish", action="store_true",
                   help="republish it to the catalogue repository if it would change")
    p.set_defaults(fn=cmd_catalogue)
    p = sub.add_parser("review"); p.add_argument("name")
    p.add_argument("--import", dest="imp", action="store_true")
    p.add_argument("--accept", action="store_true")
    p.set_defaults(fn=cmd_review)
    a = ap.parse_args(argv)
    if getattr(a, "cmd", None) == "review" and (a.imp or a.accept):
        if a.imp:
            return cmd_review_import(a)
        reg = _reg(); sw = _pick(reg, a.name)
        print(f"Accepted {review.accept(sw)} verdict(s)."); reg.save(); return
    if not getattr(a, "fn", None):
        ap.print_help()
        return
    a.fn(a)
