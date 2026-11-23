"""python3 -m unittest test_classifier   (no network; GitHub is a fake snapshot)"""

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["CLASSIFIER_CONFIG"] = tempfile.mkdtemp()

from classifier import github, licences, model, platforms, review, scanner, status  # noqa: E402

OWNERS = ["Test Person", "tperson"]


def make_soft(root: Path, name: str, files: dict) -> Path:
    d = root / name
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text) if isinstance(text, str) else p.write_bytes(text)
    return d


class Groups(unittest.TestCase):
    def test_table(self):
        g = model.auto_group
        self.assertEqual(g("finished", False, True), model.RELEASABLE)
        self.assertEqual(g("finished", False, False), model.FINISHED_PRIVATE)
        self.assertEqual(g("finished", True, True), model.RELEASABLE_SUITE)
        self.assertEqual(g("development", True, False), model.SUITE_PRIVATE)
        self.assertEqual(g("development", False, True), model.DEV_PRIVATE)
        self.assertEqual(g("unset", False, True), model.UNCLASSIFIED)

    def test_override_cannot_release_uncleared(self):
        sw = model.new_software("x", "/x")
        sw.update(stage="finished", group_override=model.RELEASABLE)
        self.assertEqual(model.group(sw, cleared=False)[0], model.FINISHED_PRIVATE)
        sw["group_override"] = model.DEV_PRIVATE
        self.assertEqual(model.group(sw, cleared=True)[0], model.DEV_PRIVATE)

    def test_open_licences(self):
        for ok in ("MIT", "GPL-3.0-or-later", "MIT AND MPL-2.0", "CC-BY-SA-4.0", "GPL-3.0+"):
            self.assertTrue(model.is_open_licence(ok), ok)
        for bad in ("", "CC-BY-NC-4.0", "Proprietary", "all-rights-reserved"):
            self.assertFalse(model.is_open_licence(bad), bad)


