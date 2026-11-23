"""Downloadable programs: ready-to-run packages on a GitHub release.

The catalogue links to the packages on each repository's latest release (see
catalogue.release_downloads).  This module makes sure there is something to
link to:

  * finds whether a software has a release build (a GitHub Actions workflow
    that creates a GitHub release) and what its latest release is;
  * warns when a public, Releasable program has no release build, no release
    yet, or a release older than its code (`audit`);
  * sets up a release build for a Python program (`plan_setup` + `write_setup`):
    PyInstaller bundles Python and every package, then a Windows installer
    (Inno Setup), a macOS disk image and a Linux AppImage are attached to the
    release under names that never change, so
        https://github.com/<repo>/releases/latest/download/<name>
    always gives the newest one;
  * checks for what breaks once a program is packaged: saving files beside
    itself (a packaged program's own folder is read-only or replaced on every
    update) and QtWebEngine's sandbox inside an AppImage;
  * starts a build and waits for it (`start_build`, `wait_build`), so the
    catalogue can be republished when the packages exist.

Finding things is read-only.  Writing files changes only the software's own
folder, and only when asked; starting a build runs the repository's workflow.
"""

from __future__ import annotations

import json
import re
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import model

DESKTOPS = ("Windows", "macOS", "Linux")
WORKFLOW_DIR = ".github/workflows"
WORKFLOW_FILE = "release.yml"

# Files that are documentation, not the program: changes to them alone do not
# make a release out of date, and they are not bundled into the package.
DOC_SUFFIXES = (".md", ".rst", ".txt", ".html", ".htm")
DOC_STEMS = {"license", "licence", "copying", "notice", "authors", "changelog", "readme",
             "manual", "third-party", "platforms"}
NOT_BUNDLED_DIRS = {".github", "packaging", "tests", "test", "docs", "doc", "screenshots",
                    "__pycache__"}
NOT_BUNDLED_SUFFIXES = (".py", ".pyc", ".sh", ".ps1", ".bat", ".cmd", ".iss", ".spec",
                        ".desktop", ".yml", ".yaml", ".toml", ".cfg")
NOT_BUNDLED_FILES = {"requirements.txt", "setup.py", "pyproject.toml", ".gitignore",
                     ".nojekyll", "Makefile"}

GUI_TOOLKITS = ("PyQt6", "PyQt5", "PySide6", "PySide2", "tkinter", "wx", "kivy", "pygame",
                "customtkinter", "toga", "flet", "dearpygui")

# import name -> what PyInstaller needs beyond following the imports
QUIRKS = {
    "sounddevice": {"collect": ["sounddevice"]},
    "imageio_ffmpeg": {"collect": ["imageio_ffmpeg"]},
    "pyworld": {"collect": ["pyworld"], "hidden": ["pkg_resources"],
                "pip": ['"setuptools<81"']},      # pyworld imports pkg_resources
    "vlc": {"collect": ["vlc"]},
    "certifi": {"collect": ["certifi"]},
}

MIC = re.compile(r"\bsounddevice\b|\bpyaudio\b|QAudioSource|QAudioInput|QMediaRecorder|"
                 r"QMediaCaptureSession|speech_recognition|\bInputStream\(")
CAMERA = re.compile(r"\bQCamera\b|cv2\.VideoCapture|videoInputs\(")
# ffmpeg recording from a camera: its capture device names as whole string literals
FFMPEG_CAMERA = re.compile(r"['\"](avfoundation|dshow|v4l2|video4linux2)['\"]")

WRITE_OP = re.compile(r"open\([^)]*['\"][wax]b?\+?['\"]|\.write_text\(|\.write_bytes\(|"
                      r"makedirs\(|\.mkdir\(|sqlite3\.connect\(|shutil\.copy|\.touch\(")
WRITABLE_NAME = re.compile(
    r"['\"](data|cache|config|settings|saves?|saved|output|out|logs?|state|user|userdata|"
    r"projects?)['\"/\\]|['\"][^'\"\n]*\.(sqlite3?|db|log)['\"]|"
    r"['\"][^'\"\n]*(settings|config|prefs|preferences|state|history)[^'\"\n]*"
    r"\.(json|ini|cfg|toml)['\"]")


# --------------------------------------------------------------- what is there

def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def setup_info(root: Path | str) -> dict | None:
    """The workflow in the folder that builds a GitHub release, if any:
    {'workflow': file name, 'dispatch': can be started by hand,
     'version_input': asks for a version number}."""
    d = Path(root) / WORKFLOW_DIR
    if not d.is_dir():
        return None
    for f in sorted(d.glob("*.y*ml")):
        t = _read(f)
        if re.search(r"gh release (create|upload)|action-gh-release|actions/create-release", t):
            return {"workflow": f.name, "dispatch": "workflow_dispatch" in t,
                    "version_input": bool(re.search(r"inputs:\s*\n\s+version:", t))}
    return None


