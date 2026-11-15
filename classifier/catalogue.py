"""The public catalogue: one page describing every releasable software and how
to install it on each supported operating system.

Only software that is publicly published is listed: in a Releasable group AND
in a public GitHub repository right now.  Private software never appears.
The page is Markdown, meant for a GitHub profile README (a public repository
named after the account, e.g. owner/owner, which GitHub shows on the profile
page) or any other public repository.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from . import aiuse, model, platforms

# import name -> pip name, where they differ
PIP_NAME = {"PIL": "Pillow", "cv2": "opencv-python", "yaml": "PyYAML", "osgeo": "GDAL",
            "sklearn": "scikit-learn", "bs4": "beautifulsoup4", "docx": "python-docx",
            "pptx": "python-pptx", "rtmidi": "python-rtmidi", "fontTools": "fonttools",
            "imageio_ffmpeg": "imageio-ffmpeg", "send2trash": "Send2Trash",
            "pystac_client": "pystac-client", "rio_cogeo": "rio-cogeo",
            "rio_tiler": "rio-tiler", "planetary_computer": "planetary-computer"}


DOC_NAMES = ("README.md", "README.txt", "README", "MANUAL.md")


def readme_summary(path: Path, limit: int = 420) -> str:
    """The first plain paragraph of the software's README (or MANUAL), cut at a
    sentence end when it is long."""
    for name in DOC_NAMES:
        p = Path(path) / name
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            for para in re.split(r"\n\s*\n", text):
                para = para.strip()
                if para and not para.startswith(("#", "```", "|", "!", "<", "-", "*")):
                    para = re.sub(r"\s+", " ", para)
                    if len(para) <= limit:
                        return para
                    cut = [m.end() for m in re.finditer(r"[.!?](\s|$)", para[:limit])]
                    return para[:cut[-1]].strip() if cut else para[:limit].rsplit(" ", 1)[0] + "…"
    return ""


def display_name(sw: dict) -> str:
    """The README's own title (e.g. 'ChumpMan 11D'), else the registered name."""
    for name in DOC_NAMES:
        p = Path(sw["path"]) / name
        if p.is_file():
            m = re.search(r"^#\s+(.+?)\s*$", p.read_text(encoding="utf-8", errors="replace"), re.M)
            if m and len(m.group(1)) <= 60:
                return m.group(1).strip()
    return sw["name"]


def _online_link(sw: dict) -> str:
    """A GitHub Pages address the software's own README links to (a web version)."""
    for name in DOC_NAMES:
        p = Path(sw["path"]) / name
        if p.is_file():
            m = re.search(r"https://[\w-]+\.github\.io/[\w./-]*",
                          p.read_text(encoding="utf-8", errors="replace"))
            if m:
                return m.group(0)
    return ""


def runtime_deps(sw: dict) -> list[str]:
    """Python packages the program itself needs: leave out those imported only by
    build tools (make_*/gen_*/build_* scripts) and tests."""
    tool = re.compile(r"(^|/)(make|gen|generate|build|render|draw)[_-][^/]*\.py$|(^|/)_?test")
    out = []
    for f in (sw.get("scan") or {}).get("findings", []):
        if f["key"].startswith("dep:py:"):
            if f["paths"] and all(tool.search(p) for p in f["paths"]):
                continue
            out.append(f["key"][len("dep:py:"):])
    return sorted(out) or list((sw.get("scan") or {}).get("python_deps", []))


def listed(reg: model.Registry, statuses: dict, placements: dict | None = None) -> list[dict]:
    """Releasable software that is in a public repository.  Without `placements`
    (no GitHub check) nothing can be shown to be public, so nothing is listed."""
    out = []
    for n, sw in sorted(reg.software.items(), key=lambda x: x[0].lower()):
        if statuses.get(n, {}).get("group") not in model.RELEASABLE_GROUPS:
            continue
        if any(l.get("visibility") == "PUBLIC" for l in (placements or {}).get(n, [])):
            out.append(sw)
    return out


def _where(sw: dict, placements: dict | None) -> dict | None:
    locs = (placements or {}).get(sw["name"])
    if locs is None:
        locs = [{"repo": l["repo"], "branches": l["branches"], "path": ""}
                for l in (sw.get("platform_review") or {}).get("placements", [])]
    public = [l for l in locs if l.get("visibility", "PUBLIC") == "PUBLIC"]
    return public[0] if public else (locs[0] if locs else None)


WHERE = {
    "Linux": "Type these into a **terminal** (in most file managers: right-click inside a "
             "folder > *Open in Terminal*).",
    "Windows": "Type these into **PowerShell**: open the Start menu, type *PowerShell* and "
               "press Enter (Windows 11's *Terminal* opens PowerShell too; the Command Prompt "
               "also works). The installer needs Python first: "
               "`winget install --id Python.Python.3.13 -e --scope user`.",
    "macOS": "Type these into **Terminal** (Applications > Utilities > Terminal, or press "
             "⌘-Space and type *Terminal*). The installer needs Python first: "
             "`brew install python@3.13` (https://brew.sh), or the installer from python.org.",
    "OpenBSD": "Type these into a terminal.",
}