class Scan(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_findings_and_clearance(self):
        d = make_soft(self.tmp, "App", {
            "app.py": "import json\nimport numpy\nimport helper\nfrom PyQt6 import QtCore\n",
            "helper.py": "# Copyright (c) 2026 Test Person\nx = 1\n",
            "copied.py": "# Copyright (c) 2011 Someone Else\n# All rights reserved\n",
            "make_icon.py": "img.save('icons/app.png')\n",
            "icons/app.png": b"\x89PNG\0",
            "samples/song.mid": b"MThd\0",
            "static/jquery.min.js": "!function(){}",
            "vendor/lib.py": "pass\n",
        })
        s = scanner.scan(d, OWNERS)
        keys = {f["key"]: f for f in s["findings"]}
        self.assertIn("notice:copied.py", keys)
        self.assertNotIn("notice:helper.py", keys)          # the licensor's own notice
        self.assertIn("dep:py:numpy", keys)
        self.assertEqual(keys["dep:py:numpy"]["auto"]["licence"], "BSD-3-Clause")
        self.assertIn("dep:py:PyQt6", keys)
        self.assertNotIn("dep:py:json", keys)               # standard library
        self.assertNotIn("dep:py:helper", keys)             # local module
        self.assertEqual(keys["asset:icons/*.png"]["auto"]["kind"], "generated")
        self.assertIsNone(keys["asset:samples/*.mid"]["auto"])
        self.assertEqual(keys["vendored:static/jquery.min.js"]["auto"]["licence"], "MIT")
        self.assertIn("vendored:vendor", keys)

        sw = model.new_software("App", str(d))
        sw["scan"] = s
        self.assertFalse(model.clearance(sw, s["fingerprint"])[0])
        sw["resolutions"]["notice:copied.py"] = {"kind": "restricted"}
        self.assertIn("not open-source", model.clearance(sw, s["fingerprint"])[1])
        sw["resolutions"]["notice:copied.py"] = {"kind": "excluded"}
        sw["resolutions"]["asset:samples/*.mid"] = {"kind": "self"}
        sw["resolutions"]["vendored:vendor"] = {"kind": "opensource", "licence": "CC-BY-NC-4.0"}
        self.assertFalse(model.clearance(sw, s["fingerprint"])[0])   # NC is not open
        sw["resolutions"]["vendored:vendor"]["licence"] = "MIT"
        self.assertEqual(model.clearance(sw, s["fingerprint"])[1],
                         "Provenance not yet confirmed by the licensor")
        sw["attested"] = True
        self.assertTrue(model.clearance(sw, s["fingerprint"])[0])
        self.assertFalse(model.clearance(sw, "changed")[0])          # stale scan
        self.assertEqual(licences.recommend(sw)[0], "GPL-3.0-or-later")  # PyQt6 is GPL

    def test_claude_review_items(self):
        d = make_soft(self.tmp, "B", {"b.py": "x = 1\n"})
        sw = model.new_software("B", str(d))
        sw["scan"] = scanner.scan(d, OWNERS)
        sw["attested"] = True
        self.assertTrue(model.clearance(sw)[0])
        (d / review.DIR).mkdir()
        (d / review.DIR / "review.json").write_text(json.dumps({"summary": "s", "items": [
            {"key": "file:b.py", "verdict": "unsure", "reason": "looks copied"}]}))
        sw["claude_review"] = review.load(sw)
        self.assertFalse(model.clearance(sw)[0])            # Claude's new item blocks
        sw["claude_review"]["items"][0]["verdict"] = "self"
        self.assertEqual(review.accept(sw), 1)
        self.assertTrue(model.clearance(sw)[0])
        self.assertIn("file:b.py", review.brief(sw, {"licensor": "T"}) or "file:b.py")


class JavaScriptAndPlatforms(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_js_deps_and_web_game(self):
        d = make_soft(self.tmp, "Game", {
            "game.html": ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
                          '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=X">\n'
                          '<script>\nimport * as T from "three";\nconst m = require("./mine.js");\n'
                          'addEventListener("keydown", e => {});\n</script>\n'),
            "mine.js": 'import x from "@scope/pkg/sub";\nimport fs from "fs";\n',
            "package.json": '{"dependencies": {"left-pad": "1"}, "devDependencies": {"vite": "5"}}',
            "node_modules/left-pad/package.json": '{"license": "WTFPL"}',
            "wrap.py": "from PyQt6 import QtCore\n",
        })
        s = scanner.scan(d, OWNERS)
        keys = {f["key"]: f for f in s["findings"]}
        self.assertIn("dep:js:three", keys)
        self.assertEqual(keys["dep:js:three"]["auto"]["licence"], "MIT")
        self.assertIn("dep:js:@scope/pkg", keys)
        self.assertNotIn("dep:js:fs", keys)                  # Node built-in
        self.assertNotIn("dep:js:./mine.js", keys)           # the software's own
        self.assertIsNone(keys["dep:js:left-pad"]["auto"])   # WTFPL is not on the open list
        self.assertIn("dep:js:vite", keys)
        self.assertEqual(keys["dep:cdn:fonts.googleapis.com/css2"]["auto"]["licence"], "OFL-1.1")
        self.assertEqual(len([k for k in keys if k.startswith("dep:cdn:")]), 1)  # no preconnect
        self.assertIn("JavaScript", s["languages"])
        r = platforms.review(d, s["python_deps"])
        self.assertTrue(r["web"])
        for p in platforms.PLATFORMS:
            self.assertTrue(any("browser" in e for e in r["evidence"][p]), p)
        self.assertTrue(any("keyboard-only" in b for b in r["blockers"]["Android"]))

    def test_frontend_needs_server(self):
        d = make_soft(self.tmp, "Served", {
            "index.html": '<script>fetch("/api/x")</script>',
            "server.py": "import http.server\nimport fcntl\n"})
        s = scanner.scan(d, OWNERS)
        r = platforms.review(d, s["python_deps"])
        self.assertFalse(r["web"])
        self.assertFalse(r["evidence"]["Android"])
        self.assertTrue(any("Unix-only" in b for b in r["blockers"]["Windows"]))

    def test_handled_linux_bits_are_not_blockers(self):
        d = make_soft(self.tmp, "Handled", {
            "make_icon.py": 'FONT = "/usr/share/fonts/x.ttf"\n',              # developer tool
            "app.py": ('import sys\nMACOS = sys.platform == "darwin"\n'
                       'def show(p):\n    opener = "open" if MACOS else "xdg-open"\n'),
            "sf.py": ('SF = ["/usr/share/sounds/a.sf2",\n      "/usr/share/sounds/b.sf2"]\n'),
            "need.py": 'CFG = open("/etc/needed.conf").read()\n'})
        r = platforms.review(d, [])
        self.assertEqual(r["blockers"]["macOS"], ["fixed Linux system paths (need.py)"])

    def test_supported_with_blockers_warns(self):
        sw = model.new_software("x", "/x")
        sw["platforms"] = {"Windows": {"track": "supported"}}
        sw["platform_review"] = {"evidence": {}, "blockers": {"Windows": ["reads /proc"]}}
        self.assertTrue(platforms.warnings(sw))


class AIUse(unittest.TestCase):
    def test_levels(self):
        from classifier import aiuse
        tmp = Path(tempfile.mkdtemp())
        files = {f"m{i}.py": "x = 1\n" for i in range(6)}
        none = make_soft(tmp, "None", {"none_app.py": "print(1)\n", **files})
        self.assertEqual(aiuse.review(none)["suggested"], "none")
        opt = make_soft(tmp, "Opt", {
            "opt_app.py": "import extra\n", **files,
            "extra.py": ("import shutil\ndef run():\n    if shutil.which('claude-docker') is None:"
                         "\n        return\n"),
            "test_x.py": "import anthropic\n",                 # tests do not count
            "pats.py": 'P = r"api\\.openai\\.com"\n'})      # a pattern, not a use
        r = aiuse.review(opt)
        self.assertEqual(r["suggested"], "optional")
        self.assertEqual(r["systems"], ["Claude (in a container)"])
        req = make_soft(tmp, "Req", {"req_app.py": "import anthropic\n",
                                     "b.py": "from anthropic import Anthropic\n"})
        self.assertEqual(aiuse.review(req)["suggested"], "required")
        mcp = make_soft(tmp, "Srv", {"srv.py": "from mcp.server import Server\n"})
        self.assertEqual(aiuse.review(mcp)["suggested"], "for_ai")
        sw = model.new_software("Opt", str(opt))
        sw["ai_review"] = r
        sw["ai"] = {"level": "", "what": "reviews provenance"}
        self.assertEqual(aiuse.describe(sw),
                         "Runs without AI; an optional part connects to Claude "
                         "(reviews provenance).")
        lic = licences.build("MIT", "T", "Claude", "Anthropic", "Opt",
                             ai_use=aiuse.describe(sw))
        self.assertIn("Use of AI when running", lic)
        self.assertEqual(licences.detect(lic), "MIT")


class OtherAISystems(unittest.TestCase):
    def test_many_systems_and_user_table(self):
        import importlib
        from classifier import aiuse
        tmp = Path(tempfile.mkdtemp())
        d = make_soft(tmp, "Multi", {
            "multi_app.py": "import core\n", **{f"m{i}.py": "x=1\n" for i in range(8)},
            "core.py": "import openai\nimport google.genai\n",
            "local.py": "import subprocess\nsubprocess.run(['ollama', 'run', 'x'])\n",
            "keys.py": "import os\nK = os.environ.get('DEEPSEEK_API_KEY')\n",
            "mine.py": "import acme_brain\n",
            "web.js": 'import { generateText } from "ai";\n'})
        r = aiuse.review(d)
        for want in ("OpenAI (or an OpenAI-compatible service)", "Gemini", "DeepSeek",
                     "a local model (Ollama)", "an AI framework (Vercel AI SDK)"):
            self.assertIn(want, r["systems"])
        self.assertNotIn("Acme Brain", r["systems"])
        cfg = Path(os.environ["CLASSIFIER_CONFIG"])
        (cfg / "ai-systems.json").write_text(json.dumps({"python": {"acme_brain": "Acme Brain"}}))
        aiuse.load_user_table()
        self.assertIn("Acme Brain", aiuse.review(d)["systems"])
        sw = model.new_software("Multi", str(d))
        sw["ai_review"] = {"suggested": "optional", "systems": ["Gemini",
                           "a local model (Ollama)"], "files": {}}
        self.assertIn("Gemini and a local model (Ollama)", aiuse.describe(sw))


class Catalogue(unittest.TestCase):
    def test_only_releasable_listed(self):
        from classifier import catalogue
        tmp = Path(tempfile.mkdtemp())
        reg = model.Registry(tmp / "reg.json")
        for name in ("Shown", "Hidden"):
            d = make_soft(tmp, name, {"README.md": f"# {name}\n\nThe {name} program.\n",
                                      f"{name.lower()}_app.py": "from PyQt6 import QtCore\n",
                                      "install.sh": "#!/bin/sh\n"})
            reg.add(str(d))
            reg.software[name]["scan"] = scanner.scan(d, OWNERS)
        reg.software["Shown"]["platforms"] = {"Linux": {"track": "supported"}}
        st = {"Shown": {"group": model.RELEASABLE, "licence_info": {"spdx": "MIT"}},
              "Hidden": {"group": model.FINISHED_PRIVATE}}
        # Releasable but not in a public repository: not listed
        self.assertNotIn("### Shown", catalogue.page(reg, st, {}))
        self.assertNotIn("### Shown", catalogue.page(reg, st, {"Shown": [
            {"repo": "me/shown", "path": "", "branches": ["main"], "visibility": "PRIVATE"}]}))
        text = catalogue.page(reg, st, {"Shown": [{"repo": "me/shown", "path": "",
                                                   "branches": ["main"],
                                                   "visibility": "PUBLIC"}]})
        self.assertIn("### Shown", text)
        self.assertIn("The Shown program.", text)
        self.assertNotIn("Hidden", text)
        self.assertIn("git clone https://github.com/me/shown.git", text)
        self.assertIn("bash install.sh", text)
        self.assertIn("Type these into a **terminal**", text)
        self.assertIn("Download ZIP", text)
        self.assertNotIn("untested", text)
        dl = catalogue.entry(reg.software["Shown"], st["Shown"], None,
                             [("Windows", "https://x/a.exe"), ("Linux", "https://x/a.AppImage")])
        self.assertIn("**Download the program** (executable: double-click to install and run, "
                      "nothing else needed): [Windows installer (.exe)](https://x/a.exe) · "
                      "[Linux app (.AppImage)](https://x/a.AppImage)", dl)
        self.assertIn("Or install from the source code, at the command line:", dl)
        self.assertIn("Install on Linux at the command line (from the source code)", dl)
        reg.settings["contact"] = "someone@example.org"
        self.assertIn("please write to someone@example.org",
                      catalogue.page(reg, st, {"Shown": [{"repo": "me/shown", "path": "",
                                                          "branches": ["main"], "visibility": "PUBLIC"}]}))
        self.assertIn("pip install --user PyQt6", text)


def _png(width: int) -> bytes:
    import struct
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, width) + b"\0" * 8


