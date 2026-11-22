"""Commit and push a releasable software to GitHub.

What is published follows the rest of Classifier:

  * Releasable software (Cleared, with a passing licence) goes to a public or a
    private repository.  Private software (Finished but Private, Modular Suite
    but Private) goes ONLY to a private repository: a new one is created private,
    and an existing public one is refused.  In Development and Private software is
    never pushed: it must stay off GitHub.
  * Provenance: every file resolved "Excluded from release" stays out.  A
    choice made for one file overrides the choice for a group it belongs to.
    Caches, Classifier's own review folder, secret-looking files and files over
    GitHub's 100 MB limit are always left out.  The exclusions are written into
    the published .gitignore.
  * Licence: LICENSE and THIRD-PARTY.md go with it.
  * Platforms: PLATFORMS.md gives install steps for the platforms whose track is
    Supported; a branch named for a supported platform (e.g. windows) can be
    brought up to date by merging, and is left alone when the merge conflicts.

The work happens in a temporary clone; the software's own folder is not changed.
Files already on GitHub that are not in the folder are kept unless asked
otherwise, except excluded ones, which are removed (they stay in the history).
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import catalogue, github, licences, model, platforms, scanner

GITHUB_LIMIT = 100 * 1024 * 1024
ALWAYS_OUT_DIRS = {".git", "__pycache__", ".venv", "venv", "env", "node_modules",
                   ".mypy_cache", ".pytest_cache", ".classifier-review", ".clearance-review",
                   ".idea", ".vscode", ".tox", "site-packages"}
ALWAYS_OUT_FILES = ["*.pyc", "*.pyo", ".DS_Store", "Thumbs.db", "*.swp", "*~"]
SECRET_FILES = [".env", ".env.*", "*.env", "*.pem", "*.key", "id_rsa*", "id_ed25519*",
                "credentials*.json", ".credentials.json", "*.p12", "*.pfx", ".netrc",
                ".pypirc", ".npmrc", "secrets.*", "token*.txt"]
CHECKS_FOR = "Publish to GitHub"


class PublishError(RuntimeError):
    pass


# --- what goes in --------------------------------------------------------------

def _matches(rel: str, pattern: str) -> bool:
    """A provenance path: exact file, folder prefix, glob, or a bare folder name
    matching any folder level (e.g. "__pycache__")."""
    pattern = pattern.rstrip("/")
    if not pattern:
        return False
    if rel == pattern or rel.startswith(pattern + "/"):
        return True
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(rel, pattern)
    if "/" not in pattern:
        return pattern in rel.split("/")[:-1]
    return False


def _file_level_keys(sw: dict) -> dict[str, dict]:
    """Resolutions made for one named file (file:..., notice:..., vendored:<file>)."""
    out = {}
    for f in model.findings(sw):
        if f["key"].startswith(("file:", "notice:", "vendored:")) and len(f["paths"]) == 1:
            out[f["paths"][0]] = model.resolution_of(sw, f)
    return out


def plan(sw: dict) -> dict:
    """{'include': [rel], 'excluded': [(rel, why)], 'always_out': [(rel, why)]}."""
    root = Path(sw["path"])
    single = _file_level_keys(sw)
    excl_patterns = []                       # (pattern, finding title)
    for f in model.findings(sw):
        r = model.resolution_of(sw, f)
        if r.get("kind") == "excluded":
            for p in f["paths"]:
                excl_patterns.append((p, f["title"]))
    include, excluded, always = [], [], []
    seen_dirs_out = set()
    ignored = _git_ignored(root)
    for rel, p in _all_files(root):
        parts = rel.split("/")
        bad_dir = next((d for d in parts[:-1] if d in ALWAYS_OUT_DIRS), None)
        if bad_dir:
            key = "/".join(parts[:parts.index(bad_dir) + 1]) + "/"
            if key not in seen_dirs_out:
                seen_dirs_out.add(key)
                always.append((key, "cache, environment or tool folder"))
            continue
        name = parts[-1]
        if rel in ignored:
            always.append((rel, "ignored by the folder's own .gitignore"))
            continue
        if any(fnmatch.fnmatch(name, g) for g in ALWAYS_OUT_FILES):
            always.append((rel, "compiled or temporary file"))
            continue
        if any(fnmatch.fnmatch(name, g) for g in SECRET_FILES):
            always.append((rel, "looks like a password, key or token file"))
            continue
        try:
            if p.stat().st_size > GITHUB_LIMIT:
                always.append((rel, "over GitHub's 100 MB file limit"))
                continue
        except OSError:
            continue
        own = single.get(rel)
        if own and own.get("kind") and own.get("kind") != "excluded":
            include.append(rel)              # a choice for this very file wins
            continue
        hit = next((why for pat, why in excl_patterns if _matches(rel, pat)), None)
        if hit or (own and own.get("kind") == "excluded"):
            excluded.append((rel, hit or "marked Excluded"))
            continue
        include.append(rel)
    return {"include": include, "excluded": excluded, "always_out": always}


def _git_ignored(root: Path) -> set[str]:
    """Files the folder's own .gitignore keeps out (when it is a git working
    tree): they are outside what the scan saw, so they are not published either."""
    if not (Path(root) / ".git").exists():
        return set()
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--others", "--ignored",
                        "--exclude-standard"], capture_output=True)
    if r.returncode != 0:
        return set()
    return {x for x in r.stdout.decode("utf-8", "replace").split("\0") if x}


def _all_files(root: Path):
    """Every file in the folder (not git's view: publishing decides for itself)."""
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            yield p.relative_to(root).as_posix(), p


# --- where it goes -------------------------------------------------------------

def destination(sw: dict, placements: dict | None, owner: str) -> dict:
    """{'repo', 'path', 'exists', 'visibility', 'branches', 'how'}: the software's
    own repository, its suite's repository as a folder, or a new repository."""
    locs = (placements or {}).get(sw["name"]) or []
    if locs:
        own = [l for l in locs if not l["path"]]
        loc = own[0] if own else locs[0]
        return {"repo": loc["repo"], "path": loc["path"], "exists": True,
                "visibility": loc["visibility"], "branches": loc["branches"],
                "how": loc["how"]}
    suite = sw.get("suite", "").strip()
    if suite:
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", suite).strip("-").lower()
        return {"repo": f"{owner}/{name}", "path": Path(sw["path"]).name, "exists": None,
                "visibility": "", "branches": [], "how": f"the {suite} suite's repository"}
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(sw["path"]).name).strip("-").lower()
    return {"repo": f"{owner}/{name}", "path": "", "exists": None, "visibility": "",
            "branches": [], "how": "a repository named after the software"}


