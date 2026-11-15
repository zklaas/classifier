"""Have Claude review a software's provenance, in claude-classifier-docker.

The session opens on the software's folder READ-ONLY.  The only writable place
is the folder's `.classifier-review/` subfolder, which holds the brief Classifier
writes and the `review.json` Claude writes back.  Claude cannot change the
software, reach GitHub, or reach anything but Anthropic.

Claude's verdicts come back as suggestions: Classifier shows them beside each
finding, and only the licensor's "Accept" turns one into a resolution.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import model

DIR = ".classifier-review"
LAUNCHER = "claude-classifier-docker"
SCHEMA = """{
  "reviewed_at": "YYYY-MM-DD HH:MM",
  "summary": "two or three sentences on the software's provenance as a whole",
  "items": [
    {
      "key": "the finding's key exactly as listed in the brief, or file:<path> for a new one",
      "verdict": "self | claude | generated | logic | opensource | excluded | restricted | unsure",
      "licence": "an SPDX id when the verdict is opensource, else empty",
      "reason": "one or two sentences: what you looked at and why you decided this"
    }
  ]
}"""


def launcher() -> str | None:
    return shutil.which(LAUNCHER) or (
        str(Path.home() / ".local/bin" / LAUNCHER)
        if (Path.home() / ".local/bin" / LAUNCHER).exists() else None)


SIGNIN_DIR = model.DATA_DIR / "signin"


def signin_state() -> tuple[str, str]:
    """('ok'|'renews'|'expired'|'none', text) for the Claude sign-in the review
    window uses.  The stored access key lasts hours and Claude renews it when
    used, so a recently expired key is fine ('renews'); one unused for weeks
    may no longer renew ('expired')."""
    import time
    creds = Path.home() / ".local/share" / LAUNCHER / "credentials.json"
    try:
        o = json.loads(creds.read_text(encoding="utf-8")).get("claudeAiOauth", {})
    except (OSError, ValueError):
        return "none", "Claude sign-in: not signed in yet"
    exp = o.get("expiresAt", 0) / 1000
    now = time.time()
    if not exp or exp > now:
        return "ok", "Claude sign-in: OK"
    if o.get("refreshToken") and now - exp < 14 * 86400:
        return "renews", "Claude sign-in: OK (renews itself when used)"
    return "expired", ("Claude sign-in: not used since "
                       + time.strftime("%Y-%m-%d", time.localtime(exp))
                       + "; it may need signing in again")


def signin_expiry() -> float:
    try:
        o = json.loads((Path.home() / ".local/share" / LAUNCHER / "credentials.json")
                       .read_text(encoding="utf-8")).get("claudeAiOauth", {})
        return o.get("expiresAt", 0) / 1000
    except (OSError, ValueError):
        return 0.0


def login_note() -> str:
    state, text = signin_state()
    if state in ("ok", "renews"):
        return ""
    return (f"\n\n{text}. Use Sign in to Claude… first, or type /login in the "
            "Claude window if it says \"Login expired\".")


def signin_launch():
    """Open the Claude window straight on /login, in an empty folder.  The new
    sign-in is saved when that window is closed (shared with claude-docker)."""
    exe = launcher()
    if not exe:
        raise FileNotFoundError(f"{LAUNCHER} is not installed")
    SIGNIN_DIR.mkdir(parents=True, exist_ok=True)
    (SIGNIN_DIR / "prompt.md").write_text("/login\n", encoding="utf-8")
    log = SIGNIN_DIR / "launch.log"
    fh = open(log, "w", encoding="utf-8")
    return subprocess.Popen([exe, "--gui", str(SIGNIN_DIR.resolve()), "--prompt-file",
                             str((SIGNIN_DIR / "prompt.md").resolve())],
                            stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                            start_new_session=True), log


def brief(sw: dict, settings: dict) -> str:
    licensor = settings.get("licensor") or "the licensor"
    ai = settings.get("ai_system", "Claude")
    lines = []
    for f in model.findings(sw):
        r = model.resolution_of(sw, f)
        state = (f"currently: {model.RESOLUTIONS.get(r.get('kind', ''), '?')}"
                 + (f" {r.get('licence')}" if r.get("licence") else "")
                 + (" (automatic guess)" if r.get("by") == "auto" else ""))
        paths = ", ".join(f["paths"][:12]) + (" ..." if len(f["paths"]) > 12 else "")
        lines.append(f"- key `{f['key']}`: {f['title']}. {f['detail']} Files: {paths}. "
                     f"[{state}]")
    findings = "\n".join(lines) or "- (the scan found nothing to ask about)"
    return f"""# Provenance review for {sw['name']}

