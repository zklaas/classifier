"""Operating-system support: a track per platform, with evidence and blockers.

The licensor sets each track's status.  The scan contributes:
  evidence  signs the software already reaches that platform (installers,
            platform checks in code, platform branches on GitHub, project files)
  blockers  things in the code that will not work there as written

Desktop Python + PyQt6 software ports naturally to Windows, macOS and (with
packages) OpenBSD.  iOS/iPadOS and Android need a different front end (a web
page, Kivy, BeeWare/Briefcase, or a native app), so for those the useful
question is whether such a track exists at all.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import scanner

PLATFORMS = ["Linux", "Windows", "macOS", "iOS/iPadOS", "Android", "OpenBSD"]
TRACKS = {"": "Not planned", "planned": "Planned", "progress": "In progress",
          "untested": "Supported, untested", "supported": "Supported"}

# (regex over code, platform, evidence text)
EVIDENCE_CODE = [
    (r"sys\.platform\s*==\s*['\"]win32|os\.name\s*==\s*['\"]nt|platform\.system\(\)\s*==\s*['\"]Windows",
     "Windows", "code checks for Windows"),
    (r"sys\.platform\s*==\s*['\"]darwin|platform\.system\(\)\s*==\s*['\"]Darwin",
     "macOS", "code checks for macOS"),
    (r"sys\.platform\.startswith\(['\"]linux|sys\.platform\s*==\s*['\"]linux",
     "Linux", "code checks for Linux"),
    (r"openbsd", "OpenBSD", "code mentions OpenBSD"),
    (r"\bwinreg\b|\bmsvcrt\b|ctypes\.windll|LOCALAPPDATA|APPDATA", "Windows",
     "Windows-specific code paths"),
    (r"\bNSApplication|\bAppKit\b|~/Library/", "macOS", "macOS-specific code paths"),
]
EVIDENCE_FILES = [
    (r"\.ps1$|\.bat$|\.cmd$|\.ico$|\.iss$|\.nsi$|\.wxs$|(^|/)WINDOWS\.md$", "Windows"),
    (r"\.icns$|\.command$|Info\.plist$|\.entitlements$|(^|/)MACOS\.md$|\.dmg$", "macOS"),
    (r"\.desktop$|install-desktop\.sh$|\.service$|(^|/)LINUX\.md$", "Linux"),
    (r"\.xcodeproj/|\.swift$|(^|/)Podfile$|(^|/)IOS\.md$", "iOS/iPadOS"),
    (r"AndroidManifest\.xml$|build\.gradle(\.kts)?$|buildozer\.spec$|\.kt$|(^|/)ANDROID\.md$",
     "Android"),
    (r"(^|/)OPENBSD\.md$|pkg_add", "OpenBSD"),
]
# Linux-only (L), Unix-only (U: not Windows), Windows-only (W)
BLOCKERS = [
    (r"/proc/", "L", "reads /proc (Linux only)"),
    (r"\bsystemd-run\b|\bsystemctl\b|\bjournalctl\b", "L", "uses systemd"),
    (r"\bxdg-open\b|\bnotify-send\b|\bgsettings\b|\bxdotool\b|\bwmctrl\b", "L",
     "calls Linux desktop tools"),
    (r"\bapt(-get)?\s+install\b", "L", "mentions apt install (Linux-only advice or setup)"),
    (r"['\"]/usr/share/|['\"]/usr/bin/|['\"]/etc/", "L", "fixed Linux system paths"),
    (r"['\"]/home/\w+", "L", "a hard-coded /home/ path"),
    (r"\bxterm\b|\bgnome-terminal\b|\bkonsole\b", "L", "opens a Linux terminal"),
    (r"^\s*import\s+(fcntl|termios|pwd|grp|resource|pty|tty)\b|^\s*from\s+(fcntl|termios|pwd|grp|resource|pty)\s+import",
     "U", "imports a Unix-only module"),
    (r"os\.fork\(|signal\.SIGKILL|os\.getuid\(|os\.killpg", "U", "Unix-only calls"),
    (r"\bdocker\b", "D", "needs Docker"),
    (r"^\s*import\s+(winreg|msvcrt|winsound)\b|ctypes\.windll", "W", "Windows-only module"),
]
# Python packages that do not run on phones / tablets as a desktop app would.
NO_MOBILE = {"PyQt6", "PyQt5", "PySide6", "tkinter", "pyworld", "sounddevice", "pyaudio",
             "rasterio", "osgeo", "geopandas", "vtk", "pyvista", "torch", "cv2"}
# Packages without OpenBSD packages or wheels (best knowledge; check with pkg_info).
NO_OPENBSD = {"pyworld", "torch", "databricks", "copernicusmarine", "imageio_ffmpeg"}
BUILD_TOOL = re.compile(r"(^|/)(make|gen|generate|build|render|draw)[_-][^/]*\.py$")
PLATFORM_BRANCH = {
    "Windows": re.compile(r"win", re.I), "macOS": re.compile(r"mac|darwin|osx", re.I),
    "iOS/iPadOS": re.compile(r"ios|ipad", re.I), "Android": re.compile(r"android", re.I),
    "OpenBSD": re.compile(r"openbsd|bsd", re.I),
}


def review(root: Path | str, python_deps=(), placements=None) -> dict:
    """{'evidence': {platform: [text]}, 'blockers': {platform: [text]},
    'web': bool}.  `placements` is github.locate()'s result, for branch names."""
    root = Path(root)
    ev = {p: set() for p in PLATFORMS}
    bl = {p: set() for p in PLATFORMS}
    web = False
    keys = touch = False
    backend = False       # the page needs this software's own server
    server_re = re.compile(r"http\.server|HTTPServer|socketserver|aiohttp\.web|from flask|"
                           r"FastAPI|uvicorn|express\(\)|createServer\(")
    local_fetch = re.compile(r"""fetch\(\s*[`'"](/|\./|https?://(localhost|127\.0\.0\.1))"""
                             r"""|new WebSocket\(\s*[`'"]wss?://(localhost|127)""")
    for rel, p in scanner._walk(root):
        for pat, plat in EVIDENCE_FILES:
            if re.search(pat, rel):
                ev[plat].add(f"has {Path(rel).name}" if "/" not in rel else f"has {rel}")
        if p.suffix.lower() not in scanner.CODE_EXT:
            continue
        text = scanner._read_text(p)
        if not text:
            continue
        if p.suffix.lower() in {".html", ".htm"} and "<script" in text.lower():
            web = True
        if server_re.search(text) or local_fetch.search(text):
            backend = True
        if p.suffix.lower() in {".html", ".htm", ".js", ".mjs", ".ts"}:
            if re.search(r"""['"]key(down|up|press)['"]|onkey(down|up|press)""", text, re.I):
                keys = True
            if re.search(r"""['"](touch(start|end|move)|pointer(down|up|move))['"]|ontouch|onpointer""",
                         text, re.I):
                touch = True
        if p.suffix.lower() in {".sh", ".ps1", ".bat", ".cmd"}:
            continue          # installers are platform-specific by design
        if BUILD_TOOL.search(rel):
            continue          # make_icon.py and the like: run by the developer, not by users
        for pat, plat, why in EVIDENCE_CODE:
            if re.search(pat, text, re.I if plat == "OpenBSD" else 0):
                ev[plat].add(why)
        for pat, kind, why in BLOCKERS:
            if re.search(pat, text, re.M):
                where = f"{why} ({rel})"
                targets = {"L": ["Windows", "macOS", "OpenBSD"], "U": ["Windows"],
                           "W": ["Linux", "macOS", "OpenBSD"],
                           "D": ["iOS/iPadOS", "Android", "OpenBSD"]}[kind]
                for t in targets:
                    if t in ("Windows", "macOS", "OpenBSD") and kind in "LU" and \
                            _guarded(text, pat):
                        continue
                    bl[t].add(where)
    keyboard_only = keys and not touch
    for dep in python_deps:
        if dep in NO_MOBILE:
            for t in ("iOS/iPadOS", "Android"):
                bl[t].add(f"{dep} does not run there as a desktop app does")
        if dep in NO_OPENBSD:
            bl["OpenBSD"].add(f"{dep}: no OpenBSD package known")
    if (root / "package.json").exists():
        ev["Linux"].add("Node.js project (Node runs on Linux, Windows, macOS and OpenBSD)")
    for loc in placements or []:
        for b in loc.get("branches", []):
            for plat, rx in PLATFORM_BRANCH.items():
                if rx.search(b):
                    ev[plat].add(f"branch {b} in {loc['repo']}")
    if web and backend:
        ev["Linux"].add("web front end served by the software's own server "
                        "(a browser alone is not enough)")
    if web and not backend:
        for t in PLATFORMS:
            ev[t].add("web page with scripts: runs in any modern browser on this platform")
        if keyboard_only:
            for t in ("iOS/iPadOS", "Android"):
                bl[t].add("controls are keyboard-only (no touch or pointer events): "
                          "phones and tablets have no keys to press")
        for t in ("iOS/iPadOS", "Android"):
            bl[t] = {b for b in bl[t] if "desktop app" not in b} | (
                {"the desktop wrapper (" + ", ".join(sorted(set(python_deps) & NO_MOBILE))
                 + ") will not run there, but the web page can"}
                if set(python_deps) & NO_MOBILE else set())
    return {"evidence": {k: sorted(v) for k, v in ev.items()},
            "blockers": {k: sorted(v)[:30] for k, v in bl.items()},
            "web": web and not backend, "web_frontend": web and backend}