def platform_branches(sw: dict, branches: list[str], default: str) -> list[tuple[str, str]]:
    """(branch, platform) for branches named after a platform whose track is Supported."""
    out = []
    tracks = sw.get("platforms", {})
    for b in branches:
        if b == default:
            continue
        for plat, rx in platforms.PLATFORM_BRANCH.items():
            if rx.search(b) and tracks.get(plat, {}).get("track") in ("supported", "untested"):
                out.append((b, plat))
                break
    return out


def platforms_md(sw: dict, dest: dict, settings: dict | None = None) -> str:
    tracks = sw.get("platforms", {})
    sup = [p for p in platforms.PLATFORMS
           if tracks.get(p, {}).get("track") in ("supported", "untested")]
    if not sup:
        return ""
    loc = {"repo": dest["repo"], "path": dest["path"], "branches": dest.get("branches", []),
           "visibility": "PUBLIC"}
    out = [f"# Installing {sw['name']}", ""]
    for p in sup:
        steps = catalogue.install_steps(sw, p, loc)
        if not steps:
            continue
        out += [f"## {p}", ""]
        if catalogue.where_to_type(p) and not steps[0].startswith("Open "):
            out += [catalogue.where_to_type(p), ""]
        out += ["```"] + steps + ["```", ""]
        if steps[0].startswith("git clone"):
            out += [catalogue.without_git(p, dest["repo"]), ""]
        guide = {"Windows": "WINDOWS.md", "macOS": "MACOS.md"}.get(p)
        if guide and (Path(sw["path"]) / guide).exists():
            out += [f"More about {p}: [{guide}]({guide}).", ""]
    if sw.get("install_notes"):
        out += [sw["install_notes"], ""]
    contact = catalogue.contact_line(settings or {})
    if contact:
        out += [contact, ""]
    return "\n".join(out)


# --- checks --------------------------------------------------------------------

def must_be_private(st: dict) -> bool:
    return st.get("group") in model.PRIVATE_GROUPS