class Releases(unittest.TestCase):
    def _app(self, extra=None):
        tmp = Path(tempfile.mkdtemp())
        files = {
            "README.md": "# Deck Tool\n\nA small card program. It shuffles.\n\n## Use\n\nRun it.\n",
            "decktool.py": ("import os, sys\nfrom PyQt6 import QtWidgets\nimport helpers\n"
                            "HERE = os.path.dirname(os.path.abspath(__file__))\n"
                            "DECKS = os.path.join(HERE, 'decks')\n"
                            "SETTINGS = os.path.join(HERE, 'settings.json')\n"
                            "if __name__ == '__main__':\n    pass\n"),
            "helpers.py": "import sounddevice\n",
            "_check_all.py": ("HERE = __file__\nopen(HERE + '.log', 'w')\n"
                              "if __name__ == '__main__':\n    pass\n"),
            "make_icon.py": "if __name__ == '__main__':\n    pass\n",
            "decks/a.json": "{}", "notes/todo.txt": "x",
            "icons/decktool-256.png": _png(256), "icons/decktool-16.png": _png(16),
            "icons/decktool.ico": b"\0", "install.sh": "python3 decktool.py\n",
        }
        files.update(extra or {})
        d = make_soft(tmp, "DeckTool", files)
        sw = model.new_software("DeckTool", str(d))
        sw["scan"] = scanner.scan(d, OWNERS)
        sw["platforms"] = {"Windows": {"track": "supported"}, "Linux": {"track": "untested"}}
        return sw, d

    def test_setup_plan_and_write(self):
        from classifier import releases
        sw, d = self._app()
        self.assertIsNone(releases.setup_info(d))
        self.assertEqual(releases.entry_point(sw)[0], "decktool.py")
        plan = releases.plan_setup(sw, {"licensor": "Test Person", "contact": "t@example.org"},
                                   "me/decktool")
        self.assertEqual(plan["problems"], [])
        i = plan["info"]
        self.assertEqual(i["platforms"], ["Windows", "Linux"])
        self.assertEqual(i["data"], ["decks/", "icons/"])        # notes/ is not opened by name
        self.assertEqual(i["icons"]["png"], "icons/decktool-256.png")
        self.assertIn("sounddevice", i["collect"])
        self.assertTrue(i["gui"] and i["mic"])
        self.assertEqual(set(plan["files"]), {".github/workflows/release.yml",
                                              "packaging/decktool.iss", "packaging/release-notes.md"})
        wf = plan["files"][".github/workflows/release.yml"]
        self.assertIn("python -m pip install pyinstaller PyQt6 sounddevice", wf)
        self.assertIn('--add-data "decks${{ matrix.sep }}decks"', wf)
        self.assertIn("DeckTool-Linux-x86_64.AppImage", wf)
        self.assertNotIn("macos-latest", wf)
        self.assertIn("t@example.org", plan["files"]["packaging/release-notes.md"])
        # saving beside itself is found in what the program imports, not in a check script
        warn = " ".join(plan["warnings"])
        self.assertIn("saves files beside itself", warn)
        self.assertIn("decktool.py:6", warn)
        self.assertNotIn("_check_all", warn)
        self.assertIn("## Download\n", plan["readme"])
        self.assertLess(plan["readme"].index("## Download"), plan["readme"].index("## Use"))
        releases.write_setup(sw, plan)
        info = releases.setup_info(d)
        self.assertEqual(info["workflow"], "release.yml")
        self.assertTrue(info["dispatch"] and info["version_input"])
        again = releases.plan_setup(sw, {}, "me/decktool")
        self.assertTrue(any("already set up" in p for p in again["problems"]))
        self.assertIsNone(releases.plan_setup(sw, {}, "me/decktool", force=True)["readme"])
        try:
            import yaml
            yaml.safe_load(wf)
        except ImportError:
            pass

    def test_frozen_check_and_webengine(self):
        from classifier import releases
        sw, d = self._app({"decktool.py": (
            "import os, sys\nfrom PyQt6.QtWebEngineWidgets import QWebEngineView\n"
            "HERE = os.path.dirname(os.path.abspath(__file__))\n"
            "def data_dir():\n    if getattr(sys, 'frozen', False):\n"
            "        return os.path.expanduser('~/.local/share/DeckTool')\n"
            "    return os.path.join(HERE, 'data')\n"
            "if __name__ == '__main__':\n    pass\n")})
        probs = " ".join(releases.problems_when_packaged(sw))
        self.assertNotIn("beside itself", probs)               # it checks sys.frozen
        self.assertIn("QTWEBENGINE_DISABLE_SANDBOX", probs)
        plan = releases.plan_setup(sw, {}, "me/decktool")
        self.assertIn("PyQt6-WebEngine", plan["info"]["pip"])
        self.assertIn("QTWEBENGINE_DISABLE_SANDBOX", plan["files"][".github/workflows/release.yml"])

    def test_no_clear_entry_and_patterns_in_strings(self):
        from classifier import releases
        sw, d = self._app({"decktool.py": "x = 'QAudioSource QtWebEngine'  # QCamera\n",
                           "one.py": "if __name__ == '__main__':\n    pass\n",
                           "two.py": "if __name__ == '__main__':\n    pass\n"})
        self.assertIsNone(releases.entry_point(sw)[0])
        plan = releases.plan_setup(sw, {}, "me/decktool")
        self.assertTrue(any("Which script" in p for p in plan["problems"]))
        self.assertEqual(plan["files"], {})
        code = {"a.py": "PAT = 'QAudioSource|QtWebEngine'  # QCamera\n"}
        self.assertFalse(releases.uses(releases.MIC, code))
        self.assertFalse(releases.uses_webengine(code))

    def test_versions_docs_and_audit(self):
        from classifier import releases
        self.assertEqual(releases.next_version(None), "1.0.0")
        self.assertEqual(releases.next_version("v1.2.3"), "1.2.4")
        self.assertEqual(releases.next_version("v2"), "2.0.1")
        self.assertTrue(releases.is_doc("README.md") and releases.is_doc("packaging/x.iss"))
        self.assertFalse(releases.is_doc("app/gui.py"))
        sw, d = self._app()
        self.assertEqual(releases.affects_package(
            ["decktool.py", "helpers.py", "make_icon.py", "notes/todo.txt", "decks/b.json"], sw),
            ["decktool.py", "helpers.py", "decks/b.json"])
        reg = model.Registry(Path(tempfile.mkdtemp()) / "reg.json")
        reg.software["DeckTool"] = sw
        st = {"DeckTool": {"group": model.RELEASABLE}}
        placed = {"DeckTool": [{"repo": "me/decktool", "path": "", "branches": ["main"],
                                "visibility": "PUBLIC"}]}
        calls = []
        old_latest, old_changed = releases.latest_release, releases.changed_since
        try:
            releases.latest_release = lambda repo: calls.append(repo) or None
            releases.changed_since = lambda repo, tag: []
            issues = releases.audit(reg, st, placed, {"me/decktool": {"branches": {"main": []}}})
            self.assertEqual(len(issues), 1)
            self.assertIn("has no downloads", issues[0]["message"])
            self.assertEqual(releases.summary(sw), "none")
            self.assertEqual(releases.audit(reg, {"DeckTool": {"group": model.FINISHED_PRIVATE}},
                                            placed), [])
            releases.write_setup(sw, releases.plan_setup(sw, {}, "me/decktool"))
            issues = releases.audit(reg, st, placed, {"me/decktool": {"branches": {"main": []}}})
            self.assertTrue(any("not on GitHub yet" in i["message"] for i in issues))
            issues = releases.audit(reg, st, placed, {"me/decktool": {"branches": {
                "main": [".github/workflows/release.yml"]}}})
            self.assertTrue(any("no release has been built" in i["message"] for i in issues))
            releases.latest_release = lambda repo: {"tag": "v1.0.0", "at": "2026-01-02T00:00:00Z",
                                                    "assets": ["DeckTool-Setup-Windows.exe"]}
            releases.changed_since = lambda repo, tag: ["decktool.py", "README.md"]
            issues = releases.audit(reg, st, placed, None)
            msgs = " ".join(i["message"] for i in issues)
            self.assertIn("older than its code: 1 file(s)", msgs)
            self.assertIn("no download for Linux", msgs)
            self.assertEqual(releases.summary(sw), "v1.0.0 (older than code)")
            self.assertTrue(all(i["action"] is None for i in issues))
        finally:
            releases.latest_release, releases.changed_since = old_latest, old_changed