def where_to_type(plat: str) -> str:
    return WHERE.get(plat, "")


def without_git(plat: str, repo: str) -> str:
    """How to get the files without git."""
    folder = repo.split("/")[-1] + "-main"
    extra = {"Windows": " (`git` needs Git for Windows: `winget install --id Git.Git -e`)",
             "macOS": " (the first `git` command offers to install Apple's command-line tools: "
                      "accept)"}.get(plat, "")
    there = {"Windows": "open PowerShell there (type *powershell* in File Explorer's address "
                        "bar and press Enter)",
             "macOS": "open Terminal there (type `cd `, drag the folder onto the Terminal "
                      "window, press Return)",
             }.get(plat, "open a terminal there")
    return (f"No git?{extra} On the GitHub page click **Code > Download ZIP** and unpack it "
            f"(the folder is called `{folder}`); {there}, and skip the first two lines above.")


def contact_line(settings: dict) -> str:
    c = (settings or {}).get("contact", "").strip()
    return f"If something doesn't work, please write to {c}." if c else ""


def install_steps(sw: dict, plat: str, loc: dict | None) -> list[str]:
    root = Path(sw["path"])
    deps = [PIP_NAME.get(d, d) for d in runtime_deps(sw)]
    if "PyQt6" in deps and any("QtWebEngine" in p.read_text(errors="replace")
                               for p in root.rglob("*.py") if ".venv" not in p.parts):
        deps.append("PyQt6-WebEngine")
    rev = sw.get("platform_review") or {}
    web = rev.get("web") or False
    sub = loc.get("path", "") if loc else ""
    folder = sub or (loc["repo"].split("/")[1] if loc else root.name)
    branches = loc.get("branches", []) if loc else []
    win_branch = next((b for b in branches if re.search(r"win", b, re.I)), None)
    clone = (f"git clone https://github.com/{loc['repo']}.git"
             + (f" --branch {win_branch}" if plat == "Windows" and win_branch else "")
             if loc else "Download the files")
    cd = f"cd {loc['repo'].split('/')[1]}" + (f"/{sub}" if sub else "") if loc else f"cd {folder}"
    entry = next((p.name for p in sorted(root.glob("*_app.py"))), None) or next(
        (p.name for p in sorted(root.glob("*.py")) if p.stem.lower() == root.name.lower()), None)
    steps = []
    if plat == "Linux":
        steps += [clone, cd]
        if deps:
            steps.append("python3 -m pip install --user " + " ".join(deps)
                         + "   # or your distribution's packages")
        if (root / "install.sh").exists():
            steps.append("bash install.sh")
        elif entry:
            steps.append(f"python3 {entry}")
    elif plat == "Windows":
        steps += [clone, cd]
        if (root / "install.ps1").exists() or win_branch:
            steps.append("powershell -ExecutionPolicy Bypass -File install.ps1")   # installs the packages
        else:
            if deps:
                steps.append("py -m pip install " + " ".join(deps))
            if entry:
                steps.append(f"py {entry}")
    elif plat == "macOS" and (root / "install-macos.sh").exists():
        steps += [clone, cd, "bash install-macos.sh"]                            # installs the packages
    elif plat in ("macOS", "OpenBSD"):
        steps += [clone, cd]
        if deps:
            steps.append("python3 -m pip install --user " + " ".join(deps)
                         + ("   # or pkg_add the py3- ports" if plat == "OpenBSD" else ""))
        if entry:
            steps.append(f"python3 {entry}")
    if web and plat in platforms.PLATFORMS:
        pages = (sw.get("scan") or {}).get("web_pages", [])
        page = next((p for p in pages if "index" not in p), pages[0] if pages else "index.html")
        browser = (f"Open {page} in a web browser"
                   + (f" (from https://github.com/{loc['repo']})" if loc else ""))
        if plat in ("iOS/iPadOS", "Android") or not steps:
            steps = [browser]
        else:
            steps += ["", f"# or, without installing: {browser[0].lower() + browser[1:]}"]
    return steps