def blockers(sw: dict, st: dict, dest: dict | None = None) -> list[str]:
    """Reasons this software may not be pushed to `dest` (empty = it may)."""
    out = []
    g = st.get("group")
    if g == model.DEV_PRIVATE:
        out.append("It is In Development and Private, which must stay off GitHub.")
    elif g == model.UNCLASSIFIED:
        out.append("It is not classified yet: set its stage on the Classification tab.")
    elif g in model.RELEASABLE_GROUPS:
        if not st.get("cleared"):
            out.append(f"It is not Cleared ({st.get('reason')}).")
        if st.get("licence") != "ok":
            out.append("Its licence does not pass yet (Licence tab: Write LICENSE).")
    if must_be_private(st) and dest and dest.get("exists") and \
            dest.get("visibility") == "PUBLIC":
        out.append(f"It is {g}, but {dest['repo']} is public. Private software may only "
                   "go to a private repository: make it private first (Check GitHub… "
                   "does this), or choose another repository.")
    if not Path(sw["path"]).is_dir():
        out.append("Its folder is missing.")
    return out


# --- doing it ------------------------------------------------------------------

def _git(cwd: Path, *args, check=True, timeout=600):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                       timeout=timeout)
    if check and r.returncode != 0:
        raise PublishError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def publish(sw: dict, settings: dict, dest: dict, *, visibility: str = "private",
            private_only: bool = False,
            remove_remote_only: bool = False, merge_branches: list[str] = (),
            message: str = "", remote_url: str | None = None, progress=print,
            sync_folder: bool = False, make_public: bool = False) -> str:
    """Do it.  `remote_url` overrides https://github.com/<repo>.git (tests use a
    local bare repository; then nothing is created on GitHub)."""
    pl = plan(sw)
    root = Path(sw["path"])
    url = remote_url or f"https://github.com/{dest['repo']}.git"
    work = Path(tempfile.mkdtemp(prefix="classifier-publish-"))
    clone = work / "repo"
    login = (model.gh_login() if not remote_url else "") or "classifier"
    ident = ["-c", f"user.name={login}", "-c", f"user.email={login}@users.noreply.github.com"]
    try:
        if private_only:
            visibility = "private"
        exists = dest.get("exists")
        if exists is None and not remote_url:
            exists = _repo_exists(dest["repo"])
        if exists and private_only and not remote_url:
            vis = json.loads(github.gh("repo", "view", dest["repo"], "--json",
                                       "visibility")).get("visibility", "")
            if vis != "PRIVATE":
                raise PublishError(f"{dest['repo']} is {vis.lower() or 'not private'}; "
                                   f"{sw['name']} is Private software and may only go to "
                                   "a private repository.")
        if not exists and not remote_url:
            progress(f"Creating {visibility} repository {dest['repo']}…")
            github.gh("repo", "create", dest["repo"], f"--{visibility}",
                      "--description", sw.get("public_summary", "")[:300] or sw["name"])
        progress("Fetching the repository…")
        r = subprocess.run(["git", "clone", "--quiet", "--no-single-branch", url, str(clone)],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise PublishError(f"clone failed: {r.stderr.strip()}")
        empty = not _git(clone, "branch", "-r").strip()
        if empty:
            _git(clone, "checkout", "--quiet", "-b", "main")
            default = "main"
        else:
            default = _git(clone, "symbolic-ref", "--short", "refs/remotes/origin/HEAD",
                           check=False).strip().removeprefix("origin/") or "main"
            _git(clone, "checkout", "--quiet", default)
        target = clone / dest["path"] if dest["path"] else clone
        target.mkdir(parents=True, exist_ok=True)

        progress("Copying the files…")
        wanted = set(pl["include"])
        # remove what must not be there: excluded files, and (if asked) others
        excluded = {rel for rel, _ in pl["excluded"]}
        for p in sorted(target.rglob("*"), reverse=True):
            if ".git" in p.relative_to(clone).parts[:1]:
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(target).as_posix()
            if rel.startswith(".git/") or rel == ".git":
                continue
            if rel in wanted or rel in {"LICENSE", "THIRD-PARTY.md", "PLATFORMS.md",
                                        ".gitignore"}:
                continue
            is_excl = rel in excluded or any(_matches(rel, r) for r, _ in pl["always_out"])
            if is_excl or remove_remote_only:
                p.unlink()
        for rel in pl["include"]:
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, dst)
        md = platforms_md(sw, dest, settings)
        if md:
            (target / "PLATFORMS.md").write_text(md, encoding="utf-8")
        tp = licences.third_party(sw)
        if tp:
            (target / "THIRD-PARTY.md").write_text(tp, encoding="utf-8")
        _write_gitignore(target, pl)

        _git(clone, "add", "-A")
        notes = []
        done = []
        if not _git(clone, "status", "--porcelain").strip():
            notes.append(f"Nothing new to commit: {dest['repo']} already matches {sw['name']}.")
            merge_branches = []
        else:
            msg = message or default_message(sw, pl, settings)
            _git(clone, *ident, "commit", "--quiet", "-m", msg)
            progress(f"Pushing to {dest['repo']} ({default})…")
            _git(clone, "push", "--quiet", "origin", f"HEAD:{default}", timeout=1800)
            done.append(default)
        for b in merge_branches:
            progress(f"Merging into {b}…")
            _git(clone, "checkout", "--quiet", b)
            m = subprocess.run(["git", "-C", str(clone), *ident, "merge", "--no-edit",
                                "--quiet", default], capture_output=True, text=True)
            if m.returncode != 0:
                _git(clone, "merge", "--abort", check=False)
                notes.append(f"{b}: the merge conflicts, so it was left as it was; merge "
                             f"{default} into {b} by hand.")
                continue
            _git(clone, "push", "--quiet", "origin", b)
            done.append(b)
        where = f"https://github.com/{dest['repo']}" + (f"/tree/{default}/{dest['path']}"
                                                        if dest["path"] else "")
        text = (f"Published {sw['name']} to {where} ({', '.join(done)}): "
                f"{len(pl['include'])} files, {len(pl['excluded'])} excluded by Provenance."
                if done else "")
        if make_public:
            _git(clone, "checkout", "--quiet", default)
            exposed = history_exposes(clone, dest, pl)
            if exposed:
                notes.append("NOT made public: the repository's history still holds files "
                             "that must not be public: " + ", ".join(exposed[:8])
                             + (" …" if len(exposed) > 8 else "") + ". Keep it private, or "
                             "publish to a new repository (its history starts clean).")
            elif not remote_url:
                progress(f"Making {dest['repo']} public…")
                notes.append(github.make_public(dest["repo"]))
        if sync_folder:
            if local_clone_of(root, dest):
                progress("Bringing the commit into your folder…")
                notes.append(sync_local(root, default))
            else:
                notes.append("Your folder is not a git copy of this repository, so it was "
                              "left as it is.")
        elif (root / ".git").exists() and done and local_clone_of(root, dest):
            notes.append("Your folder is a git copy of this repository and is now behind it: "
                         "publish again with 'Bring the new commit into your folder' ticked, "
                         "or run `git pull` in it.")
        return "\n".join(x for x in [text] + notes if x)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def local_clone_of(root: Path, dest: dict) -> bool:
    """True when the software's folder is the top of a git working tree whose
    origin is the destination repository (and the software is its whole tree)."""
    if dest.get("path") or not (Path(root) / ".git").exists():
        return False
    url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                         capture_output=True, text=True).stdout.strip()
    if url.startswith(("/", "file:")):          # a local practice repository (tests)
        return True
    repo = url.split("github.com")[-1].lstrip(":/").removesuffix(".git")
    return repo.lower() == dest["repo"].lower()