class Publish(unittest.TestCase):
    def test_plan_and_rules(self):
        import subprocess
        from classifier import publish
        tmp = Path(tempfile.mkdtemp())
        d = make_soft(tmp, "Pub", {
            "pub_app.py": "x=1\n", "data/big.json": "{}", "data/own.json": "{}",
            "__pycache__/a.pyc": b"\0", ".env": "SECRET=1", ".classifier-review/r.json": "{}"})
        sw = model.new_software("Pub", str(d))
        sw["scan"] = {"findings": [
            {"key": "asset:data/*.json", "kind": "asset", "title": "2 data files",
             "detail": "", "paths": ["data/big.json", "data/own.json"], "auto": None}]}
        sw["resolutions"] = {"asset:data/*.json": {"kind": "excluded"}}
        sw["claude_review"] = {"items": [{"key": "file:data/own.json", "verdict": "self"}]}
        sw["resolutions"]["file:data/own.json"] = {"kind": "self"}
        pl = publish.plan(sw)
        self.assertIn("pub_app.py", pl["include"])
        self.assertIn("data/own.json", pl["include"])          # the file's own choice wins
        self.assertEqual([r for r, _ in pl["excluded"]], ["data/big.json"])
        out = [r for r, _ in pl["always_out"]]
        self.assertIn(".env", out)
        self.assertTrue(any(r.startswith("__pycache__") for r in out))
        self.assertTrue(any(r.startswith(".classifier-review") for r in out))
        # who may go where
        b = publish.blockers
        self.assertTrue(b(sw, {"group": model.DEV_PRIVATE}))
        pub_repo = {"repo": "me/x", "exists": True, "visibility": "PUBLIC"}
        self.assertTrue(b(sw, {"group": model.FINISHED_PRIVATE}, pub_repo))
        self.assertFalse(b(sw, {"group": model.FINISHED_PRIVATE},
                           {**pub_repo, "visibility": "PRIVATE"}))
        self.assertTrue(b(sw, {"group": model.RELEASABLE, "cleared": True,
                               "licence": "missing"}))
        self.assertFalse(b(sw, {"group": model.RELEASABLE, "cleared": True, "licence": "ok"},
                           pub_repo))
        # a push to a local practice repository
        bare = tmp / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
        dest = {"repo": "me/pub", "path": "", "exists": True, "branches": [],
                "visibility": "PRIVATE"}
        publish.publish(sw, {"ai_system": "Claude"}, dest, remote_url=str(bare),
                        progress=lambda m: None)
        files = subprocess.run(["git", "--git-dir", str(bare), "ls-tree", "-r", "--name-only",
                                "main"], capture_output=True, text=True).stdout.split()
        self.assertIn("pub_app.py", files)
        self.assertIn("data/own.json", files)
        for gone in ("data/big.json", ".env", "__pycache__/a.pyc", ".classifier-review/r.json"):
            self.assertNotIn(gone, files)