def is_doc(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    stem = name.split(".")[0].lower()
    return (rel.lower().endswith(DOC_SUFFIXES) and not rel.lower().endswith((".html", ".htm"))
            or stem in DOC_STEMS or rel.startswith((".github/", "packaging/"))
            or name in (".gitignore", ".nojekyll"))


def release_platforms(sw: dict) -> list[str]:
    """Desktop systems the software is Supported (or Supported, untested) on;
    Linux when no track is set."""
    tracks = sw.get("platforms", {})
    out = [p for p in DESKTOPS if tracks.get(p, {}).get("track") in ("supported", "untested")]
    return out or ["Linux"]


def _python_files(sw: dict, include: list[str] | None = None) -> dict[str, str]:
    root = Path(sw["path"])
    if include is None:
        from . import publish
        include = publish.plan(sw)["include"]
    return {rel: _read(root / rel) for rel in include if rel.endswith(".py")}


def code_only(text: str) -> str:
    """The text without strings and comments (so a pattern written in a string,
    or a word in a comment, does not count as use)."""
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT) and tok.type != getattr(
                    tokenize, "FSTRING_MIDDLE", -1):
                out.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    return " ".join(out)


def reachable(code: dict[str, str], entry: str) -> dict[str, str]:
    """The program's own files that `entry` imports, directly or not (a package
    counts whole).  Tests and build tools that nothing imports are left out."""
    mods = {}
    for rel in code:
        parts = rel[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        mods[".".join(parts)] = rel
    imp = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import\s+([\w, ()*]+)|import\s+([\w., ]+))",
                     re.M)
    seen, todo = set(), [entry]
    while todo:
        rel = todo.pop()
        if rel in seen or rel not in code:
            continue
        seen.add(rel)
        pkg = rel.rsplit("/", 1)[0].replace("/", ".") if "/" in rel else ""
        names = []
        for frm, what, plain in imp.findall(code[rel]):
            if plain:
                names += [n.strip().split(" as ")[0] for n in plain.split(",")]
                continue
            base = frm
            if frm.startswith("."):
                up = len(frm) - len(frm.lstrip("."))
                parts = pkg.split(".") if pkg else []
                parts = parts[:len(parts) - (up - 1)] if up > 1 else parts
                base = ".".join(parts + ([frm.lstrip(".")] if frm.lstrip(".") else []))
            names.append(base)
            names += [f"{base}.{w.strip().split(' as ')[0]}" for w in
                      what.strip("()").split(",") if w.strip() and w.strip() != "*"]
        for n in names:
            while n:
                if n in mods:
                    todo.append(mods[n])
                    top = n.split(".")[0]
                    todo += [r for m, r in mods.items() if m == top or m.startswith(top + ".")]
                    break
                n = n.rsplit(".", 1)[0] if "." in n else ""
    return {r: code[r] for r in code if r in seen}


def entry_point(sw: dict, include: list[str] | None = None) -> tuple[str | None, list[str]]:
    """(the script that starts the program, every top-level script that could).
    None when there is no clear choice."""
    root = Path(sw["path"])
    code = _python_files(sw, include)
    cands = [rel for rel, t in code.items() if "/" not in rel
             and re.search(r"__name__\s*==\s*['\"]__main__['\"]", t)
             and not re.match(r"(_|test|make_|gen_|build_|setup\.py)", rel)]
    if not cands:
        return None, []
    simple = re.sub(r"[^a-z0-9]", "", sw["name"].lower())
    folder = re.sub(r"[^a-z0-9]", "", root.name.lower())
    launch = " ".join(_read(root / f) for f in ("run.sh", "install-desktop.sh", "install.sh",
                                                 "install.ps1", "install-macos.sh"))

    def rank(rel):
        stem = re.sub(r"[^a-z0-9]", "", rel[:-3].lower())
        return (0 if stem in (simple, folder) else
                1 if stem in (simple + "app", folder + "app") else
                2 if rel in launch else
                3 if stem in ("main", "app", "run", "__main__") else 9)
    cands.sort(key=lambda r: (rank(r), r))
    best = cands[0] if (rank(cands[0]) < 9 or len(cands) == 1) else None
    return best, cands


def command_name(entry: str) -> str:
    stem = entry.rsplit("/", 1)[-1][:-3]
    return re.sub(r"_?app$", "", stem).lower() or stem.lower()


def is_gui(code: dict[str, str]) -> bool:
    pat = re.compile(r"^\s*(from|import)\s+(" + "|".join(GUI_TOOLKITS) + r")\b", re.M)
    return any(pat.search(t) for t in code.values())


def uses_webengine(code: dict[str, str]) -> bool:
    return any("QtWebEngine" in code_only(t) for t in code.values())


def uses(pattern: re.Pattern, code: dict[str, str]) -> bool:
    return any(pattern.search(code_only(t)) for t in code.values())


def bundled_data(sw: dict, include: list[str], code: dict[str, str]) -> list[str]:
    """Top-level files and folders (folders end in '/'; not code) that the program
    opens by name, plus icons/, to bundle beside it."""
    tops = {}
    for rel in include:
        top = rel.split("/", 1)[0]
        tops.setdefault(top, "/" in rel)
    allcode = "\n".join(code.values())
    out = []
    for top, is_dir in sorted(tops.items()):
        if top in NOT_BUNDLED_DIRS or top.startswith(".") or top in NOT_BUNDLED_FILES:
            continue
        if not is_dir and top.endswith(NOT_BUNDLED_SUFFIXES):
            continue
        if is_dir and (Path(sw["path"]) / top / "__init__.py").exists():
            continue                     # a package: PyInstaller follows the imports
        if top == "icons" or re.search(r"['\"/\\]" + re.escape(top) + r"['\"/\\]", allcode):
            out.append(top + "/" if is_dir else top)
    return out