def sync_local(root: Path, branch: str) -> str:
    """Bring the published commit into the software's own git copy, like a pull
    that never touches a change you made.

    The folder's branch is moved to the published commit (git reset --mixed:
    the index changes, the files do not).  Then, as `git pull` would, files the
    commit has but the folder lacks are copied in, and files the folder has not
    changed since its previous commit are updated to the published version
    (e.g. PLATFORMS.md / THIRD-PARTY.md that publishing regenerated); the
    published .gitignore replaces the folder's.  Files you changed are left as
    they are, and nothing is ever deleted.  The previous commit stays reachable
    through `git reflog`."""
    root = Path(root)
    _git(root, "fetch", "--quiet", "origin", timeout=600)
    cur = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if cur != branch:
        return (f"Your folder is on branch {cur}, not {branch}, so it was left as it is; "
                f"switch to {branch} and publish again to bring it up to date.")
    old_full = _git(root, "rev-parse", "HEAD").strip()
    old = old_full[:7]
    _git(root, "reset", "--quiet", "--mixed", f"origin/{branch}")
    missing, modified = [], []
    for ln in _git(root, "status", "--porcelain", "-z").split("\0"):
        if ln.startswith(" D "):
            missing.append(ln[3:])
        elif ln.startswith(" M "):
            modified.append(ln[3:])
    unchanged = [rel for rel in modified if _same_as(root, old_full, rel)]
    take = set(missing) | set(unchanged) | {".gitignore"}
    for rel in sorted(take):
        _git(root, "checkout", f"origin/{branch}", "--", rel, check=False)
    left = [ln for ln in _git(root, "status", "--porcelain").splitlines()]
    new = _git(root, "rev-parse", "--short", "HEAD").strip()
    msg = f"Your folder's git copy is now at the published commit {new} (it was at {old})."
    if left:
        msg += (f" {len(left)} file(s) in the folder differ from it (your own changes, or "
                "files kept out of the release); those were left as they are.")
    return msg


