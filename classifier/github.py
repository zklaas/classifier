"""Where each software is on GitHub, and what its Development Group requires.

Rules (from the Development Groups):
  * Private in some form, found in a PUBLIC repository  -> make the repository private
  * In Development and Private, found in any repository -> remove it:
        the whole repository when the repository is that software,
        otherwise its folder, on every branch that has it
  * Releasable, public, without a passing licence        -> warning
  * Files Excluded from release, found in a public repo  -> warning
  * Stage not set, found in a public repository         -> note

All GitHub access goes through the `gh` CLI, logged in as the user.  Finding
things is read-only; the fixing functions change GitHub and are only called
after the user has agreed.
"""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import model


class GhError(RuntimeError):
    pass


def gh(*args, timeout=120, check=True) -> str:
    exe = model.gh_path()
    if not exe:
        raise GhError("the GitHub CLI `gh` is not installed")
    r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise GhError((r.stderr or r.stdout).strip() or f"gh {' '.join(args)} failed")
    return r.stdout


def list_repos(owner: str) -> list[dict]:
    out = gh("repo", "list", owner, "--limit", "1000", "--json",
             "nameWithOwner,name,visibility,isArchived,url,defaultBranchRef")
    return json.loads(out)


def branches(repo: str) -> list[str]:
    out = gh("api", f"repos/{repo}/branches", "--paginate", "--jq", ".[].name")
    return [b for b in out.splitlines() if b]


def tree(repo: str, branch: str) -> list[str]:
    out = gh("api", f"repos/{repo}/git/trees/{branch}?recursive=1", "--jq",
             '.tree[] | select(.type=="blob") | .path', check=False)
    return [p for p in out.splitlines() if p]


def snapshot(owner: str, progress=None) -> dict:
    """{repo: {visibility, url, archived, branches: {branch: [paths]}}}."""
    snap = {}
    repos = list_repos(owner)
    for i, r in enumerate(repos):
        if progress:
            progress(i, len(repos), r["nameWithOwner"])
        name = r["nameWithOwner"]
        info = {"visibility": r["visibility"], "url": r["url"],
                "archived": r.get("isArchived", False), "branches": {}}
        for b in branches(name):
            info["branches"][b] = tree(name, b)
        snap[name] = info
    return snap


def _local_remote(path: Path) -> tuple[str, str] | None:
    """(owner/repo, folder inside it) when `path` is in a git working tree with a
    github.com origin."""
    try:
        top = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=20)
        if top.returncode != 0:
            return None
        url = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if "github.com" not in url:
        return None
    repo = url.split("github.com")[-1].lstrip(":/").removesuffix(".git")
    sub = Path(path).resolve().relative_to(Path(top.stdout.strip()).resolve()).as_posix()
    return repo, "" if sub == "." else sub


def _local_names(path: Path) -> set[str]:
    try:
        return {p.name for p in Path(path).iterdir() if p.is_file()}
    except OSError:
        return set()


def locate(sw: dict, snap: dict) -> list[dict]:
    """Every place in the snapshot that holds this software:
    [{'repo', 'path', 'branches', 'visibility', 'how'}]."""
    found = {}
    path = Path(sw["path"])
    local = _local_names(path)
    base = path.name.lower()

    def put(repo, sub, how):
        info = snap.get(repo)
        if not info:
            return
        prefix = (sub.rstrip("/") + "/") if sub else ""
        brs = [b for b, files in info["branches"].items()
               if any(f.startswith(prefix) for f in files)] if prefix else list(info["branches"])
        if brs and (repo, sub) not in found:
            found[(repo, sub)] = {"repo": repo, "path": sub, "branches": brs,
                                  "visibility": info["visibility"], "how": how}

    for link in sw.get("repos", []):
        put(link["repo"], link.get("path", ""), "linked by hand")
    lr = _local_remote(path)
    if lr:
        put(lr[0], lr[1], "the folder's git remote")
    for repo, info in snap.items():
        files = {f for fs in info["branches"].values() for f in fs}
        tops = {f.split("/")[0] for f in files if "/" in f}
        for t in tops:
            if t.lower() == base:
                inside = {f.split("/", 1)[1] for f in files if f.startswith(t + "/") and
                          f.count("/") == 1}
                if inside & local:
                    put(repo, t, f"a folder named {t}/ with the same files")
        if repo.split("/")[1].lower() == base:
            roots = {f for f in files if "/" not in f}
            if roots & local - {"README.md", "LICENSE", ".gitignore"}:
                put(repo, "", "a repository of the same name with the same files")
    return list(found.values())