def icons(sw: dict, include: list[str], cmd: str) -> dict:
    """{'ico', 'icns', 'png'}: relative paths (or '') of the program's icons; the
    PNG closest to 256 pixels wide."""
    def pick(ext):
        hits = [r for r in include if r.lower().endswith(ext)]
        hits.sort(key=lambda r: (cmd not in r.lower(), "icon" not in r.lower(), len(r)))
        return hits

    pngs = []
    for r in pick(".png"):
        try:
            with open(Path(sw["path"]) / r, "rb") as f:
                head = f.read(24)
            w = struct.unpack(">I", head[16:20])[0] if head[:8] == b"\x89PNG\r\n\x1a\n" else 0
        except OSError:
            w = 0
        if w >= 64 and ("icon" in r.lower() or cmd in r.lower()):
            pngs.append((abs(w - 256), r))
    pngs.sort()
    return {"ico": (pick(".ico") or [""])[0], "icns": (pick(".icns") or [""])[0],
            "png": pngs[0][1] if pngs else ""}


def pip_names(sw: dict, code: dict[str, str]) -> list[str]:
    from .catalogue import PIP_NAME, runtime_deps
    out = ["pyinstaller"]
    for dep in runtime_deps(sw):
        out.append(PIP_NAME.get(dep, dep))
        out += QUIRKS.get(dep, {}).get("pip", [])
    if uses_webengine(code) and "PyQt6" in out and "PyQt6-WebEngine" not in out:
        out.append("PyQt6-WebEngine")
    if uses_webengine(code) and "PyQt5" in out and "PyQtWebEngine" not in out:
        out.append("PyQtWebEngine")
    seen = []
    for p in out:
        if p not in seen:
            seen.append(p)
    return seen