def _same_as(root: Path, commit: str, rel: str) -> bool:
    """True when the folder's file is exactly the one in `commit` (so it was not
    changed by hand since then)."""
    was = _git(root, "rev-parse", "--verify", "--quiet", f"{commit}:{rel}",
               check=False).strip()
    now = _git(root, "hash-object", "--", rel, check=False).strip()
    return bool(was) and was == now


def history_exposes(clone: Path, dest: dict, pl: dict) -> list[str]:
    """Paths anywhere in the repository's history that must not become public:
    files Provenance excludes, and files that look like passwords or keys."""
    out = _git(clone, "log", "--all", "--name-only", "--format=", timeout=600)
    prefix = (dest["path"].rstrip("/") + "/") if dest.get("path") else ""
    excl = [r for r, _ in pl["excluded"]]
    bad = set()
    for path in {ln.strip() for ln in out.splitlines() if ln.strip()}:
        if prefix and not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        name = rel.split("/")[-1]
        if any(_matches(rel, e) for e in excl) or \
                any(fnmatch.fnmatch(name, g) for g in SECRET_FILES):
            bad.add(path)
    return sorted(bad)


def other_private_software(reg, statuses: dict, placements: dict, repo: str,
                           me: str) -> list[str]:
    """Software in a Private group that also lives in `repo`."""
    return sorted(n for n, locs in placements.items()
                  if n != me and any(l["repo"] == repo for l in locs)
                  and statuses.get(n, {}).get("group") in model.PRIVATE_GROUPS)


def _repo_exists(repo: str) -> bool:
    try:
        github.gh("repo", "view", repo, "--json", "name")
        return True
    except github.GhError:
        return False


def _write_gitignore(target: Path, pl: dict):
    gi = target / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    lines = [ln.strip() for ln in text.splitlines()]
    add = ["__pycache__/", "*.pyc", ".classifier-review/"]
    add += sorted({rel for rel, _ in pl["excluded"]})
    new = [a for a in add if a not in lines]
    if new:
        block = "\n# Kept out of releases by Classifier (provenance)\n" + "\n".join(new) + "\n"
        gi.write_text(text + ("" if text.endswith("\n") or not text else "\n") + block,
                      encoding="utf-8")


def remote_only(dest: dict, snap: dict | None, pl: dict) -> list[str]:
    """Files on GitHub (default branch) under the destination that are not in the
    folder and not excluded: kept unless the person asks to remove them."""
    info = (snap or {}).get(dest["repo"])
    if not info or not info["branches"]:
        return []
    files = next(iter(info["branches"].values())) if "main" not in info["branches"] \
        else info["branches"]["main"]
    prefix = (dest["path"].rstrip("/") + "/") if dest["path"] else ""
    have = set(pl["include"]) | {r for r, _ in pl["excluded"]} | {
        "LICENSE", "THIRD-PARTY.md", "PLATFORMS.md", ".gitignore"}
    return sorted(f[len(prefix):] for f in files
                  if f.startswith(prefix) and f[len(prefix):] not in have)


def default_message(sw: dict, pl: dict, settings: dict) -> str:
    st_lic = licences.check(Path(sw["path"]), settings.get("ai_system", "Claude"))
    lines = [f"Publish {sw['name']}", "",
             (f"Licence: {st_lic['spdx']} (with AI disclosure)." if st_lic.get("status") == "ok"
              else "No licence: private software, not released."),
             f"Files: {len(pl['include'])}."]
    if pl["excluded"]:
        lines.append(f"Excluded from release (provenance): {len(pl['excluded'])} file(s).")
    lines += ["", "Published by Classifier."]
    return "\n".join(lines)