class PublishRespectsReviewItemsAndGitignore(unittest.TestCase):
    def test_review_glob_and_gitignored_files_stay_out(self):
        import subprocess
        from classifier import publish
        tmp = Path(tempfile.mkdtemp())
        d = make_soft(tmp, "G", {"g.py": "x=1\n", "samples/a.wav": b"RIFF", "keep/b.wav": b"RIFF",
                                 "secret/notes.txt": "private", ".gitignore": "secret/\n"})
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        sw = model.new_software("G", str(d))
        sw["scan"] = {"findings": []}
        # a Claude review item keyed by a pattern, not a file
        sw["claude_review"] = {"items": [{"key": "asset:samples/*.wav", "verdict": "excluded"}]}
        sw["resolutions"] = {"asset:samples/*.wav": {"kind": "excluded"}}
        f = [x for x in model.findings(sw) if x["key"] == "asset:samples/*.wav"][0]
        self.assertEqual(f["paths"], ["samples/*.wav"])
        pl = publish.plan(sw)
        self.assertIn("g.py", pl["include"])
        self.assertIn("keep/b.wav", pl["include"])
        self.assertNotIn("samples/a.wav", pl["include"])
        self.assertNotIn("secret/notes.txt", pl["include"])     # the folder's .gitignore

    def test_plain_folder_gitignore_keeps_private_names_out(self):
        """Not a git repository: its .gitignore still applies, and the files it covers
        are not named one by one in the published .gitignore."""
        from classifier import publish
        tmp = Path(tempfile.mkdtemp())
        d = make_soft(tmp, "P", {"p.py": "x=1\n", ".gitignore": "data/\n",
                                 "data/settings.json": "{}", "data/cache/My_Private_Video_123.mp4": b"\0"})
        self.assertFalse((d / ".git").exists())
        sw = model.new_software("P", str(d))
        sw["scan"] = {"findings": [{"key": "file:data/settings.json", "kind": "claude", "title": "s",
                                    "detail": "", "paths": ["data/settings.json"], "auto": None}]}
        sw["resolutions"] = {"file:data/settings.json": {"kind": "excluded"}}
        pl = publish.plan(sw)
        self.assertEqual(pl["include"], [".gitignore", "p.py"])
        self.assertEqual(pl["excluded"], [])
        out = {rel for rel, _ in pl["always_out"]}
        self.assertIn("data/settings.json", out)
        self.assertIn("data/cache/My_Private_Video_123.mp4", out)
        target = Path(tempfile.mkdtemp())
        (target / ".gitignore").write_text("data/\n")
        publish._write_gitignore(target, pl)
        text = (target / ".gitignore").read_text()
        self.assertNotIn("settings.json", text)
        self.assertNotIn("Private_Video", text)
        self.assertFalse((d / ".git").exists())                  # the folder was not made a repository