def saves_beside_itself(code: dict[str, str]) -> list[str]:
    """'file:line: text' for places that write into the program's own folder.
    A packaged program's folder is read-only (macOS app, Linux AppImage) or
    replaced on every update (Windows), so such files must go to the user's
    data folder when the program runs packaged (sys.frozen).  A file that
    already checks sys.frozen is taken to handle it."""
    hits = []
    for rel, t in sorted(code.items()):
        if re.search(r"\bfrozen\b", t):
            continue
        vars_ = set(re.findall(r"^\s*([A-Za-z_]\w*)\s*=.*__file__", t, re.M))
        near = re.compile(r"__file__" + "".join(rf"|\b{re.escape(v)}\b" for v in vars_))
        for n, line in enumerate(t.splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or not near.search(s):
                continue
            if WRITE_OP.search(s) or WRITABLE_NAME.search(s):
                hits.append(f"{rel}:{n}: {s[:110]}")
    return hits


def problems_when_packaged(sw: dict, code: dict[str, str] | None = None) -> list[str]:
    if code is None:
        code = _python_files(sw)
        entry = entry_point(sw)[0]
        if entry in code:
            code = reachable(code, entry)
    out = []
    beside = saves_beside_itself(code)
    if beside:
        out.append("It saves files beside itself. Packaged, its own folder is read-only "
                   "(macOS, Linux AppImage) or replaced by each update (Windows): when "
                   "sys.frozen is set, save to the user's data folder instead "
                   "(%LOCALAPPDATA%, ~/Library/Application Support, ~/.local/share) or "
                   "Documents. Seen at: " + "; ".join(beside[:5])
                   + (f" (+{len(beside) - 5} more)" if len(beside) > 5 else ""))
    if (uses_webengine(code) and "Linux" in release_platforms(sw)
            and not any("QTWEBENGINE_DISABLE_SANDBOX" in t for t in code.values())):
        out.append("It uses QtWebEngine: inside a Linux AppImage its sandbox helper cannot "
                   "start, so set QTWEBENGINE_DISABLE_SANDBOX=1 when sys.frozen is set on "
                   "Linux (before QtWebEngine is imported).")
    return out


# --------------------------------------------------------------- setting it up

def asset_names(app: str) -> dict:
    a = re.sub(r"[^A-Za-z0-9_.-]", "", app)
    return {"Windows": f"{a}-Setup-Windows.exe", "macOS": f"{a}-macOS.dmg",
            "Linux": f"{a}-Linux-x86_64.AppImage"}


def _short_description(sw: dict) -> str:
    from .catalogue import readme_summary
    s = (sw.get("public_summary") or readme_summary(Path(sw["path"])) or sw["name"]).strip()
    s = re.split(r"(?<=[.!?])\s", s)[0].rstrip(".")
    s = re.sub(r"[*_`]", "", s)
    return (s[:77].rsplit(" ", 1)[0] + "…") if len(s) > 80 else s


def plan_setup(sw: dict, settings: dict, repo: str, entry: str | None = None,
               force: bool = False) -> dict:
    """What `write_setup` would write.  {'problems': [...] (nothing is written while
    there are any), 'warnings': [...], 'files': {rel: text}, 'readme': new README
    text or None, 'info': {...}}."""
    from . import publish
    from .catalogue import contact_line, display_name
    root = Path(sw["path"])
    include = publish.plan(sw)["include"]
    allcode = _python_files(sw, include)
    problems, warnings = [], []
    existing = setup_info(root)
    if existing and not force:
        problems.append(f"A release build is already set up ({WORKFLOW_DIR}/"
                        f"{existing['workflow']}).")
    if not allcode:
        problems.append("Classifier sets up downloads for Python programs only; for other "
                        "languages write a release workflow by hand (any workflow that "
                        "creates a GitHub release is recognised).")
        return {"problems": problems, "warnings": [], "files": {}, "readme": None, "info": {}}
    best, cands = entry_point(sw, include)
    entry = entry or best
    if not entry:
        problems.append("Which script starts the program? None of these is clear: "
                        + (", ".join(cands) or "no top-level script has "
                           "if __name__ == \"__main__\"") + ".")
    elif entry not in allcode:
        problems.append(f"{entry} is not a published Python file.")
    code = reachable(allcode, entry) if entry in allcode else allcode
    if "/" not in repo:
        problems.append("Name the GitHub repository as owner/name.")
    plats = release_platforms(sw)
    app = re.sub(r"\s+", "", display_name(sw)) or sw["name"]
    title = display_name(sw)
    cmd = command_name(entry) if entry else sw["name"].lower()
    ic = icons(sw, include, cmd)
    if "Linux" in plats and not ic["png"]:
        problems.append("The Linux AppImage needs a PNG icon (at least 64 pixels, with "
                        "\"icon\" or the program's name in its file name).")
    if "Windows" in plats and not ic["ico"]:
        warnings.append("No .ico icon: the Windows program gets PyInstaller's standard icon.")
    if "macOS" in plats and not ic["icns"]:
        warnings.append("No .icns icon: the macOS app gets PyInstaller's standard icon.")
    warnings += problems_when_packaged(sw, code)
    if not settings.get("contact"):
        warnings.append("No contact address in Settings: the release notes will not say "
                        "where to report problems.")
    gui = is_gui(code)
    if not gui:
        warnings.append("No window toolkit found: it is packaged as a command-line program "
                        "and the build checks that `--help` works.")
    info = {"app": app, "title": title, "cmd": cmd, "entry": entry or "", "platforms": plats,
            "gui": gui, "icons": ic, "data": bundled_data(sw, include, code),
            "pip": pip_names(sw, code), "webengine": uses_webengine(code),
            "repo": repo, "assets": {p: n for p, n in asset_names(app).items() if p in plats},
            "description": _short_description(sw),
            "publisher": settings.get("licensor", ""), "contact": contact_line(settings),
            "mic": uses(MIC, code),
            "camera": uses(CAMERA, code) or any(FFMPEG_CAMERA.search(t) for t in code.values())}
    info["collect"], info["hidden"] = [], []
    from .catalogue import runtime_deps
    for dep in runtime_deps(sw):
        info["collect"] += QUIRKS.get(dep, {}).get("collect", [])
        info["hidden"] += QUIRKS.get(dep, {}).get("hidden", [])
    files = {}
    if not problems:
        files[f"{WORKFLOW_DIR}/{WORKFLOW_FILE}"] = workflow_text(info)
        if "Windows" in plats:
            files[f"packaging/{cmd}.iss"] = iss_text(info)
        files["packaging/release-notes.md"] = notes_text(info)
    readme = readme_with_downloads(root, info) if not problems else None
    return {"problems": problems, "warnings": warnings, "files": files, "readme": readme,
            "info": info}


def write_setup(sw: dict, plan: dict) -> list[str]:
    """Write the planned files into the software's folder; returns what was written."""
    if plan["problems"]:
        raise ValueError("; ".join(plan["problems"]))
    root = Path(sw["path"])
    done = []
    for rel, text in plan["files"].items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        done.append(rel)
    if plan["readme"] is not None:
        name = next((n for n in ("README.md", "MANUAL.md") if (root / n).is_file()), "README.md")
        (root / name).write_text(plan["readme"], encoding="utf-8")
        done.append(name)
    return done


def readme_with_downloads(root: Path, info: dict) -> str | None:
    """The README with a '## Download' section added (None when it has one)."""
    name = next((n for n in ("README.md", "MANUAL.md") if (root / n).is_file()), None)
    text = _read(root / name) if name else f"# {info['title']}\n\n{info['description']}.\n"
    if re.search(r"^##\s+Downloads?\b", text, re.M):
        return None
    base = f"https://github.com/{info['repo']}/releases/latest"
    label = {"Windows": "**Windows:**", "macOS": "**macOS** (Apple silicon):",
             "Linux": "**Linux:**"}
    rows = [f"* {label[p]} [{n}]({base}/download/{n})" for p, n in info["assets"].items()]
    if set(info["assets"]) & {"Windows", "macOS"}:
        after = ("The first time, Windows or macOS asks before opening it; the "
                 f"[release page]({base}) says what to click.")
    else:
        after = f"See the [release page]({base}) for how to start it."
    sec = ["## Download", "", "Ready-to-run downloads (executable programs), with nothing "
           "else to install:", ""] + rows + [
        "", after, "To install from the source code instead, see below.", "", ""]
    m = re.search(r"^##\s", text, re.M)
    if m:
        return text[:m.start()] + "\n".join(sec) + text[m.start():]
    return text.rstrip("\n") + "\n\n" + "\n".join(sec).rstrip("\n") + "\n"


def _sh(s: str) -> str:
    return s.replace("'", "'\\''")


def workflow_text(i: dict) -> str:
    plats, app, cmd, ic = i["platforms"], i["app"], i["cmd"], i["icons"]
    matrix = []
    none = '""'
    if "Windows" in plats:
        matrix += ["          - os: windows-latest", f"            icon: {ic['ico'] or none}",
                   '            sep: ";"']
    if "macOS" in plats:
        matrix += ["          - os: macos-latest", f"            icon: {ic['icns'] or none}",
                   '            sep: ":"']
    if "Linux" in plats:
        matrix += ["          - os: ubuntu-22.04            # older Linux base: the AppImage "
                   "runs on more systems", f"            icon: {ic['png']}", '            sep: ":"']
    pyi = ["pyinstaller --noconfirm --clean" + (" --windowed" if i["gui"] else "")
           + f" --name {app}"]
    pyi.append('$ICON')
    pyi += [f'--add-data "{d.rstrip("/")}${{{{ matrix.sep }}}}{d.rstrip("/")}"'
            if d.endswith("/") else f'--add-data "{d}${{{{ matrix.sep }}}}."' for d in i["data"]]
    pyi += [f"--collect-all {c}" for c in i["collect"]]
    pyi += [f"--hidden-import {h}" for h in i["hidden"]]
    pyi.append(i["entry"])
    env = ["          QT_QPA_PLATFORM: offscreen"]
    if i["webengine"]:
        env += ['          QTWEBENGINE_DISABLE_SANDBOX: "1"',
                '          QTWEBENGINE_CHROMIUM_FLAGS: "--no-sandbox --disable-gpu"']
    if i["gui"]:
        check = f"""          # the window: still running after 25 s = it started (Python's timeout works everywhere)
          python - "$EXE" <<'PY'
          import subprocess, sys
          try:
              subprocess.run([sys.argv[1]], timeout=25)
          except subprocess.TimeoutExpired:
              print("window started"); sys.exit(0)
          sys.exit("the window closed by itself")
          PY"""
    else:
        check = '          "$EXE" --help'
    mac_exe = f"dist/{app}.app/Contents/MacOS/{app}" if i["gui"] else f"dist/{app}/{app}"
    perms = []
    if i["mic"]:
        perms.append(f'plutil -insert NSMicrophoneUsageDescription -string "{app} uses the '
                     f'microphone." dist/{app}.app/Contents/Info.plist')
    if i["camera"]:
        perms.append(f'plutil -insert NSCameraUsageDescription -string "{app} uses the '
                     f'camera." dist/{app}.app/Contents/Info.plist')
    out = [f"# Build downloadable {i['title']} packages ({', '.join(plats)}) and attach them "
           "to a GitHub", "# Release.", "#",
           '#   Start it:  Actions > "Release packages" > Run workflow (give a version, '
           "e.g. 1.0.1)", "#              or push a tag such as v1.0.1", "#",
           f"# Each package bundles Python and everything {i['title']} uses (built by "
           "PyInstaller), so", "# people need nothing else. Asset names stay the same from "
           "release to release, so",
           f"#   https://github.com/{i['repo']}/releases/latest/download/<name>",
           "# always gives the newest one.  (Set up by Classifier.)",
           "name: Release packages", "", "on:", "  push:", '    tags: ["v*"]',
           "  workflow_dispatch:", "    inputs:", "      version:",
           '        description: "Version number (e.g. 1.0.1)"', "        required: true", "",
           "permissions:", "  contents: write", "", "env:",
           "  VERSION: ${{ github.event.inputs.version || github.ref_name }}", "",
           "jobs:", "  build:", "    strategy:", "      fail-fast: false", "      matrix:",
           "        include:", *matrix, "    runs-on: ${{ matrix.os }}", "    steps:",
           "      - uses: actions/checkout@v4", "", "      - uses: actions/setup-python@v5",
           "        with:", '          python-version: "3.12"', "",
           "      - name: Install the build tools",
           "        run: python -m pip install " + " ".join(i["pip"]), ""]
    if "Linux" in plats:
        out += ["      - name: Linux libraries Qt needs (for the start-up check)",
                "        if: runner.os == 'Linux'",
                "        run: sudo apt-get update && sudo apt-get install -y libegl1 "
                "libxkbcommon-x11-0 libxcb-cursor0 libfuse2 libportaudio2 libnss3 "
                "libxcomposite1 libxdamage1 libxrandr2 libxtst6 libasound2", ""]
    out += ["      - name: Build with PyInstaller", "        shell: bash", "        run: |",
            '          ICON=""',
            '          if [ -n "${{ matrix.icon }}" ]; then ICON="--icon ${{ matrix.icon }}"; fi',
            "          " + " \\\n            ".join(pyi), "",
            "      - name: Check that it starts", "        shell: bash", "        env:", *env,
            "        run: |",
            f'          if [ "$RUNNER_OS" = macOS ]; then EXE={mac_exe}',
            f'          elif [ "$RUNNER_OS" = Windows ]; then EXE=dist/{app}/{app}.exe',
            f"          else EXE=dist/{app}/{app}; fi", check, ""]
    if perms and "macOS" in plats and i["gui"]:
        out += ["      - name: macOS permission notes", "        if: runner.os == 'macOS'",
                "        run: |", *[f"          {p}" for p in perms],
                f"          codesign --force --deep -s - dist/{app}.app", ""]
    if "Windows" in plats:
        out += ["      - name: Package for Windows (installer)", "        if: runner.os == 'Windows'",
                "        shell: pwsh", "        run: |",
                "          if (-not (Get-Command iscc -ErrorAction SilentlyContinue)) "
                "{ choco install innosetup -y --no-progress }",
                "          $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source",
                '          if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\\Inno Setup 6\\ISCC.exe" }',
                f'          & $iscc "/DAppVersion=$($env:VERSION.TrimStart(\'v\'))" packaging\\{cmd}.iss',
                '          if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }', ""]
    if "macOS" in plats:
        src = f"dist/{app}.app" if i["gui"] else f"dist/{app}"
        out += ["      - name: Package for macOS (disk image)", "        if: runner.os == 'macOS'",
                "        run: |", "          mkdir -p release dmg", f"          cp -R {src} dmg/",
                "          ln -s /Applications dmg/Applications",
                f'          hdiutil create -volname "{i["title"]}" -srcfolder dmg -ov -format '
                f"UDZO release/{i['assets']['macOS']}", ""]
    if "Linux" in plats:
        out += ["      - name: Package for Linux (AppImage)", "        if: runner.os == 'Linux'",
                "        run: |", "          mkdir -p release AppDir/usr/bin",
                f"          cp -R dist/{app}/. AppDir/usr/bin/",
                f"          cp {ic['png']} AppDir/{cmd}.png",
                "          printf '%s\\n' '[Desktop Entry]' 'Type=Application' "
                f"'Name={_sh(i['title'])}' \\",
                f"            'Comment={_sh(i['description'])}' 'Exec={app}' 'Icon={cmd}' "
                f"'Categories=Utility;'" + ("" if i["gui"] else " 'Terminal=true'")
                + f" > AppDir/{cmd}.desktop",
                "          printf '%s\\n' '#!/bin/sh' 'HERE=\"$(dirname \"$(readlink -f \"$0\")\")\"' \\",
                f"            'exec \"$HERE/usr/bin/{app}\" \"$@\"' > AppDir/AppRun",
                "          chmod +x AppDir/AppRun",
                "          curl -fsSL -o appimagetool https://github.com/AppImage/appimagetool/"
                "releases/download/continuous/appimagetool-x86_64.AppImage",
                "          chmod +x appimagetool",
                f"          ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 ./appimagetool AppDir "
                f"release/{i['assets']['Linux']}", ""]
    out += ["      - uses: actions/upload-artifact@v4", "        with:",
            "          name: package-${{ runner.os }}", "          path: release/*", "",
            "  release:", "    needs: build", "    runs-on: ubuntu-latest", "    steps:",
            "      - uses: actions/checkout@v4", "      - uses: actions/download-artifact@v4",
            "        with:", "          path: packages", "          merge-multiple: true",
            "      - name: Publish the release", "        env:",
            "          GH_TOKEN: ${{ github.token }}", "        run: |",
            '          TAG="v${VERSION#v}"', "          ls -la packages",
            '          if gh release view "$TAG" > /dev/null 2>&1; then',
            '            gh release upload "$TAG" packages/* --clobber', "          else",
            '            gh release create "$TAG" packages/* --target "$GITHUB_SHA" \\',
            f'              --title "{i["title"]} ${{VERSION#v}}" --notes-file '
            "packaging/release-notes.md", "          fi", ""]
    return "\n".join(out)


def iss_text(i: dict) -> str:
    app, cmd = i["app"], i["cmd"]
    guid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/{i['repo']}")).upper()
    icon = ("SetupIconFile=..\\" + i["icons"]["ico"].replace("/", "\\") + "\n"
            if i["icons"]["ico"] else "")
    pub = f"AppPublisher={i['publisher']}\n" if i["publisher"] else ""
    return f"""; Inno Setup script: the Windows installer for {i['title']}.
; Built by .github/workflows/{WORKFLOW_FILE} from PyInstaller's dist\\{app} folder:
;   iscc /DAppVersion=1.0.0 packaging\\{cmd}.iss
; Installs for the current user only (no administrator rights), adds a Start-menu
; entry and an uninstaller (Settings > Apps).  (Set up by Classifier.)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{{{{guid}}}
AppName={i['title']}
AppVersion={{#AppVersion}}
{pub}AppPublisherURL=https://github.com/{i['repo']}
DefaultDirName={{localappdata}}\\Programs\\{app}
DefaultGroupName={i['title']}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\\release
OutputBaseFilename={i['assets']['Windows'][:-4]}
{icon}UninstallDisplayIcon={{app}}\\{app}.exe
LicenseFile=..\\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\\dist\\{app}\\*"; DestDir: "{{app}}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{{group}}\\{i['title']}"; Filename: "{{app}}\\{app}.exe"
Name: "{{userdesktop}}\\{i['title']}"; Filename: "{{app}}\\{app}.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{{app}}\\{app}.exe"; Description: "Start {i['title']}"; Flags: nowait postinstall skipifsilent
"""


def notes_text(i: dict) -> str:
    base = f"https://github.com/{i['repo']}/releases/latest/download"
    rows = {
        "Windows": ("**Windows** 10 or 11", "Double-click it and follow the steps. "
                    f"{i['title']} appears in the Start menu."),
        "macOS": ("**macOS** 11 or newer (Apple silicon: M1 or later)",
                  f"Open it and drag **{i['app']}** onto **Applications**."),
        "Linux": ("**Linux** (64-bit PC)", "Right-click it > *Properties* > *Allow executing "
                  "as program* (or `chmod +x` it), then double-click."),
    }
    out = ["## Download", "", "| Your computer | Download | Then |", "|---|---|---|"]
    for p, n in i["assets"].items():
        out.append(f"| {rows[p][0]} | [{n}]({base}/{n}) | {rows[p][1]} |")
    out += ["", f"Nothing else is needed: Python and everything {i['title']} uses are inside.",
            ""]
    first = [p for p in i["assets"] if p in ("Windows", "macOS")]
    if first:
        out += ["### The first time you open it", "",
                "These downloads are not signed with a paid developer certificate, so your "
                "computer", "asks once before running them:", ""]
        if "Windows" in first:
            out.append("* **Windows** may say *\"Windows protected your PC\"*: click **More "
                       "info**, then **Run anyway**.")
        if "macOS" in first:
            out.append(f"* **macOS** may say {i['app']} *\"cannot be opened\"* or *\"could not "
                       "be verified\"*: click\n  **Done**, open **System Settings > Privacy & "
                       "Security**, scroll down and click\n  **Open Anyway** next to "
                       f"{i['app']}, then open it again.")
        if "Linux" in i["assets"]:
            out.append("* **Linux** asks nothing, once the file is allowed to run.")
        out.append("")
    if i["contact"]:
        out += [i["contact"], ""]
    return "\n".join(out)


# --------------------------------------------------------------- on GitHub

def latest_release(repo: str) -> dict | None:
    """{'tag', 'at', 'assets': [names]} of the latest release, or None."""
    from . import github
    try:
        raw = github.gh("api", f"repos/{repo}/releases/latest", "--jq",
                        "{tag: .tag_name, at: .published_at, assets: [.assets[].name]}")
        return json.loads(raw)
    except (github.GhError, ValueError):
        return None


def changed_since(repo: str, tag: str) -> list[str]:
    """Program files (not documentation) changed on the default branch since `tag`."""
    from . import github
    try:
        out = github.gh("api", f"repos/{repo}/compare/{tag}...HEAD", "--jq",
                        ".files[]?.filename")
    except github.GhError:
        return []
    return [f for f in out.splitlines() if f and not is_doc(f)]


def packaged_paths(sw: dict) -> tuple[set, tuple] | None:
    """(files, folder prefixes) that go into the downloadable package: the code the
    program imports plus the data it bundles.  None when it cannot be worked out."""
    from . import publish
    root = Path(sw["path"])
    if not root.is_dir():
        return None
    include = publish.plan(sw)["include"]
    allcode = _python_files(sw, include)
    entry = entry_point(sw, include)[0]
    if entry not in allcode:
        return None
    code = reachable(allcode, entry)
    data = bundled_data(sw, include, code)
    return (set(code) | {d for d in data if not d.endswith("/")},
            tuple(d for d in data if d.endswith("/")))


def affects_package(changed: list[str], sw: dict) -> list[str]:
    pk = packaged_paths(sw)
    if pk is None:
        return changed
    files, dirs = pk
    return [f for f in changed if f in files or f.startswith(dirs)]


def _whole_public_repo(locs: list[dict]) -> dict | None:
    return next((l for l in locs if l.get("visibility") == "PUBLIC" and not l.get("path")), None)


def summary(sw: dict) -> str:
    """Short text for the Downloads column (from the last GitHub check)."""
    r = sw.get("release_state") or {}
    if not r:
        return ""
    if r.get("release"):
        return r["release"]["tag"] + (" (older than code)" if r.get("changed") else "")
    return "set up, not built" if r.get("setup") else "none"


def describe(sw: dict) -> list[str]:
    """Lines for the GitHub tab / `classifier release`."""
    r = sw.get("release_state") or {}
    local = setup_info(sw["path"])
    out = [f"Release build: {WORKFLOW_DIR}/{local['workflow']}" if local
           else "Release build: none set up in the folder"]
    if not r:
        out.append("GitHub not checked yet for releases (Check GitHub).")
        return out
    out.append(f"Repository: {r['repo']} (checked {r['checked']})")
    rel = r.get("release")
    if rel:
        out.append(f"Latest release: {rel['tag']} ({rel['at'][:10]}): "
                   + (", ".join(rel["assets"]) or "no files"))
        if r.get("changed"):
            out.append(f"{len(r['changed'])} file(s) in the download changed since: "
                       + ", ".join(r["changed"][:8]) + (" …" if len(r["changed"]) > 8 else ""))
    else:
        out.append("No release yet.")
    return out


def audit(reg, statuses: dict, placements: dict, snap: dict | None = None,
          progress=lambda m: None) -> list[dict]:
    """Notes and warnings (no actions) about downloads, for public Releasable
    software that is a whole repository.  Stores sw['release_state']."""
    issues = []
    for name, sw in sorted(reg.software.items()):
        if statuses.get(name, {}).get("group") not in model.RELEASABLE_GROUPS:
            continue
        loc = _whole_public_repo(placements.get(name, []))
        if not loc:
            continue
        repo = loc["repo"]
        progress(f"Releases of {repo}")
        local = setup_info(sw["path"])
        rel = latest_release(repo)
        changed = affects_package(changed_since(repo, rel["tag"]), sw) if rel else []
        sw["release_state"] = {"repo": repo, "checked": model.now(), "setup": bool(local),
                               "release": rel, "changed": changed}
        base = {"repo": repo, "path": "", "branches": loc.get("branches", []),
                "software": name, "action": None}
        branch_files = [] if snap is None else [
            f for b in (snap.get(repo) or {}).get("branches", {}).values() for f in b]
        pushed = local and (snap is None or f"{WORKFLOW_DIR}/{local['workflow']}" in branch_files)
        python = bool((sw.get("scan") or {}).get("languages", {}).get("Python"))
        if not local and not rel:
            if python or any(p in release_platforms(sw) for p in DESKTOPS):
                how = ("Set it up on the GitHub tab (Set up downloads…) or with "
                       f"`classifier release {name} --setup`." if python else
                       "Add a workflow that creates a GitHub release.")
                issues.append({**base, "level": "warn",
                               "message": f"{name} is public in {repo} but has no downloads: "
                                          f"no release build is set up. {how}"})
            continue
        if local and not pushed:
            issues.append({**base, "level": "note",
                           "message": f"{name}: the release build in your folder is not on "
                                      f"GitHub yet. Commit and push, then build a release."})
        elif local and not rel:
            issues.append({**base, "level": "warn",
                           "message": f"{name}: a release build is set up in {repo} but no "
                                      "release has been built, so there is nothing to "
                                      "download. Build a release (GitHub tab, or "
                                      f"`classifier release {name} --build`)."})
        if rel and changed:
            issues.append({**base, "level": "note",
                           "message": f"{name}: the latest release ({rel['tag']}, "
                                      f"{rel['at'][:10]}) is older than its code: "
                                      f"{len(changed)} file(s) that go into the download changed since ("
                                      + ", ".join(changed[:4]) + (" …" if len(changed) > 4 else "")
                                      + "). Build a new release to bring the downloads up "
                                        "to date."})
        if rel:
            have = " ".join(rel.get("assets", [])).lower()
            exts = {"Windows": (".exe", ".msi"), "macOS": (".dmg", ".pkg"),
                    "Linux": (".appimage", ".deb", ".rpm", ".flatpak")}
            missing = [p for p in release_platforms(sw) if not any(e in have for e in exts[p])]
            if missing:
                issues.append({**base, "level": "note",
                               "message": f"{name}: its latest release has no download for "
                                          + ", ".join(missing) + ", where it is Supported."})
        if local:
            for p in problems_when_packaged(sw):
                issues.append({**base, "level": "warn",
                               "message": f"{name} (downloads): {p}"})
    return issues


# --------------------------------------------------------------- building

def next_version(tag: str | None) -> str:
    if not tag:
        return "1.0.0"
    m = re.match(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", tag)
    if not m:
        return "1.0.0"
    a, b, c = (int(x or 0) for x in m.groups())
    return f"{a}.{b}.{c + 1}"


def start_build(repo: str, workflow: str, version: str, version_input: bool = True) -> int:
    """Run the release workflow; returns the run's id."""
    from . import github
    t0 = datetime.now(timezone.utc).timestamp() - 10
    args = ["workflow", "run", workflow, "-R", repo]
    if version_input:
        args += ["-f", f"version={version}"]
    github.gh(*args)
    for _ in range(20):
        time.sleep(3)
        raw = github.gh("run", "list", "-R", repo, "--workflow", workflow, "-L", "5",
                        "--json", "databaseId,createdAt,event")
        for r in json.loads(raw or "[]"):
            at = datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")).timestamp()
            if r.get("event") == "workflow_dispatch" and at >= t0:
                return int(r["databaseId"])
    raise github.GhError("The build was asked for, but GitHub did not show it within a "
                         "minute; look under the repository's Actions tab.")


def run_url(repo: str, run_id: int) -> str:
    return f"https://github.com/{repo}/actions/runs/{run_id}"


def wait_build(repo: str, run_id: int, progress=lambda m: None, timeout: int = 5400,
               every: int = 30) -> dict:
    """Wait for a run to finish: {'conclusion', 'jobs': ['name: conclusion']}."""
    from . import github
    start = time.time()
    while True:
        raw = github.gh("run", "view", str(run_id), "-R", repo, "--json",
                        "status,conclusion,jobs")
        r = json.loads(raw)
        jobs = [f"{j['name']}: {j.get('conclusion') or j.get('status')}" for j in r["jobs"]]
        if r["status"] == "completed":
            return {"conclusion": r.get("conclusion") or "", "jobs": jobs}
        done = sum(1 for j in r["jobs"] if j.get("status") == "completed")
        progress(f"Building the release: {done}/{len(r['jobs']) or '?'} jobs done "
                 f"({int(time.time() - start) // 60} min)")
        if time.time() - start > timeout:
            return {"conclusion": "still running", "jobs": jobs}
        time.sleep(every)


def build_and_catalogue(reg, sw: dict, repo: str, version: str, progress=print) -> str:
    """Start a release build, wait for it, then republish the catalogue."""
    from . import catalogue
    info = setup_info(sw["path"])
    if not info:
        raise ValueError(f"{sw['name']} has no release build set up.")
    if not info["dispatch"]:
        raise ValueError(f"{info['workflow']} cannot be started by hand (no "
                         "workflow_dispatch); push a version tag instead.")
    progress(f"Starting the release build {version} of {repo}…")
    rid = start_build(repo, info["workflow"], version, info["version_input"])
    progress(f"Building: {run_url(repo, rid)}")
    res = wait_build(repo, rid, progress)
    lines = [f"Release build {version} of {repo}: {res['conclusion']} ({run_url(repo, rid)})"]
    lines += ["  " + j for j in res["jobs"]]
    if res["conclusion"] == "success":
        rel = latest_release(repo)
        if rel:
            lines.append("Downloads: " + ", ".join(rel["assets"]))
            sw["release_state"] = {"repo": repo, "checked": model.now(), "setup": True,
                                   "release": rel, "changed": []}
        if catalogue.auto_enabled(reg.settings):
            lines.append(catalogue.refresh(reg, progress=progress))
    return "\n".join(lines)
