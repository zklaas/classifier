"""Look through a software folder for anything whose provenance needs a word.

A scan cannot prove who wrote a line of code.  What it can do is find the
evidence that something came from elsewhere, and list every item whose origin
the licensor (or a Claude review) has to state:

  notice     copyright / licence notices naming someone other than the licensor
             or Claude, and SPDX tags
  vendored   minified or bundled files, vendor/third_party folders, nested
             LICENSE files (a sign of a copied component)
  dep        modules imported from outside the software and the standard
             library, and scripts loaded from CDNs (open-source sources when
             their licence is open; well-known ones are recognised)
  asset      images, sound, fonts, data, documents: things code does not
             produce by "following logic", grouped by folder and type
  author     git commit authors other than the licensor or Claude

Each finding has a stable `key`, which resolutions are stored under, so a
resolution survives rescans.  Some findings carry an `auto` resolution (a
recognised open-source dependency, icons drawn by the software's own
make_icon script) which the licensor may overrule.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import model

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "env", ".mypy_cache",
             ".pytest_cache", ".clearance-review", ".classifier-review", ".idea",
             ".vscode", ".tox", "site-packages"}
ENV_DIRS = {"node_modules"}      # installed, not written: reported once, not walked
VENDOR_DIRS = {"vendor", "vendored", "third_party", "thirdparty", "third-party",
               "external", "externals", "extern", "bower_components"}
CODE_EXT = model.CODE_EXT | {".bat", ".cmd", ".glsl", ".sql", ".r", ".pl", ".rb",
                             ".php", ".kt", ".swift", ".cs", ".vb", ".m", ".tsx", ".jsx"}
DOC_EXT = {".md", ".rst"}
CONFIG_EXT = {".json", ".toml", ".ini", ".cfg", ".yaml", ".yml", ".conf", ".desktop",
              ".service", ".spec", ".in"}
SMALL_CONFIG = 256 * 1024
ASSET_KIND = {}
for kind, exts in {
    "image": ".png .jpg .jpeg .gif .bmp .tif .tiff .webp .ico .svg .icns .xcf .psd",
    "sound": ".wav .mp3 .ogg .flac .aac .m4a .opus .mid .midi .sf2 .sfz",
    "video": ".mp4 .mkv .webm .avi .mov",
    "font": ".ttf .otf .woff .woff2 .eot .pfb",
    "document": ".pdf .doc .docx .odt .ppt .pptx .odp .epub .rtf",
    "spreadsheet": ".xls .xlsx .ods",
    "data": (".csv .tsv .xml .musicxml .mxl .geojson .kml .kmz .gpx .shp .shx .dbf "
             ".prj .cpg .gpkg .tif .nc .h5 .hdf5 .parquet .feather .npy .npz .pkl "
             ".sqlite .db .sqlite3 .zip .gz .tar .7z .bz2 .xz .bin .dat .aslx .txt "
             ".jsonl .ndjson .pbf .pmtiles .mbtiles .las .laz"),
    "binary": ".so .dll .dylib .exe .whl .jar .class .pyd .o .a",
}.items():
    for e in exts.split():
        ASSET_KIND[e] = kind
TEXT_DOC_NAMES = {"readme", "readme.txt", "license", "license.txt", "licence",
                  "licence.txt", "copying", "notice", "notice.txt", "changelog",
                  "changelog.txt", "authors", "authors.txt", "requirements.txt",
                  "third-party.md", "third-party.txt", "todo.txt", "credits.txt"}
GENERATOR_RE = re.compile(r"^(make|gen|generate|build|render|draw|create)[_-]", re.I)

NOTICE_RE = re.compile(
    r"(?im)^[^\n]{0,40}?\b(?:copyright|\(c\)|©)\s*(?:\(c\)|©)?\s*"
    r"((?:19|20)\d{2}(?:\s*[-–,]\s*(?:19|20)?\d{2})*)[ ,]*(?:by\s+)?([^\n]{0,100})")
SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.\-+ ()]+)")
LICENCE_PHRASES = [
    ("Permission is hereby granted, free of charge", "MIT"),
    ("Licensed under the Apache License", "Apache-2.0"),
    ("GNU Affero General Public License", "AGPL-3.0"),
    ("GNU Lesser General Public License", "LGPL"),
    ("GNU General Public License", "GPL"),
    ("Mozilla Public License", "MPL-2.0"),
    ("Redistribution and use in source and binary forms", "BSD"),
    ("@license", "?"),
    ("All rights reserved", "all-rights-reserved"),
    ("Creative Commons", "CC"),
]
CDN_RE = re.compile(r"""<(script|link)\b([^>]*)>""", re.I)
URL_ATTR = re.compile(r"""(?:src|href)=["'](https?://[^"']+)["']""", re.I)
INLINE_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.I | re.S)
JS_IMPORT = re.compile(
    r"""(?:^|[;\s])import\s+(?:[\w*{}\s,$]+\s+from\s+)?["']([^"']+)["']"""
    r"""|\bimport\(\s*["']([^"']+)["']\s*\)"""
    r"""|\brequire\(\s*["']([^"']+)["']\s*\)"""
    r"""|\bimportScripts\(\s*["']([^"']+)["']""", re.M)
NODE_BUILTINS = {"fs", "path", "os", "http", "https", "url", "util", "events", "stream",
                 "crypto", "child_process", "net", "zlib", "assert", "buffer", "readline",
                 "process", "worker_threads", "querystring", "timers", "tty", "dns"}
LANG_OF = {".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".jsx": "JavaScript",
           ".ts": "TypeScript", ".tsx": "TypeScript", ".html": "HTML", ".htm": "HTML",
           ".css": "CSS", ".sh": "Shell", ".ps1": "PowerShell", ".bat": "Batch",
           ".cmd": "Batch", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++",
           ".rs": "Rust", ".go": "Go", ".java": "Java", ".kt": "Kotlin", ".swift": "Swift",
           ".lua": "Lua", ".qml": "QML", ".glsl": "GLSL", ".sql": "SQL", ".r": "R",
           ".rb": "Ruby", ".php": "PHP", ".cs": "C#"}
# Hosts whose files are open by their own terms (auto guesses, overrulable).
KNOWN_HOSTS = {"fonts.googleapis.com": ("OFL-1.1", "Google Fonts: nearly all OFL-1.1 "
                                        "(a few Apache-2.0); check each family"),
               "fonts.gstatic.com": ("OFL-1.1", "Google Fonts files")}

# Licences of well-known Python distributions, by import name.
KNOWN_DEPS = {
    "PyQt6": "GPL-3.0-only", "PyQt5": "GPL-3.0-only", "PySide6": "LGPL-3.0-only",
    "numpy": "BSD-3-Clause", "scipy": "BSD-3-Clause", "pandas": "BSD-3-Clause",
    "pyworld": "MIT", "PIL": "MIT-CMU", "matplotlib": "LicenseRef-Matplotlib",
    "openpyxl": "MIT", "requests": "Apache-2.0", "urllib3": "MIT",
    "sounddevice": "MIT", "soundfile": "BSD-3-Clause", "pyaudio": "MIT",
    "mido": "MIT", "rtmidi": "MIT", "shapely": "BSD-3-Clause", "pyproj": "MIT",
    "fiona": "BSD-3-Clause", "rasterio": "BSD-3-Clause", "osgeo": "MIT",
    "cv2": "Apache-2.0", "yaml": "MIT", "psutil": "BSD-3-Clause",
    "websockets": "BSD-3-Clause", "aiohttp": "Apache-2.0", "mcp": "MIT",
    "anthropic": "MIT", "docx": "MIT", "pptx": "MIT", "reportlab": "BSD-3-Clause",
    "lxml": "BSD-3-Clause", "bs4": "MIT", "vtk": "BSD-3-Clause", "pyvista": "MIT",
    "httpx": "BSD-3-Clause", "pyqtgraph": "MIT", "netCDF4": "MIT",
    "xarray": "Apache-2.0", "geopandas": "BSD-3-Clause", "laspy": "BSD-2-Clause",
    "pytest": "MIT", "fastmcp": "Apache-2.0", "pydantic": "MIT", "jinja2": "BSD-3-Clause",
    "flask": "BSD-3-Clause", "sklearn": "BSD-3-Clause", "networkx": "BSD-3-Clause",
    "sympy": "BSD-3-Clause", "music21": "BSD-3-Clause", "librosa": "ISC",
    "torch": "BSD-3-Clause", "imageio": "BSD-2-Clause", "tqdm": "MIT AND MPL-2.0",
    "pyarrow": "Apache-2.0", "pyogrio": "MIT", "send2trash": "BSD-3-Clause",
    "tifffile": "BSD-3-Clause", "imageio_ffmpeg": "BSD-2-Clause", "affine": "BSD-3-Clause",
    "fontTools": "MIT", "fsspec": "BSD-3-Clause", "paramiko": "LGPL-2.1-or-later",
    "contourpy": "BSD-3-Clause", "owslib": "BSD-3-Clause", "pystac_client": "Apache-2.0",
    "cairosvg": "LGPL-3.0-or-later", "planetary_computer": "MIT", "azure": "MIT",
    "erddapy": "BSD-3-Clause", "cdsapi": "Apache-2.0", "DracoPy": "Apache-2.0",
    "databricks": "Apache-2.0", "pmtiles": "BSD-3-Clause", "PyQt6_WebEngine": "GPL-3.0-only",
}
# Well-known bundled web libraries, by a word in the file name.
KNOWN_BUNDLES = {
    "opensheetmusicdisplay": "BSD-3-Clause", "jquery": "MIT", "d3": "ISC",
    "three": "MIT", "leaflet": "BSD-2-Clause", "ol": "BSD-2-Clause",
    "openlayers": "BSD-2-Clause", "chart": "MIT", "plotly": "MIT", "proj4": "MIT",
    "maplibre-gl": "BSD-3-Clause", "bootstrap": "MIT", "vexflow": "MIT",
    "tone": "MIT", "abcjs": "MIT", "codemirror": "MIT", "marked": "MIT",
    "pmtiles": "BSD-3-Clause", "katex": "MIT", "mathjax": "Apache-2.0",
    "pyodide": "MPL-2.0",
}


def _js_pkg(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def _npm_licence(root: Path, pkg: str) -> str:
    pj = root / "node_modules" / pkg / "package.json"
    try:
        lic = json.loads(pj.read_text(encoding="utf-8")).get("license", "")
        return lic if isinstance(lic, str) else ""
    except (OSError, ValueError):
        return KNOWN_BUNDLES.get(pkg.split("/")[-1].lower(), "")


def _bundle_licence(rel: str):
    stem = Path(rel).name.lower().split(".")[0]
    lic = KNOWN_BUNDLES.get(stem)
    return ({"kind": "opensource", "licence": lic, "note": f"recognised library ({stem})",
             "by": "auto"} if lic else None)


COPYLEFT = ("GPL", "AGPL")       # LGPL contains "GPL" too; handled in licences.recommend


def _walk(root: Path):
    """(relative path, absolute path) of every file a release could carry.

    In a git working tree the listing is git's own (tracked + untracked, minus
    .gitignore), which is what would be pushed."""
    if (root / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached",
                                "--others", "--exclude-standard"],
                               capture_output=True, timeout=120)
            if r.returncode == 0:
                for rel in sorted(set(r.stdout.decode("utf-8", "replace").split("\0"))):
                    if not rel:
                        continue
                    parts = rel.split("/")
                    if any(p in SKIP_DIRS for p in parts[:-1]):
                        continue
                    p = root / rel
                    if p.is_file():
                        yield rel, p
                return
        except (OSError, subprocess.SubprocessError):
            pass
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and d not in ENV_DIRS
                         and not (d.startswith(".") and d not in {".github"}))
        for f in sorted(files):
            p = Path(dirpath) / f
            yield p.relative_to(root).as_posix(), p


def fingerprint(root: Path) -> str:
    h = hashlib.sha1()
    for rel, p in _walk(root):
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{rel}\0{st.st_size}\0{int(st.st_mtime)}\n".encode())
    return h.hexdigest()


def _is_self(text: str, owners: list[str], ai: str, maker: str) -> bool:
    t = text.lower()
    names = [o.lower() for o in owners] + [ai.lower(), maker.lower()]
    return any(n and n in t for n in names)


def _read_text(p: Path, limit: int = 2_000_000) -> str | None:
    try:
        if p.stat().st_size > limit:
            return None
        b = p.read_bytes()
    except OSError:
        return None
    if b"\0" in b[:4096]:
        return None
    return b.decode("utf-8", "replace")


def _py_imports(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def scan(root: Path | str, owners: list[str], ai: str = "Claude",
         maker: str = "Anthropic", progress=None) -> dict:
    root = Path(root)
    findings: dict[str, dict] = {}
    files = list(_walk(root))
    local_modules = set()
    for rel, p in files:
        parts = rel.split("/")
        if p.suffix == ".py":
            local_modules.add(p.stem)
        for d in parts[:-1]:
            local_modules.add(d)
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    imports: dict[str, list[str]] = {}
    generators = []        # (relative path, text) of scripts named make_*/gen_*...
    code_texts = []
    asset_groups: dict[str, dict] = {}
    counted = {"code": 0, "doc": 0, "config": 0, "asset": 0, "other": 0}
    languages: dict[str, int] = {}
    web_pages = []          # HTML files with scripts

    def js_deps(code: str, rel: str):
        for m in JS_IMPORT.finditer(code):
            spec = next(g for g in m.groups() if g)
            if spec.startswith(("http://", "https://")):
                host_path = re.sub(r"^https?://", "", spec).split("?")[0]
                add(f"dep:cdn:{host_path}", "dep", f"Loaded from the web: {host_path}",
                    "A module fetched from the web at run time. Name its licence.", [rel])
            elif not spec.startswith((".", "/")) and spec.split(":")[0] not in {"node", "data"}:
                pkg = _js_pkg(spec)
                if pkg in NODE_BUILTINS:
                    continue
                lic = _npm_licence(root, pkg)
                add(f"dep:js:{pkg}", "dep", f"JavaScript package {pkg}",
                    "Imported by JavaScript code, not copied." + (f" Licence: {lic}." if lic
                                                                   else " Name its licence."),
                    [rel], auto=({"kind": "opensource", "licence": lic,
                                  "note": "from the package's own package.json"
                                  if (root / "node_modules" / pkg).exists() else
                                  "recognised library", "by": "auto"}
                                 if lic and model.is_open_licence(lic) else None))

    def add(key, kind, title, detail, paths, auto=None):
        f = findings.setdefault(key, {"key": key, "kind": kind, "title": title,
                                      "detail": detail, "paths": [], "auto": auto})
        for x in paths:
            if x not in f["paths"] and len(f["paths"]) < 200:
                f["paths"].append(x)
        f["count"] = f.get("count", 0) + len(paths)

    if any((root / d).is_dir() for d in ENV_DIRS):
        add("vendored:node_modules", "vendored", "node_modules folder (installed packages)",
            "Installed JavaScript packages. Third-party code: keep it out of the release "
            "(Excluded) or name the licences.", ["node_modules/"])

    for i, (rel, p) in enumerate(files):
        if progress and i % 200 == 0:
            progress(i, len(files))
        name, ext = p.name.lower(), p.suffix.lower()
        parts = rel.split("/")
        in_vendor = [d for d in parts[:-1] if d.lower() in VENDOR_DIRS]
        if in_vendor:
            vd = "/".join(parts[:parts.index(in_vendor[0]) + 1])
            add(f"vendored:{vd}", "vendored", f"Third-party folder {vd}/",
                "A vendor / third_party folder usually holds code copied from elsewhere.",
                [rel])
            continue
        if name.endswith((".min.js", ".min.css", ".bundle.js")):
            add(f"vendored:{rel}", "vendored", f"Minified / bundled file {rel}",
                "Minified files are almost always a third-party library.", [rel],
                auto=_bundle_licence(rel))
            continue
        if (name.split(".")[0] in {"license", "licence", "copying"} and len(parts) > 1):
            add(f"vendored:licence:{rel}", "vendored", f"Licence file inside {'/'.join(parts[:-1])}/",
                "A licence file below the top folder marks a component taken from elsewhere.",
                [rel])

        is_code, is_doc = ext in CODE_EXT, ext in DOC_EXT or name in TEXT_DOC_NAMES
        is_config = ext in CONFIG_EXT
        kind = ASSET_KIND.get(ext)
        if name in TEXT_DOC_NAMES:
            kind = None
        if is_config:
            try:
                big = p.stat().st_size > SMALL_CONFIG
            except OSError:
                big = False
            if big:
                kind = "data"
        if kind and not is_code:
            counted["asset"] += 1
            folder = "/".join(parts[:-1]) or "."
            g = asset_groups.setdefault(f"asset:{folder}/*{ext or name}",
                                        {"kind": kind, "folder": folder, "ext": ext or name,
                                         "paths": []})
            g["paths"].append(rel)
            continue
        counted["code" if is_code else "doc" if is_doc else "config" if is_config else "other"] += 1
        if not (is_code or is_doc or is_config or ext in {"", ".txt"}):
            continue
        text = _read_text(p)
        if text is None:
            continue
        if is_code:
            lang = LANG_OF.get(ext)
            n_lines = text.count("\n") + 1
            if ext in {".html", ".htm"}:
                inline = INLINE_SCRIPT.findall(text)
                if inline:
                    web_pages.append(rel)
                    js_lines = sum(c.count("\n") + 1 for c in inline)
                    languages["JavaScript"] = languages.get("JavaScript", 0) + js_lines
                    n_lines -= js_lines
                    for code in inline:
                        js_deps(code, rel)
            if lang:
                languages[lang] = languages.get(lang, 0) + max(n_lines, 0)
            if ext in {".js", ".mjs", ".ts", ".jsx", ".tsx"}:
                js_deps(text, rel)
            code_texts.append((rel, text))
            if GENERATOR_RE.match(p.name):
                generators.append((rel, text))
            if ext == ".py":
                for m in _py_imports(text) - stdlib - local_modules:
                    imports.setdefault(m, []).append(rel)
            if ext in {".html", ".htm"}:
                for tag, attrs in CDN_RE.findall(text):
                    if re.search(r"rel=[\"']?(preconnect|dns-prefetch|canonical|icon)", attrs, re.I):
                        continue
                    u = URL_ATTR.search(attrs)
                    if not u:
                        continue
                    lib = re.sub(r"^https?://", "", u.group(1)).split("?")[0]
                    host = lib.split("/")[0]
                    known = KNOWN_HOSTS.get(host)
                    add(f"dep:cdn:{lib}", "dep", f"Loaded from the web: {lib}",
                        f"A {'script' if tag.lower() == 'script' else 'stylesheet or font'} "
                        "fetched at run time. Name its licence.", [rel],
                        auto=({"kind": "opensource", "licence": known[0], "note": known[1],
                               "by": "auto"} if known else _bundle_licence(lib.split("/")[-1])))
            if ext in {".js", ".css"} and any(len(ln) > 2000 for ln in text.splitlines()[:50]):
                add(f"vendored:{rel}", "vendored", f"Bundled / minified code in {rel}",
                    "Very long lines near the top suggest bundled third-party code.", [rel])
        root_licence = len(parts) == 1 and name.split(".")[0] in {"license", "licence", "copying"}
        for m in NOTICE_RE.finditer(text):
            holder = (m.group(2) or "").strip(" .,*#/-\"'")
            line = m.group(0).strip()
            if _is_self(line, owners, ai, maker):
                continue
            if root_licence and "free software foundation" in line.lower():
                continue          # the FSF's copyright on the GPL text itself
            add(f"notice:{rel}", "notice", f"Copyright notice in {rel}",
                f"Names {holder or 'someone'} ({m.group(1)}), not the licensor or {ai}: "
                f"“{line[:120]}”", [rel])
        for m in SPDX_RE.finditer(text):
            if root_licence:
                break
            add(f"notice:{rel}", "notice", f"Licence tag in {rel}",
                f"SPDX-License-Identifier: {m.group(1).strip()}: code under a stated licence, "
                "often copied from another project.", [rel])
        if not root_licence and is_code:
            for phrase, lic in LICENCE_PHRASES:
                if phrase.lower() in text.lower() and not _is_self(text[:4000], owners, ai, maker):
                    add(f"notice:{rel}", "notice", f"Licence wording in {rel}",
                        f"Contains “{phrase}” ({lic}), which usually marks code taken from "
                        "another project. If it is only mentioned (e.g. in a licence "
                        "generator), resolve it as your own.", [rel])
                    break

    # package.json dependencies
    for rel, p in files:
        if p.name != "package.json" or "node_modules" in rel:
            continue
        try:
            pj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for section in ("dependencies", "devDependencies", "peerDependencies",
                        "optionalDependencies"):
            for pkg in (pj.get(section) or {}):
                lic = _npm_licence(root, pkg)
                add(f"dep:js:{pkg}", "dep", f"JavaScript package {pkg}",
                    f"Listed in {rel} ({section}"
                    + ("; used only to build or test" if section == "devDependencies" else "")
                    + ")." + (f" Licence: {lic}." if lic else " Name its licence."), [rel],
                    auto=({"kind": "opensource", "licence": lic, "note": "package.json",
                           "by": "auto"} if lic and model.is_open_licence(lic) else None))

    # imports from outside the software
    for mod, where in sorted(imports.items()):
        lic = KNOWN_DEPS.get(mod)
        auto = ({"kind": "opensource", "licence": lic, "note": "recognised package",
                 "by": "auto"} if lic else None)
        add(f"dep:py:{mod}", "dep", f"Python package {mod}",
            (f"Imported, not copied. Recognised licence: {lic}." if lic else
             "Imported from outside the software. Name its licence (pip show / its "
             "project page) or mark it Excluded if it is optional."), where, auto=auto)

    # assets, with icons drawn by the software's own scripts recognised
    for key, g in sorted(asset_groups.items()):
        auto = None
        names = {Path(x).name for x in g["paths"]}
        stems = {Path(x).stem for x in g["paths"]}
        for grel, gtext in generators:
            hit = any(n in gtext for n in names) or (
                g["kind"] == "image" and (g["folder"].split("/")[-1] in {"icons", "icon", "img"}
                                          and "icon" in grel.lower()))
            if hit or any(s in gtext and len(s) > 3 for s in stems if g["kind"] == "image"):
                auto = {"kind": "generated", "note": f"made by {grel}", "by": "auto"}
                break
        n = len(g["paths"])
        add(key, "asset",
            f"{n} {g['kind']} file{'s' if n != 1 else ''} ({g['ext']}) in {g['folder']}/",
            {"image": "Pictures are not code: say who drew them, or their licence.",
             "sound": "Recordings / MIDI: say who made them, or their licence.",
             "font": "Fonts carry their own licence (OFL-1.1 is open).",
             "data": "Data files: say where they came from and under which licence.",
             "document": "Documents: say who wrote them, or their licence.",
             "spreadsheet": "Spreadsheets: say who made them, or their licence.",
             "video": "Video: say who made it, or its licence.",
             "binary": "Compiled binaries cannot be read: name their source and licence.",
             }.get(g["kind"], ""), g["paths"], auto=auto)

    # git authors
    if (root / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(root), "log", "--format=%an <%ae>%n%(trailers:key=Co-authored-by,valueonly)"],
                               capture_output=True, text=True, timeout=120)
            seen = sorted({ln.strip() for ln in r.stdout.splitlines() if ln.strip()})
            for who in seen:
                if _is_self(who, owners, ai, maker) or "noreply@anthropic.com" in who:
                    continue
                add(f"author:{who}", "author", f"Git author {who}",
                    "Commits by someone other than the licensor or Claude. If this is "
                    "another name of yours, add it under Settings > Your other names.",
                    [".git"])
        except (OSError, subprocess.SubprocessError):
            pass

    return {
        "at": model.now(),
        "fingerprint": fingerprint(root),
        "files": len(files),
        "counts": counted,
        "findings": list(findings.values()),
        "python_deps": sorted(imports),
        "languages": dict(sorted(languages.items(), key=lambda kv: -kv[1])),
        "web_pages": web_pages,
    }