def _excluded_patterns(sw: dict) -> list[str]:
    """What must not be published, by the same rules Commit and push uses (a
    choice made for one file overrides its group's; Claude's review items count)."""
    from pathlib import Path
    from . import publish
    if Path(sw["path"]).is_dir():
        return [rel for rel, _ in publish.plan(sw)["excluded"]]
    pats = []
    keep = {f["paths"][0] for f in model.findings(sw)
            if f["key"].startswith(("file:", "notice:", "vendored:")) and len(f["paths"]) == 1
            and model.resolution_of(sw, f).get("kind") not in ("", "excluded")}
    for f in model.findings(sw):
        if model.resolution_of(sw, f).get("kind") == "excluded":
            pats += [p for p in f["paths"] if p not in keep]
    return pats


def audit(reg: model.Registry, snap: dict, statuses: dict) -> list[dict]:
    """Issues: {'level': action|warn|note, 'action': make_private|delete_repo|
    remove_path|None, 'repo', 'path', 'branches', 'software', 'message'}.

    `statuses` maps software name -> {'group', 'cleared', 'licence'}."""
    issues = []
    per_repo_software = {}
    placements = {}
    for name, sw in reg.software.items():
        locs = locate(sw, snap)
        placements[name] = locs
        for loc in locs:
            per_repo_software.setdefault(loc["repo"], set()).add(name)

    made_private = set()
    for name, sw in sorted(reg.software.items()):
        st = statuses.get(name, {})
        grp = st.get("group", model.UNCLASSIFIED)
        for loc in placements[name]:
            repo, public = loc["repo"], loc["visibility"] == "PUBLIC"
            where = f"{repo}" + (f" (folder {loc['path']}/)" if loc["path"] else "")
            base = {"repo": repo, "path": loc["path"], "branches": loc["branches"],
                    "software": name}
            if grp == model.DEV_PRIVATE:
                others = per_repo_software.get(repo, set()) - {name}
                if loc["path"] == "" and not others:
                    issues.append({**base, "level": "action", "action": "delete_repo",
                                   "message": f"{name} is In Development and Private but is on "
                                              f"GitHub as {repo}. The repository should be deleted "
                                              "(a full backup is cloned first)."})
                elif loc["path"]:
                    issues.append({**base, "level": "action", "action": "remove_path",
                                   "message": f"{name} is In Development and Private but is in "
                                              f"{where} on branch(es) {', '.join(loc['branches'])}. "
                                              "Its folder should be removed from the repository."})
                else:
                    issues.append({**base, "level": "warn", "action": None,
                                   "message": f"{name} is In Development and Private and fills "
                                              f"{repo}, which also holds {', '.join(sorted(others))}. "
                                              "Remove it by hand."})
            if model.is_private(grp) and public and repo not in made_private:
                made_private.add(repo)
                issues.append({**base, "level": "action", "action": "make_private",
                               "message": f"{name} is {grp} but {repo} is PUBLIC. "
                                          "The repository should be made private."})
            if grp in model.RELEASABLE_GROUPS and public and st.get("licence") != "ok":
                issues.append({**base, "level": "warn", "action": None,
                               "message": f"{name} is public in {where} but its licence does not "
                                          "pass yet (open-source + AI disclosure)."})
            if grp == model.UNCLASSIFIED and public:
                issues.append({**base, "level": "note", "action": None,
                               "message": f"{name} is public in {where} but has no stage yet; "
                                          "classify it so Classifier can check it."})
            if public:
                prefix = (loc["path"] + "/") if loc["path"] else ""
                pats = _excluded_patterns(sw)
                if pats:
                    for b in loc["branches"]:
                        files = snap[repo]["branches"][b]
                        hits = sorted({p for p in pats for f in files
                                       if f == prefix + p.rstrip("/") or
                                       f.startswith(prefix + p.rstrip("/") + "/") or
                                       fnmatch.fnmatch(f, prefix + p)})
                        if hits:
                            issues.append({**base, "level": "warn", "action": None,
                                           "message": f"{name}: files marked Excluded from release "
                                                      f"are published in {repo} ({b}): "
                                                      + ", ".join(hits[:6])
                                                      + (" ..." if len(hits) > 6 else "")})
    order = {"action": 0, "warn": 1, "note": 2}
    issues.sort(key=lambda i: (order[i["level"]], i["software"]))
    return issues, placements


