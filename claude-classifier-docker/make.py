#!/usr/bin/env python3
"""Build and install claude-classifier-docker from ZIS's claude-docker.

claude-classifier-docker is claude-docker under its own name: the same locked-down
container (launch folder read-only, --write for one folder, network allow-list
through a proxy, a policy the session cannot change), but with

  - its own settings, policy, allow-list and hide-list in ~/.config/claude-classifier-docker
  - its own Claude login, sessions and held memory in ~/.local/share/claude-classifier-docker
  - Classifier's window class and icon
  - the SAME Docker images as claude-docker (claude-docker:latest and
    claude-docker-proxy:latest), so nothing has to be built twice.

It is generated, not hand-edited: re-run this after ZIS's claude-docker changes.

    python3 make.py [--zis ~/Claude/ZIS/claude-docker] [--no-install]
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "claude-classifier-docker"
HOME = Path.home()

# Tokens that must keep the original name: the shared images, and paths that
# exist inside those images.
PROTECT = ["claude-docker:latest", "claude-docker-proxy:latest", "/opt/claude-docker",
           "claude_docker_layout"]


FIT = '''
def _fit(widget, w, h):
    """Resize a window, but never beyond the screen it opens on (added by
    Classifier's make.py: a 1280x615 display could not show the whole window)."""
    try:
        from PyQt6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            w, h = min(w, g.width() - 16), min(h, g.height() - 48)
    except Exception:
        pass
    widget.resize(w, h)

'''


def share_login(data: Path):
    """Use claude-docker's sign-in instead of a second one.

    credentials.json becomes a link to claude-docker's file (both tools copy the
    newest token into it, through the link, so a token refreshed by either stays
    valid for both); claude.json (onboarding done, account id) is copied."""
    src = HOME / ".local" / "share" / "claude-docker"
    creds, mine = src / "credentials.json", data / "credentials.json"
    if not creds.is_file():
        print("claude-docker has no sign-in yet; sign in once in the first Claude window.")
        return
    if mine.is_symlink() and mine.resolve() == creds.resolve():
        print("Already using claude-docker's sign-in.")
    else:
        if mine.exists() or mine.is_symlink():
            mine.rename(mine.with_name("credentials.json.own"))
        mine.symlink_to(creds)
        print(f"Using claude-docker's sign-in ({creds}).")
    cj = src / "claude.json"
    if cj.is_file() and not (data / "claude.json").exists() or (
            cj.is_file() and b"hasCompletedOnboarding" not in (data / "claude.json").read_bytes()):
        shutil.copy2(cj, data / "claude.json")


def convert(src: str) -> str:
    keep = {}
    for i, tok in enumerate(PROTECT):
        mark = f"\x00KEEP{i}\x00"
        keep[mark] = tok
        src = src.replace(tok, mark)
    src = src.replace("zis-claude-docker", "classifier")       # window class / icon
    src = src.replace("claude-docker", NAME)
    src = src.replace("CLAUDE_DOCKER_WORKSPACE", "CLAUDE_CLASSIFIER_DOCKER_WORKSPACE")
    for mark, tok in keep.items():
        src = src.replace(mark, tok)
    # Fit every Qt window to the screen (ZIS's sizes assume a tall display).
    src = re.sub(r"\b(self|d)\.resize\((\d+), (\d+)\)", r"_fit(\1, \2, \3)", src)
    src = src.replace("\ndef write_json(", FIT + "\ndef write_json(", 1)
    # Start the terminal once.  The window can be "shown" twice in quick
    # succession (GNOME on Wayland does this); ZIS checks self.proc, which is only
    # set when start() runs 150 ms later, so both showings started an xterm: two
    # terminals in one window (one half-size on top), the second on the same
    # session and failing.
    old = ("            if self.proc is None and not shots:\n"
           "                QTimer.singleShot(150, self.start)\n")
    new = ("            if self.proc is None and not shots and not getattr(self, '_starting', False):\n"
           "                self._starting = True\n"
           "                QTimer.singleShot(150, self.start)\n")
    if old not in src:
        raise SystemExit("make.py: claude-docker's showEvent changed; update the start-once fix")
    src = src.replace(old, new)
    # Say what this copy is, under the original docstring's first line.
    src = re.sub(r'^("""claude-classifier-docker[^\n]*\n)',
                 r'\1\n(Generated from ZIS claude-docker by Classifier/claude-classifier-docker/'
                 r'make.py: its own config and policy; the sign-in and images of claude-docker.)\n',
                 src, count=1, flags=re.M)
    return src


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zis", default=str(HOME / "Claude" / "ZIS" / "claude-docker"),
                    help="ZIS's claude-docker folder")
    ap.add_argument("--own-login", action="store_true",
                    help="keep a separate sign-in instead of sharing claude-docker's")
    ap.add_argument("--no-install", action="store_true",
                    help="only write the generated script next to this file")
    a = ap.parse_args()
    zis = Path(a.zis).expanduser()
    if not (zis / "claude-docker").is_file():
        sys.exit(f"no claude-docker in {zis}")

    out = HERE / NAME
    text = convert((zis / "claude-docker").read_text(encoding="utf-8"))
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    left = [ln for ln in text.splitlines() if "claude-docker" in ln
            and not any(p in ln for p in PROTECT)]
    print(f"Wrote {out}")
    if left:
        print("Lines still naming claude-docker (check them):")
        for ln in left:
            print("  " + ln.strip())
    if a.no_install:
        return

    bin_dir = HOME / ".local" / "bin"
    conf = HOME / ".config" / NAME
    data = HOME / ".local" / "share" / NAME
    for d in (bin_dir, conf / "policy", conf / "proxy", data):
        d.mkdir(parents=True, exist_ok=True)
    data.chmod(0o700)
    shutil.copy2(out, bin_dir / NAME)
    for f in ("Dockerfile", ".dockerignore", "settings.json", "proxy/Dockerfile",
              "proxy/squid.conf"):
        shutil.copy2(zis / f, conf / f)
    pol = conf / "policy" / "managed-settings.json"
    if not pol.exists():
        shutil.copy2(zis / "policy" / "managed-settings.json", pol)
        print(f"Installed the policy: {pol}")
    # Classifier sessions need no hosts beyond Anthropic's: start the allow-list empty.
    allow = conf / "allowed-hosts.txt"
    if not allow.exists():
        allow.write_text("# Hosts a claude-classifier-docker session may reach besides "
                         "Anthropic's.\n# Classifier's reviews need none.\n", encoding="utf-8")
    hide = conf / "hide.txt"
    if not hide.exists():
        shutil.copy2(zis / "hide.template.txt", hide)
    ws = conf / "default-workspace"
    if not ws.exists() or not ws.read_text().strip():
        ws.write_text(str(HOME / "Claude") + "\n", encoding="utf-8")
    print(f"Installed {bin_dir / NAME} and {conf}/")
    if not a.own_login:
        share_login(data)
    print("It uses the claude-docker images.")


if __name__ == "__main__":
    main()