def entry(sw: dict, st: dict, placements=None) -> str:
    loc = _where(sw, placements)
    summary = sw.get("public_summary") or readme_summary(Path(sw["path"])) or ""
    tracks = sw.get("platforms", {})
    plats = [p for p in platforms.PLATFORMS
             if tracks.get(p, {}).get("track") in ("supported", "untested")]
    if not plats:
        plats = ["Linux"]
    lic = st.get("licence_info", {}).get("spdx") or "see LICENSE"
    out = [f"### {display_name(sw)}", ""]
    if summary:
        out += [summary, ""]
    meta = [f"**Licence:** {lic}"]
    if loc and loc.get("visibility", "PUBLIC") == "PUBLIC":
        meta.append(f"**Source:** [github.com/{loc['repo']}](https://github.com/{loc['repo']}"
                    + (f"/tree/HEAD/{loc['path']}" if loc.get("path") else "") + ")")
    else:
        meta.append("**Source:** not published yet")
    online = _online_link(sw)
    if online:
        meta.append(f"**Play online:** [{online.split('//', 1)[1].rstrip('/')}]({online})")
    if sw.get("suite"):
        meta.append(f"**Part of:** {sw['suite']}")
    # "Supported, untested" is the licensor's own note; the public page just says where it runs.
    meta.append("**Runs on:** " + ", ".join(plats))
    out += [" · ".join(meta), ""]
    ai_text = aiuse.describe(sw)
    if ai_text:
        out += [f"**AI:** {ai_text}", ""]
    for p in plats:
        steps = install_steps(sw, p, loc)
        if not steps:
            continue
        out += [f"<details><summary>Install on {p}</summary>", ""]
        if where_to_type(p) and not steps[0].startswith("Open "):
            out += [where_to_type(p), ""]
        out += ["```"] + steps + ["```", ""]
        if loc and steps[0].startswith("git clone"):
            out += [without_git(p, loc["repo"]), ""]
        if sw.get("install_notes"):
            out += [sw["install_notes"], ""]
        out += ["</details>", ""]
    return "\n".join(out)


def page(reg: model.Registry, statuses: dict, placements=None) -> str:
    s = reg.settings
    sws = listed(reg, statuses, placements)
    head = [f"# Software by {s.get('licensor') or 'the author'}", "",
            f"Open-source software made by {s.get('licensor') or 'the author'} with "
            f"{s.get('ai_system', 'Claude')}, an artificial intelligence system made by "
            f"{s.get('ai_maker', 'Anthropic')}. Each program carries an open-source "
            "licence with an AI disclosure.", ""]
    if not sws:
        return "\n".join(head + ["*Nothing is publicly released yet.*", ""])
    suites = {}
    for sw in sws:
        suites.setdefault(sw.get("suite") or "", []).append(sw)
    body = []
    for suite in sorted(suites, key=lambda x: (x != "", x)):
        body += [f"## {suite or 'Programs'}", ""]
        for sw in suites[suite]:
            body.append(entry(sw, statuses.get(sw["name"], {}), placements))
    contact = contact_line(s)
    foot = ["---", ""] + ([contact, ""] if contact else []) + [
        "*This page is generated by Classifier from each program's "
        "Development Group, licence and platform tracks.*", ""]
    return "\n".join(head + body + foot)


def publish(repo: str, text: str, create: bool, message="Update the software catalogue") -> str:
    """Write README.md in `repo` (created public first when `create`)."""
    from . import github
    if create:
        github.gh("repo", "create", repo, "--public", "--description",
                  "Software catalogue", "--add-readme")
    sha = ""
    try:
        sha = json.loads(github.gh("api", f"repos/{repo}/contents/README.md")).get("sha", "")
    except github.GhError:
        pass
    args = ["api", "-X", "PUT", f"repos/{repo}/contents/README.md",
            "-f", f"message={message}",
            "-f", "content=" + base64.b64encode(text.encode("utf-8")).decode("ascii")]
    if sha:
        args += ["-f", f"sha={sha}"]
    github.gh(*args)
    return f"https://github.com/{repo}"


def current_text(repo: str) -> str | None:
    """The README.md now in `repo` (None when there is none)."""
    from . import github
    try:
        raw = github.gh("api", f"repos/{repo}/contents/README.md", "--jq", ".content")
    except github.GhError:
        return None
    try:
        return base64.b64decode(raw.strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def auto_enabled(settings: dict) -> bool:
    return bool(settings.get("catalogue_repo")) and settings.get("catalogue_auto", True)


def refresh(reg: model.Registry, progress=lambda m: None, snap: dict | None = None) -> str:
    """Rebuild the catalogue from GitHub as it is now and republish it when the
    page would change.  Reads GitHub afresh unless `snap` (a snapshot taken after
    the last change) is given."""
    from . import github, status
    repo = reg.settings.get("catalogue_repo")
    if not repo:
        return "No catalogue repository yet (Catalogue… > Publish to GitHub… sets one)."
    owner = reg.settings.get("github_owner") or model.gh_login()
    if snap is None:
        progress("Reading GitHub for the catalogue…")
        snap = github.snapshot(owner)
    statuses = {n: status.of(sw, reg.settings) for n, sw in reg.software.items()}
    _, placed = github.audit(reg, snap, statuses)
    text = page(reg, statuses, placed)
    if (current_text(repo) or "").strip() == text.strip():
        return f"Catalogue already up to date ({repo})."
    progress("Updating the catalogue…")
    publish(repo, text, create=not repo_exists(repo),
            message="Update the software catalogue (Classifier)")
    return f"Catalogue updated: https://github.com/{repo} ({len(listed(reg, statuses, placed))} software)."


def repo_exists(repo: str) -> bool:
    from . import github
    try:
        github.gh("repo", "view", repo, "--json", "name")
        return True
    except github.GhError:
        return False