# --- fixes (these change GitHub) -------------------------------------------

def backup(repo: str) -> Path:
    dest = model.DATA_DIR / "backups"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{repo.replace('/', '__')}-{time.strftime('%Y%m%d-%H%M%S')}.git"
    r = subprocess.run(["git", "clone", "--mirror", "--quiet",
                        f"https://github.com/{repo}.git", str(target)],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise GhError(f"backup of {repo} failed: {r.stderr.strip()}")
    return target


def make_public(repo: str) -> str:
    gh("repo", "edit", repo, "--visibility", "public",
       "--accept-visibility-change-consequences")
    return f"{repo} is now public."


def make_private(repo: str) -> str:
    gh("repo", "edit", repo, "--visibility", "private",
       "--accept-visibility-change-consequences")
    return f"{repo} is now private."


def delete_repo(repo: str) -> str:
    saved = backup(repo)
    try:
        gh("repo", "delete", repo, "--yes")
    except GhError as e:
        if "delete_repo" in str(e) or "403" in str(e) or "admin" in str(e).lower():
            raise GhError(f"{e}\n\nThe gh login needs permission to delete repositories. "
                          "Run this once in a terminal, then try again:\n"
                          "    gh auth refresh -h github.com -s delete_repo\n"
                          f"(A backup was already saved at {saved}.)") from None
        raise
    return f"Deleted {repo}. Backup (every branch and tag): {saved}"


def remove_path(repo: str, path: str, branch_list: list[str], software: str) -> str:
    saved = backup(repo)
    work = Path(tempfile.mkdtemp(prefix="classifier-"))
    try:
        def git(*a):
            r = subprocess.run(["git", "-C", str(work / "r"), *a], capture_output=True,
                               text=True, timeout=600)
            if r.returncode != 0:
                raise GhError(f"git {' '.join(a)}: {r.stderr.strip()}")
            return r.stdout
        r = subprocess.run(["git", "clone", "--quiet", f"https://github.com/{repo}.git",
                            str(work / "r")], capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise GhError(f"clone failed: {r.stderr.strip()}")
        login = model.gh_login() or "classifier"
        done = []
        for b in branch_list:
            git("checkout", "--quiet", b)
            if not (work / "r" / path).exists():
                continue
            git("rm", "-r", "--quiet", "--", path)
            git("-c", f"user.name={login}", "-c",
                f"user.email={login}@users.noreply.github.com", "commit", "--quiet", "-m",
                f"Remove {path}/: {software} is In Development and Private\n\n"
                "Removed by Classifier.")
            git("push", "--quiet", "origin", b)
            done.append(b)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return (f"Removed {path}/ from {repo} on {', '.join(done) or 'no branch'}. "
            f"It is still in the repository's history; backup at {saved}.")


def apply(issue: dict) -> str:
    a = issue["action"]
    if a == "make_private":
        return make_private(issue["repo"])
    if a == "delete_repo":
        return delete_repo(issue["repo"])
    if a == "remove_path":
        return remove_path(issue["repo"], issue["path"], issue["branches"], issue["software"])
    raise ValueError(f"no action for {issue}")