class PublishSyncAndPublic(unittest.TestCase):
    def git(self, *a, cwd=None):
        import subprocess
        return subprocess.run(["git", *(["-C", str(cwd)] if cwd else []), "-c", "user.name=t",
                               "-c", "user.email=t@t", *a], check=True, capture_output=True,
                              text=True).stdout

    def test_sync_folder_and_history_check(self):
        from classifier import publish
        tmp = Path(tempfile.mkdtemp())
        bare = tmp / "remote.git"
        self.git("init", "--bare", "-q", "-b", "main", str(bare))
        folder = tmp / "Box"
        self.git("clone", "-q", str(bare), str(folder))
        (folder / "box.py").write_text("v1\n")
        (folder / "secret.wav").write_text("old")       # later excluded, still in history
        self.git("add", "-A", cwd=folder)
        self.git("commit", "-qm", "first", cwd=folder)
        self.git("push", "-q", "origin", "main", cwd=folder)
        self.git("rm", "-q", "secret.wav", cwd=folder)
        self.git("commit", "-qm", "drop", cwd=folder)
        self.git("push", "-q", "origin", "main", cwd=folder)
        (folder / "box.py").write_text("v2\n")          # a local edit
        (folder / "LICENSE").write_text("MIT, made with Claude, an AI\n")
        (folder / "secret.wav").write_text("again")    # present but excluded
        sw = model.new_software("Box", str(folder))
        sw["scan"] = {"findings": [{"key": "file:secret.wav", "kind": "claude", "title": "s",
                                    "detail": "", "paths": ["secret.wav"], "auto": None}]}
        sw["resolutions"] = {"file:secret.wav": {"kind": "excluded"}}
        dest = {"repo": "me/box", "path": "", "exists": True, "branches": ["main"],
                "visibility": "PRIVATE"}
        self.assertTrue(publish.local_clone_of(folder, dest))
        out = publish.publish(sw, {"ai_system": "Claude"}, dest, remote_url=str(bare),
                              progress=lambda m: None, sync_folder=True, make_public=True)
        # folder now at the published commit, files untouched
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=folder),
                         self.git("--git-dir", str(bare), "rev-parse", "main"))
        self.assertEqual((folder / "box.py").read_text(), "v2\n")
        self.assertEqual((folder / "secret.wav").read_text(), "again")
        self.assertIn("now at the published commit", out)
        self.assertIn("secret.wav", (folder / ".gitignore").read_text())
        # the history still holds secret.wav, so it is not made public
        self.assertIn("NOT made public", out)
        self.assertIn("secret.wav", out)
        # publishing again: nothing new, still fine
        out2 = publish.publish(sw, {"ai_system": "Claude"}, dest, remote_url=str(bare),
                               progress=lambda m: None, sync_folder=True)
        self.assertIn("Nothing new to commit", out2)

    def test_sync_updates_regenerated_files(self):
        """A generated file the folder has not touched (here THIRD-PARTY.md) is
        brought up to date by the sync, like git pull; a hand-edited one is not."""
        from classifier import publish
        tmp = Path(tempfile.mkdtemp())
        bare = tmp / "remote.git"
        self.git("init", "--bare", "-q", "-b", "main", str(bare))
        folder = tmp / "Gen"
        self.git("clone", "-q", str(bare), str(folder))
        (folder / "gen.py").write_text("x\n")
        (folder / "LICENSE").write_text("MIT, made with Claude, an AI\n")
        (folder / "THIRD-PARTY.md").write_text("old generated text\n")
        (folder / "notes.md").write_text("mine\n")
        self.git("add", "-A", cwd=folder)
        self.git("commit", "-qm", "first", cwd=folder)
        self.git("push", "-q", "origin", "main", cwd=folder)
        (folder / "notes.md").write_text("mine, edited\n")       # a local edit
        sw = model.new_software("Gen", str(folder))
        sw["scan"] = {"findings": [{"key": "lib:x", "kind": "package", "title": "Lib X",
                                    "detail": "", "paths": ["gen.py"], "auto": None}]}
        sw["resolutions"] = {"lib:x": {"kind": "opensource", "licence": "MIT", "note": "n"}}
        dest = {"repo": "me/gen", "path": "", "exists": True, "branches": ["main"],
                "visibility": "PRIVATE"}
        publish.publish(sw, {"ai_system": "Claude"}, dest, remote_url=str(bare),
                        progress=lambda m: None, sync_folder=True)
        third = (folder / "THIRD-PARTY.md").read_text()
        self.assertIn("Lib X", third)
        self.assertEqual((folder / "notes.md").read_text(), "mine, edited\n")
        self.assertEqual(self.git("status", "--porcelain", "--", "THIRD-PARTY.md",
                                  cwd=folder), "")