def _guarded(text: str, pat: str) -> bool:
    """True when every use of a Linux/Unix-only thing is already handled elsewhere:

    * it sits within 6 lines of a platform check (`sys.platform`, `os.name`,
      `platform.system()`, a WINDOWS / MACOS / PORTAUDIO-style flag) or of a
      try/except ImportError / shutil.which;
    * it is one of several alternative paths on one line (a list of places to
      look, e.g. SoundFont locations), not a single required path;
    * it is advice in a message that also gives the macOS (Homebrew) advice."""
    lines = text.splitlines()
    hits = [i for i, ln in enumerate(lines) if re.search(pat, ln)]
    if not hits:
        return False
    guard = re.compile(r"sys\.platform|os\.name|platform\.system|except\s+(ImportError|"
                       r"ModuleNotFoundError|OSError|FileNotFoundError)|shutil\.which|"
                       r"\b(IS_)?(WINDOWS|WIN32|MACOS|MAC|DARWIN|LINUX|PORTAUDIO)\b|brew install")
    for i in hits:
        if len(re.findall(r"[\"']/(usr|etc|opt)/[^\"']+[\"']", lines[i])) >= 2 or \
                re.match(r"\s*(\w+\s*=\s*)?\[?\s*[\"']/(usr|etc|opt)/[^\"']+[\"']\s*[,\]]", lines[i]):
            continue              # an item in a list of places to look
        window = "\n".join(lines[max(0, i - 6):i + 6])
        if not guard.search(window):
            return False
    return True


def summary(sw: dict) -> str:
    """Short text for the table: supported platforms."""
    tr = sw.get("platforms", {})
    on = [p for p in PLATFORMS if tr.get(p, {}).get("track") in ("supported", "untested")]
    return ", ".join(on)


def warnings(sw: dict) -> list[str]:
    rev = sw.get("platform_review") or {}
    out = []
    for p in PLATFORMS:
        t = sw.get("platforms", {}).get(p, {}).get("track", "")
        if t in ("supported", "untested") and rev.get("blockers", {}).get(p):
            out.append(f"{p} is marked {TRACKS[t]} but the code has "
                       f"{len(rev['blockers'][p])} blocker(s) for it")
        specific = [e for e in rev.get("evidence", {}).get(p, [])
                    if not e.startswith("web page with scripts")]
        if not t and specific and p != "Linux":
            out.append(f"{p}: not planned, yet there are signs of support ({specific[0]})")
    return out