You are helping Classifier decide whether **{sw['name']}** can be *Cleared* for an
open-source licence. Software is Cleared only when everything in it was written by
{licensor} (the licensor) or by {ai}, follows plainly from the programming
language's own logic, or comes from open-source sources under an open licence.
Anything else (copied code under no licence or a closed one, images or data under
non-open terms, material whose origin cannot be told) blocks clearance unless it
is kept out of the release.

The software's folder is /workspace, read-only. You may write ONLY in
/workspace/{DIR}/. You cannot reach the internet beyond Anthropic, so judge from
what is in the files (headers, comments, credits, READMEs, style, embedded data)
and from what {licensor} tells you.

## What Classifier's scan found

{findings}

## Your job

1. Tell {licensor} in one short message what you are about to do, then work.
2. For each finding above, look at the files and give a verdict. Automatic
   guesses (marked) deserve a quick check, not a long one.
3. Then look through the code yourself for anything the scan cannot see:
   code that reads as copied (a sudden change of style, a credit or URL in a
   comment such as a Stack Overflow link, a known algorithm reproduced verbatim
   with its original comments), embedded tables of outside data, quoted texts
   (lyrics, scripts, books) still under copyright. Add each one as a new item
   with key `file:<relative path>`.
4. When you cannot tell, ask {licensor} (one question at a time) rather than
   guessing; if it stays unclear, use verdict `unsure`.
5. Write the result to /workspace/{DIR}/review.json in exactly this form, and
   rewrite it as you go so nothing is lost if the session ends:

```json
{SCHEMA}
```

Verdicts: `self` = {licensor}'s own work; `claude` = written by {ai}; `generated`
= produced by the software's own code; `logic` = follows plainly from the
language (boilerplate, standard idioms); `opensource` = from an open-source source
(give the SPDX licence); `excluded` = should be kept out of any release;
`restricted` = not open-source or of unknown origin; `unsure` = could not decide.

6. Finish with a short summary in the chat, and tell {licensor} to press
   **Import Claude's review** in Classifier. Classifier only offers your verdicts
   as suggestions; {licensor} accepts them.
"""


def prepare(sw: dict, settings: dict) -> Path:
    root = Path(sw["path"])
    d = root / DIR
    d.mkdir(exist_ok=True)
    (d / "brief.md").write_text(brief(sw, settings), encoding="utf-8")
    gi = root / ".gitignore"
    if (root / ".git").exists():
        text = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if DIR not in text:
            gi.write_text(text + ("" if text.endswith("\n") or not text else "\n")
                          + f"{DIR}/\n", encoding="utf-8")
    return d


def launch(sw: dict, settings: dict):
    """Start the review window; returns (Popen, log path)."""
    exe = launcher()
    if not exe:
        raise FileNotFoundError(
            f"{LAUNCHER} is not installed. Run: python3 "
            f"{Path(__file__).resolve().parent.parent / 'claude-classifier-docker' / 'make.py'}")
    d = prepare(sw, settings)
    root = str(Path(sw["path"]).resolve())
    log = d / "launch.log"
    fh = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen([exe, "--gui", root, "--write", str(d.resolve()),
                             "--brief-file", str((d / "brief.md").resolve())],
                            stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                            start_new_session=True)
    return proc, log


def load(sw: dict) -> dict | None:
    p = Path(sw["path"]) / DIR / "review.json"
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("items", [])
    data["imported_at"] = model.now()
    return data


def accept(sw: dict, keys: list[str] | None = None) -> int:
    """Turn Claude's suggestions into resolutions ('unsure' ones are skipped)."""
    rev = sw.get("claude_review") or {}
    n = 0
    known = {f["key"] for f in model.findings(sw)}
    for it in rev.get("items", []):
        if keys is not None and it.get("key") not in keys:
            continue
        v = it.get("verdict", "")
        if v not in model.RESOLUTIONS or not v or it.get("key") not in known:
            continue
        sw.setdefault("resolutions", {})[it["key"]] = {
            "kind": v, "licence": it.get("licence", ""),
            "note": it.get("reason", ""), "by": "claude (accepted)"}
        n += 1
    return n