class Licence(unittest.TestCase):
    def test_build_and_check(self):
        d = Path(tempfile.mkdtemp())
        for spdx in licences.WRITABLE:
            (d / "LICENSE").write_text(licences.build(spdx, "Test Person", "Claude",
                                                      "Anthropic", "App"))
            c = licences.check(d)
            self.assertEqual(c["status"], "ok", (spdx, c))
        (d / "LICENSE").write_text(licences.MIT_TEXT.format(year=2026, holder="T"))
        self.assertEqual(licences.check(d)["status"], "no-ai")
        (d / "LICENSE").write_text("All rights reserved. Made with Claude, an AI.")
        self.assertEqual(licences.check(d)["status"], "unknown")
        (d / "LICENSE").unlink()
        self.assertEqual(licences.check(d)["status"], "missing")


class GitHubRules(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.reg = model.Registry(self.tmp / "reg.json")
        for name in ("Solo", "Draft", "SuiteA", "SuiteB", "Fine"):
            d = make_soft(self.tmp, name, {f"{name.lower()}.py": "x=1\n", "README.md": "r"})
            self.reg.add(str(d))
        self.snap = {
            "me/solo": {"visibility": "PUBLIC", "url": "", "archived": False,
                        "branches": {"main": ["solo.py", "README.md"]}},
            "me/draft": {"visibility": "PRIVATE", "url": "", "archived": False,
                         "branches": {"main": ["draft.py"]}},
            "me/suite": {"visibility": "PUBLIC", "url": "", "archived": False,
                         "branches": {"main": ["SuiteA/suitea.py", "SuiteB/suiteb.py"],
                                      "win": ["SuiteA/suitea.py"]}},
            "me/fine": {"visibility": "PUBLIC", "url": "", "archived": False,
                        "branches": {"main": ["fine.py"]}},
        }

    def st(self, **groups):
        return {n: {"group": groups.get(n, model.UNCLASSIFIED), "cleared": False,
                    "licence": "missing"} for n in self.reg.software}

    def test_rules(self):
        issues, placed = github.audit(self.reg, self.snap, self.st(
            Solo=model.FINISHED_PRIVATE, Draft=model.DEV_PRIVATE,
            SuiteA=model.SUITE_PRIVATE, SuiteB=model.DEV_PRIVATE, Fine=model.RELEASABLE))
        acts = {(i["action"], i["repo"], i["path"]) for i in issues if i["action"]}
        self.assertIn(("make_private", "me/solo", ""), acts)
        self.assertIn(("delete_repo", "me/draft", ""), acts)
        self.assertIn(("make_private", "me/suite", "SuiteA"), acts)
        self.assertIn(("remove_path", "me/suite", "SuiteB"), acts)
        self.assertNotIn("me/fine", {a[1] for a in acts})
        self.assertTrue(any(i["level"] == "warn" and i["repo"] == "me/fine" for i in issues))
        self.assertEqual(sorted(l["branches"] for l in placed["SuiteA"])[0], ["main", "win"])

    def test_unclassified_is_left_alone(self):
        issues, _ = github.audit(self.reg, self.snap, self.st())
        self.assertFalse([i for i in issues if i["action"]])


if __name__ == "__main__":
    unittest.main()
