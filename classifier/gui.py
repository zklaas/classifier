"""Classifier's window."""

from __future__ import annotations

import html
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QCompleter, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QTableWidget,
    QTableWidgetItem, QTabWidget, QTextBrowser, QToolBar, QVBoxLayout, QWidget)

from . import aiuse, catalogue, github, helptext, publish, licences, model, platforms, review, status

APP_DIR = Path(__file__).resolve().parent.parent
GROUP_COLOURS = {
    model.RELEASABLE: "#2e7d32", model.RELEASABLE_SUITE: "#00897b",
    model.FINISHED_PRIVATE: "#6a1b9a", model.SUITE_PRIVATE: "#3949ab",
    model.DEV_PRIVATE: "#c62828", model.UNCLASSIFIED: "#757575",
}
LIC_COLOURS = {"ok": "#2e7d32", "missing": "#757575", "unknown": "#ef6c00",
               "not-open": "#c62828", "no-ai": "#ef6c00"}


def fit(widget, w, h):
    """Resize to w x h, but never larger than the screen's free area, and keep
    the window on the screen."""
    scr = QApplication.primaryScreen()
    if scr is None:
        widget.resize(w, h)
        return
    g = scr.availableGeometry()
    w, h = min(w, g.width() - 16), min(h, g.height() - 40)
    widget.resize(w, h)
    widget.move(g.x() + (g.width() - w) // 2, g.y() + max(0, (g.height() - h - 30) // 2))


def scrolled(inner: QWidget, horizontal=True) -> QScrollArea:
    """Put a panel in a scroll area so it shows scrollbars instead of running
    off a small screen."""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    if not horizontal:
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(inner)
    return sa


class Worker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except Exception as e:          # shown to the user, never swallowed
            self.failed.emit(f"{type(e).__name__}: {e}")


def html_window(parent, title, body, size=(760, 640)):
    d = QDialog(parent)
    d.setWindowTitle(title)
    fit(d, *size)
    lay = QVBoxLayout(d)
    tb = QTextBrowser()
    tb.setOpenExternalLinks(True)
    tb.setHtml(body)
    lay.addWidget(tb)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    bb.rejected.connect(d.reject)
    lay.addWidget(bb)
    return d


class SettingsDialog(QDialog):
    def __init__(self, reg: model.Registry, parent=None, first=False):
        super().__init__(parent)
        self.reg = reg
        self.setWindowTitle("Classifier settings")
        fit(self, 560, 440)
        s = reg.settings
        lay = QVBoxLayout(self)
        if first:
            msg = QLabel("Welcome. Classifier writes licences in your name as licence creator, "
                         "recognises your work by your names, and looks for your software in "
                         "the folders you list (each folder inside them with code counts as "
                         "one software). Please fill these in.")
            msg.setWordWrap(True)
            lay.addWidget(msg)
        form = QFormLayout()
        self.licensor = QLineEdit(s.get("licensor", ""))
        self.licensor.setPlaceholderText("Your name as it should appear in licences")
        self.aliases = QLineEdit(", ".join(s.get("aliases", [])))
        self.aliases.setPlaceholderText("GitHub handle, e-mail, other spellings (comma-separated)")
        self.ai = QLineEdit(s.get("ai_system", "Claude"))
        self.maker = QLineEdit(s.get("ai_maker", "Anthropic"))
        self.roots = QPlainTextEdit("\n".join(s.get("roots", [])))
        self.roots.setFixedHeight(64)
        self.contact = QLineEdit(s.get("contact", ""))
        self.contact.setPlaceholderText("e-mail or web page shown on public pages for problem reports")
        self.owner = QLineEdit(s.get("github_owner", ""))
        self.owner.setPlaceholderText("blank = the account gh is logged in as")
        self.default_lic = QComboBox()
        self.default_lic.addItems(list(licences.WRITABLE))
        self.default_lic.setCurrentText(s.get("default_licence", "MIT"))
        self.auto_private = QCheckBox("Make public repositories private as soon as I am told "
                                      "(no second question)")
        self.auto_private.setChecked(bool(s.get("auto_private", True)))
        self.cat_auto = QCheckBox("Keep the public catalogue up to date automatically (after "
                                  "Commit and push and Check GitHub…)")
        self.cat_auto.setChecked(bool(s.get("catalogue_auto", True)))
        self.cat_auto.setToolTip("The catalogue repository is "
                                 + (s.get("catalogue_repo") or "not set yet: publish the "
                                    "catalogue once from Catalogue…"))
        form.addRow("Licence creator:", self.licensor)
        form.addRow("Your other names:", self.aliases)
        form.addRow("AI system:", self.ai)
        form.addRow("Made by:", self.maker)
        rbox = QWidget()
        rl = QVBoxLayout(rbox)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.roots)
        browse = QPushButton("Add a folder…")
        browse.clicked.connect(self._browse_root)
        rl.addWidget(browse, 0, Qt.AlignmentFlag.AlignLeft)
        self.roots.setPlaceholderText("e.g. /home/you/projects")
        form.addRow("Folders holding\nyour software:", rbox)
        form.addRow("Contact for problems:", self.contact)
        form.addRow("GitHub account:", self.owner)
        form.addRow("Default licence:", self.default_lic)
        form.addRow("", self.auto_private)
        form.addRow("", self.cat_auto)
        ai_list = QPushButton("Edit the list of AI systems…")
        ai_list.setToolTip("Add AI systems the AI-connection check should recognise")
        ai_list.clicked.connect(self.edit_ai_list)
        form.addRow("AI detection:", ai_list)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def edit_ai_list(self):
        import json
        from . import aiuse
        p = model.CONFIG_DIR / aiuse.USER_TABLE.name
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(aiuse.USER_TABLE_EXAMPLE, indent=2) + "\n", encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
        QMessageBox.information(self, "AI systems",
                                f"Opened {p}.\n\nReplace the example entries with your own, "
                                "save, restart Classifier and rescan.")

    def _browse_root(self):
        d = QFileDialog.getExistingDirectory(self, "A folder that holds your software folders",
                                             str(Path.home()))
        if d:
            text = self.roots.toPlainText().rstrip()
            self.roots.setPlainText((text + "\n" if text else "") + d)

    def save(self):
        s = self.reg.settings
        s["licensor"] = self.licensor.text().strip()
        s["aliases"] = [a.strip() for a in self.aliases.text().split(",") if a.strip()]
        s["ai_system"] = self.ai.text().strip() or "Claude"
        s["ai_maker"] = self.maker.text().strip() or "Anthropic"
        s["roots"] = [r.strip() for r in self.roots.toPlainText().splitlines() if r.strip()]
        s["github_owner"] = self.owner.text().strip()
        s["contact"] = self.contact.text().strip()
        s["default_licence"] = self.default_lic.currentText()
        s["auto_private"] = self.auto_private.isChecked()
        s["catalogue_auto"] = self.cat_auto.isChecked()
        self.reg.save()
        self.accept()


class GithubDialog(QDialog):
    """The GitHub check's report, with the changes it calls for."""

    def __init__(self, win, issues, placements):
        super().__init__(win)
        self.win = win
        self.issues = issues
        self.setWindowTitle("GitHub check")
        fit(self, 900, 560)
        lay = QVBoxLayout(self)
        acts = [i for i in issues if i["action"]]
        head = QLabel(
            "<b>GitHub matches every Development Group.</b>" if not issues else
            f"<b>{len(acts)} change(s) needed</b>, {len(issues) - len(acts)} warning(s)/note(s). "
            "Ticked changes are made when you press <i>Make the ticked changes</i>; "
            "removals ask you to type the repository name first.")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        icons = {"action": "⚠ ", "warn": "• ", "note": "· "}
        for i in issues:
            it = QListWidgetItem(icons[i["level"]] + i["message"])
            if i["action"]:
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked)
                it.setForeground(QColor("#c62828" if i["action"] != "make_private" else "#ef6c00"))
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        where = QTextBrowser()
        where.setMaximumHeight(150)
        rows = []
        for n, locs in sorted(placements.items()):
            for l in locs:
                rows.append(f"<b>{html.escape(n)}</b>: {l['repo']}"
                            f"{'/' + l['path'] if l['path'] else ''} ({l['visibility'].lower()}; "
                            f"{', '.join(l['branches'])}; found by {html.escape(l['how'])})")
        where.setHtml("<b>Where your software is on GitHub</b><br>" +
                      ("<br>".join(rows) or "Nowhere."))
        lay.addWidget(where)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumHeight(110)
        self.out.hide()
        lay.addWidget(self.out)
        bb = QDialogButtonBox()
        self.go = bb.addButton("Make the ticked changes", QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.setEnabled(bool(acts))
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        self.go.clicked.connect(self.apply)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if acts and win.reg.settings.get("auto_private") and all(
                i["action"] == "make_private" for i in acts):
            QTimer.singleShot(200, self.apply)

    def apply(self):
        todo = []
        for k in range(self.list.count()):
            it = self.list.item(k)
            i = it.data(Qt.ItemDataRole.UserRole)
            if i["action"] and it.checkState() == Qt.CheckState.Checked:
                todo.append((it, i))
        todo.sort(key=lambda t: t[1]["action"] == "make_private", reverse=True)
        self.out.show()
        for it, i in todo:
            if i["action"] in ("delete_repo", "remove_path"):
                what = ("DELETE the repository " + i["repo"] if i["action"] == "delete_repo"
                        else f"remove {i['path']}/ from {i['repo']} on {', '.join(i['branches'])}")
                typed, ok = QInputDialog.getText(
                    self, "Confirm removal",
                    f"{i['message']}\n\nThis will {what}.\nA backup clone is made first.\n\n"
                    f"Type {i['repo']} to go ahead:")
                if not ok or typed.strip() != i["repo"]:
                    self.out.appendPlainText(f"Skipped: {i['software']} ({i['repo']}).")
                    continue
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                msg = github.apply(i)
                it.setCheckState(Qt.CheckState.Unchecked)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                it.setText("✔ " + msg)
                it.setForeground(QColor("#2e7d32"))
                self.out.appendPlainText(msg)
            except Exception as e:
                self.out.appendPlainText(f"FAILED ({i['repo']}): {e}")
            finally:
                QApplication.restoreOverrideCursor()
            QApplication.processEvents()
        self.go.setEnabled(False)
        self.win.auto_catalogue()


class SigninDialog(QDialog):
    """Sign in to Claude in three steps, without the terminal (see signin.py)."""

    def __init__(self, win):
        from . import signin
        super().__init__(win)
        self.win = win
        self.session = signin.Session()
        self.link = ""
        self.busy = None
        self.setWindowTitle("Sign in to Claude")
        fit(self, 620, 430)
        lay = QVBoxLayout(self)
        intro = QLabel("Claude's reviews need you signed in to your Claude account.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        def step(n, text):
            l = QLabel(f"<b>{n}.</b> {text}")
            l.setWordWrap(True)
            lay.addWidget(l)
            return l

        self.s1 = step(1, "Getting the sign-in page ready…")
        self.status = QLabel()
        self.status.setStyleSheet("color:#555")
        lay.addWidget(self.status)
        self.s2 = step(2, "On the sign-in page, sign in to your Claude account if asked, "
                          "and approve (the page calls it <i>Claude Code</i>, which is what "
                          "runs the reviews).")
        row = QHBoxLayout()
        self.open_btn = QPushButton("Open the sign-in page")
        self.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.link)))
        self.copy_btn = QPushButton("Copy the link")
        self.copy_btn.setToolTip("If the page does not open, paste this link into your browser")
        self.copy_btn.clicked.connect(lambda: (QApplication.clipboard().setText(self.link),
                                               self.status.setText("Link copied.")))
        row.addWidget(self.open_btn)
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.s3 = step(3, "The page then shows a code. Copy it, paste it here, and press "
                          "<i>Finish signing in</i>.")
        row = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("Paste the code here")
        self.code.returnPressed.connect(self.finish)
        self.finish_btn = QPushButton("Finish signing in")
        self.finish_btn.clicked.connect(self.finish)
        row.addWidget(self.code, 1)
        row.addWidget(self.finish_btn)
        lay.addLayout(row)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.result, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._enable(link=False)
        self._work(lambda progress: (setattr(self.session, "log", progress),
                                     self.session.start())[1], self._got_link)

    def _enable(self, link):
        for w in (self.open_btn, self.copy_btn, self.code, self.finish_btn, self.s2, self.s3):
            w.setEnabled(link)

    def _work(self, fn, done):
        wk = Worker(fn, self.win)          # owned by the main window: survives closing
        self.win.workers.append(wk)
        wk.finished.connect(lambda: self.win.workers.remove(wk) if wk in self.win.workers else None)
        self.busy = wk
        wk.progress.connect(self.status.setText)
        wk.done.connect(done)
        wk.failed.connect(self._failed)
        wk.start()

    def _got_link(self, link):
        self.link = link
        self.s1.setText("<b>1.</b> The sign-in page is ready. ✔")
        self.status.setText("")
        self._enable(link=True)
        QDesktopServices.openUrl(QUrl(link))
        self.status.setText("The sign-in page should now be open in your browser. If not, "
                            "press Open the sign-in page.")
        self.code.setFocus()

    def finish(self):
        code = self.code.text().strip()
        if not code:
            self.status.setText("Paste the code from the sign-in page first.")
            return
        self._enable(link=False)
        self._work(lambda progress: (setattr(self.session, "log", progress),
                                     self.session.finish(code))[1], self._finished)

    def _finished(self, res):
        ok, text = res
        if ok:
            self.result.setText(f"<span style='color:#2e7d32;font-size:13pt'><b>Signed in. "
                                f"✔</b></span><br>{html.escape(text)}. You can close this "
                                "window and use Review with Claude….")
            self.status.setText("")
        else:
            self.result.setText(f"<span style='color:#c62828'><b>Not signed in.</b></span> "
                                f"{html.escape(text)}<br>Close this window and try again.")

    def _failed(self, msg):
        self.result.setText(f"<span style='color:#c62828'><b>Something went wrong.</b></span> "
                            f"{html.escape(msg)}")
        self.status.setText("")

    def done(self, r):
        # Closing: end Claude if it is still running (off the GUI thread).
        if self.session.proc and self.session.proc.poll() is None:
            import threading
            threading.Thread(target=self.session.stop, daemon=True).start()
        super().done(r)


class PublishDialog(QDialog):
    """Review what will be committed and pushed, then do it."""

    def __init__(self, win, sw, owner, snap, placements):
        super().__init__(win)
        self.win, self.sw, self.owner, self.snap = win, sw, owner, snap
        self.placements = placements
        self.st = win.statuses[sw["name"]]
        self.private_only = publish.must_be_private(self.st)
        self.plan = publish.plan(sw)
        self.dest = publish.destination(sw, placements, owner)
        self.setWindowTitle(f"Commit and push {sw['name']} to GitHub")
        fit(self, 900, 640)
        lay = QVBoxLayout(self)
        g = self.st["group"]
        rule = ("Private software: it may only go to a <b>private</b> repository."
                if self.private_only else
                "Releasable: it may go to a public or a private repository.")
        head = QLabel(f"<b>{html.escape(sw['name'])}</b> is <b>{html.escape(g)}</b>. {rule} "
                      "Everything Provenance excludes stays out. Your folder is not changed.")
        head.setWordWrap(True)
        lay.addWidget(head)

        form = QFormLayout()
        self.repo = QLineEdit(self.dest["repo"])
        self.repo.editingFinished.connect(self._dest_changed)
        self.path = QLineEdit(self.dest["path"])
        self.path.setPlaceholderText("blank = the whole repository is this software")
        self.path.editingFinished.connect(self._dest_changed)
        self.vis = QComboBox()
        self.vis.addItem("Private", "private")
        self.vis.addItem("Public", "public")
        self.where = QLabel()
        self.where.setWordWrap(True)
        form.addRow("Repository:", self.repo)
        form.addRow("Folder in it:", self.path)
        form.addRow("New repository is:", self.vis)
        form.addRow("", self.where)
        lay.addLayout(form)

        tabs = QTabWidget()
        def listing(rows):
            t = QPlainTextEdit("\n".join(rows) or "(none)")
            t.setReadOnly(True)
            return t
        tabs.addTab(listing(self.plan["include"]),
                    f"Will be published ({len(self.plan['include'])})")
        tabs.addTab(listing(f"{r}    ← {why}" for r, why in self.plan["excluded"]),
                    f"Excluded by Provenance ({len(self.plan['excluded'])})")
        tabs.addTab(listing(f"{r}    ← {why}" for r, why in self.plan["always_out"]),
                    f"Always left out ({len(self.plan['always_out'])})")
        self.remote_tab = QWidget()
        rl = QVBoxLayout(self.remote_tab)
        self.remote_list = QPlainTextEdit()
        self.remote_list.setReadOnly(True)
        self.remove_others = QCheckBox("Remove these from GitHub too (otherwise they are kept)")
        rl.addWidget(self.remote_list)
        rl.addWidget(self.remove_others)
        tabs.addTab(self.remote_tab, "On GitHub, not in your folder")
        self.tabs = tabs
        lay.addWidget(tabs, 1)

        self.branch_box = QVBoxLayout()
        lay.addLayout(self.branch_box)
        self.sync_cb = QCheckBox("Bring the new commit into your folder's git copy too (your "
                                 "files stay exactly as they are)")
        self.sync_cb.setChecked(True)
        lay.addWidget(self.sync_cb)
        self.public_cb = QCheckBox("Make the repository public")
        self.public_cb.setToolTip("Its whole history becomes public, so Classifier first "
                                  "checks that no past commit holds an excluded or "
                                  "secret-looking file")
        lay.addWidget(self.public_cb)
        self.branch_checks = []
        msg_row = QHBoxLayout()
        msg_row.addWidget(QLabel("Commit message:"))
        self.msg = QLineEdit(f"Publish {sw['name']}")
        msg_row.addWidget(self.msg, 1)
        lay.addLayout(msg_row)
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        lay.addWidget(self.problems)
        self.out = QLabel()
        self.out.setWordWrap(True)
        self.out.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.out)
        bb = QDialogButtonBox()
        self.go = bb.addButton("Commit and push", QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.clicked.connect(self.do_publish)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._dest_changed(first=True)

    def _dest_changed(self, first=False):
        repo, path = self.repo.text().strip(), self.path.text().strip().strip("/")
        info = self.snap.get(repo)
        if not first or self.dest["repo"] != repo:
            self.dest = {"repo": repo, "path": path, "exists": info is not None,
                         "visibility": info["visibility"] if info else "",
                         "branches": list(info["branches"]) if info else [],
                         "how": "chosen by you"}
        else:
            self.dest["path"] = path
        exists = self.dest["exists"]
        self.vis.setEnabled(not exists and not self.private_only)
        if self.private_only:
            self.vis.setCurrentIndex(0)
        if exists:
            self.where.setText(f"{repo} exists and is <b>{self.dest['visibility'].lower()}</b> "
                               f"(found as {html.escape(self.dest['how'])}); branches: "
                               f"{', '.join(self.dest['branches']) or 'none yet'}.")
        else:
            self.where.setText(f"{repo} does not exist yet: it will be created "
                               + ("<b>private</b> (Private software)." if self.private_only
                                  else "as chosen above."))
        rem = publish.remote_only(self.dest, self.snap, self.plan) if exists else []
        self.remote_list.setPlainText("\n".join(rem) or "(none)")
        self.remove_others.setEnabled(bool(rem))
        self.tabs.setTabText(3, f"On GitHub, not in your folder ({len(rem)})")
        while self.branch_box.count():
            it = self.branch_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.branch_checks = []
        default = "main" if "main" in self.dest["branches"] else (
            self.dest["branches"][0] if self.dest["branches"] else "main")
        for b, plat in publish.platform_branches(self.sw, self.dest["branches"], default):
            cb = QCheckBox(f"Also bring branch {b} up to date ({plat} is Supported): merge the "
                           "new commit into it; left alone if the merge conflicts")
            cb.setChecked(True)
            self.branch_box.addWidget(cb)
            self.branch_checks.append((b, cb))
        local = exists and publish.local_clone_of(Path(self.sw["path"]), self.dest)
        self.sync_cb.setVisible(bool(local))
        can_public = (exists and self.dest["visibility"] == "PRIVATE"
                      and not self.private_only)
        others = (publish.other_private_software(self.win.reg, self.win.statuses,
                                                 self.placements, repo, self.sw["name"])
                  if can_public else [])
        self.public_cb.setVisible(bool(can_public))
        self.public_cb.setEnabled(not others)
        if others:
            self.public_cb.setChecked(False)
            self.public_cb.setText("Make the repository public (not possible: it also holds "
                                   "Private software: " + ", ".join(others) + ")")
        else:
            self.public_cb.setText("Make the repository public (its history is checked "
                                   "first)")
        probs = publish.blockers(self.sw, self.st, self.dest)
        if self.private_only and not exists and "/" not in repo:
            probs.append("Name the repository as owner/name.")
        self.problems.setText("".join(f"<span style='color:#c62828'>✖ {html.escape(p)}</span><br>"
                                      for p in probs))
        self.go.setEnabled(not probs)

    def do_publish(self):
        vis = "private" if self.private_only else self.vis.currentData()
        merges = [b for b, cb in self.branch_checks if cb.isChecked()]
        new = "" if self.dest["exists"] else f"Create the {vis.upper()} repository {self.dest['repo']}, then "
        sync = self.sync_cb.isVisible() and self.sync_cb.isChecked()
        make_pub = self.public_cb.isVisible() and self.public_cb.isChecked()
        extra = ""
        if sync:
            extra += "\nYour folder's git copy will then be moved up to the new commit."
        if make_pub:
            extra += ("\nThen the repository is made PUBLIC: anyone will be able to see it "
                      "and its whole history.")
        text = (f"{new}commit and push {len(self.plan['include'])} files of {self.sw['name']} to "
                f"{self.dest['repo']}{'/' + self.dest['path'] if self.dest['path'] else ''}"
                + (f", and merge into {', '.join(merges)}" if merges else "") + "?\n\n"
                f"{len(self.plan['excluded'])} file(s) stay out by Provenance"
                + (", and files on GitHub that are not in your folder are REMOVED"
                   if self.remove_others.isChecked() else "") + "." + extra)
        if QMessageBox.question(self, "Commit and push", text) != QMessageBox.StandardButton.Yes:
            return
        self.go.setEnabled(False)
        sw, settings, dest = self.sw, self.win.reg.settings, dict(self.dest)
        remove, msg, private_only = self.remove_others.isChecked(), self.msg.text().strip(), \
            self.private_only

        def work(progress):
            full = msg + "\n\n" + "\n".join(publish.default_message(sw, self.plan, settings)
                                             .splitlines()[2:])
            return publish.publish(sw, settings, dest, visibility=vis,
                                   private_only=private_only, remove_remote_only=remove,
                                   merge_branches=merges, message=full, progress=progress,
                                   sync_folder=sync, make_public=make_pub)
        wk = Worker(work, self.win)
        self.win.workers.append(wk)
        wk.progress.connect(lambda m: self.out.setText(html.escape(m)))
        def done(r):
            if sync:      # the folder changed: rescan so it stays Cleared
                status.scan(sw, self.win.reg.owner_names(), self.win.reg.settings)
                self.win.reg.save()
                self.win.refresh_all()
            self.out.setText("<span style='color:#2e7d32'><b>Done.</b></span> "
                             + html.escape(r).replace("\n", "<br>"))
            self.win.auto_catalogue()
        wk.done.connect(done)
        wk.failed.connect(lambda e: (self.out.setText(
            "<span style='color:#c62828'><b>Not published.</b></span> " + html.escape(e)),
            self.go.setEnabled(True)))
        wk.start()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.reg = model.Registry()
        self.statuses: dict[str, dict] = {}
        self.current: str | None = None
        self.workers = []
        self.placements = {}
        self.snap = {}
        self._loading = False
        self.setWindowTitle("Classifier")
        fit(self, 1280, 800)
        icon = APP_DIR / "icons" / "classifier.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self._toolbar()
        self.tabs = QTabWidget()
        guide = QTextBrowser()
        guide.setHtml(helptext.GUIDE_HTML)
        self.tabs.addTab(guide, "Guide")
        self.tabs.addTab(self._software_tab(), "Software")
        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("Ready")
        if not self.reg.software:
            self.reg.discover()
            self.reg.save()
        self.tabs.setCurrentIndex(1)
        self.refresh_all(fresh=False)
        QTimer.singleShot(100, self._first_run)
        QTimer.singleShot(300, self._fingerprints)

    # ---------------------------------------------------------------- layout
    def _toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        for text, fn, tip in [
            ("Find software", self.discover, "Register new software folders in your software folders"),
            ("Add folder…", self.add_folder, "Register one folder by hand"),
            ("Scan all", self.scan_all, "Run the provenance scan on every software"),
            ("Check GitHub…", self.check_github, "Compare GitHub with the Development Groups"),
            ("Catalogue…", self.show_catalogue, "The public page listing releasable software "
                                               "and how to install it"),
            ("Settings…", self.settings, "Your name, folders, GitHub account"),
        ]:
            a = QAction(text, self)
            a.setToolTip(tip)
            a.triggered.connect(fn)
            tb.addAction(a)

    def _software_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        strip = QWidget()
        self.summary = QHBoxLayout(strip)
        self.summary.setContentsMargins(0, 0, 0, 0)
        self.summary_labels = {}
        for g in model.GROUPS + [model.UNCLASSIFIED]:
            lab = QLabel()
            lab.setToolTip(model.GROUP_HELP[g])
            lab.setStyleSheet(f"QLabel {{ background:{GROUP_COLOURS[g]}; color:white; "
                              "border-radius:6px; padding:4px 8px; }")
            self.summary.addWidget(lab)
            self.summary_labels[g] = lab
        self.summary.addStretch(1)
        chips = scrolled(strip)
        chips.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chips.setFixedHeight(strip.sizeHint().height() + 14)
        lay.addWidget(chips)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Software", "Development Group", "Cleared",
                                              "Stage", "Suite", "Licence", "AI", "Platforms"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._select)
        split.addWidget(self.table)

        self.detail = QTabWidget()
        self.detail.addTab(scrolled(self._class_tab()), "Classification")
        self.detail.addTab(scrolled(self._prov_tab()), "Provenance")
        self.detail.addTab(scrolled(self._lic_tab()), "Licence")
        self.detail.addTab(scrolled(self._plat_tab()), "Platforms")
        self.detail.addTab(scrolled(self._gh_tab()), "GitHub")
        split.addWidget(self.detail)
        split.setChildrenCollapsible(False)
        self.table.setMinimumWidth(220)
        self.detail.setMinimumWidth(320)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        QTimer.singleShot(0, lambda: split.setSizes([split.width() * 2 // 5,
                                                     split.width() * 3 // 5]))
        lay.addWidget(split, 1)
        return w

    def _class_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.c_title = QLabel()
        self.c_title.setFont(QFont(self.font().family(), 15, QFont.Weight.Bold))
        self.c_path = QLabel()
        self.c_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.c_title)
        lay.addWidget(self.c_path)
        form = QFormLayout()
        self.c_stage = QComboBox()
        for k, v in model.STAGES.items():
            self.c_stage.addItem(v, k)
        self.c_stage.currentIndexChanged.connect(self._edit_class)
        self.c_suite = QLineEdit()
        self.c_suite.setPlaceholderText("blank = stands on its own")
        self.c_suite.editingFinished.connect(self._edit_class)
        self.c_override = QComboBox()
        self.c_override.addItem("Automatic (from stage, suite and clearance)", "")
        for g in model.GROUPS:
            self.c_override.addItem(g, g)
        self.c_override.currentIndexChanged.connect(self._edit_class)
        form.addRow("Stage:", self.c_stage)
        form.addRow("Modular suite:", self.c_suite)
        form.addRow("Development Group:", self.c_override)
        self.c_summary = QLineEdit()
        self.c_summary.setToolTip("One or two sentences for the public catalogue; blank = "
                                  "the first paragraph of its README")
        self.c_summary.editingFinished.connect(self._edit_public)
        form.addRow("Public description:", self.c_summary)
        lay.addLayout(form)
        box = QFrame()
        box.setFrameShape(QFrame.Shape.StyledPanel)
        bl = QVBoxLayout(box)
        self.c_group = QLabel()
        self.c_group.setWordWrap(True)
        self.c_clear = QLabel()
        self.c_clear.setWordWrap(True)
        bl.addWidget(self.c_group)
        bl.addWidget(self.c_clear)
        lay.addWidget(box)
        aibox = QFrame()
        aibox.setFrameShape(QFrame.Shape.StyledPanel)
        al = QVBoxLayout(aibox)
        ah = QHBoxLayout()
        t = QLabel("<b>Artificial intelligence</b>")
        ah.addWidget(t)
        ah.addStretch(1)
        b = QPushButton("How AI detection works…")
        b.clicked.connect(lambda: html_window(self, "How AI detection works",
                                              helptext.AI_HTML).exec())
        ah.addWidget(b)
        al.addLayout(ah)
        how = QLabel("The check for AI connections is part of <b>Scan</b> on the "
                     "<b>Provenance</b> tab: every scan also looks for them, and its "
                     "finding appears below. Rescan there after changing the code.")
        how.setWordWrap(True)
        al.addWidget(how)
        aform = QFormLayout()
        self.c_ai = QComboBox()
        for k, v in aiuse.LEVELS.items():
            self.c_ai.addItem(v, k)
        self.c_ai.currentIndexChanged.connect(self._edit_ai)
        self.c_ai_what = QLineEdit()
        self.c_ai_what.setPlaceholderText("e.g. reviews provenance with Claude")
        self.c_ai_what.editingFinished.connect(self._edit_ai)
        aform.addRow("Uses AI:", self.c_ai)
        aform.addRow("What the AI part does:", self.c_ai_what)
        al.addLayout(aform)
        self.c_ai_info = QLabel()
        self.c_ai_info.setWordWrap(True)
        al.addWidget(self.c_ai_info)
        lay.addWidget(aibox)
        lay.addWidget(QLabel("Notes:"))
        self.c_notes = QPlainTextEdit()
        self.c_notes.setMinimumHeight(60)
        self.c_notes.textChanged.connect(self._edit_notes)
        lay.addWidget(self.c_notes, 1)
        row = QHBoxLayout()
        b = QPushButton("Open folder")
        b.clicked.connect(lambda: self.cur() and QDesktopServices.openUrl(
            QUrl.fromLocalFile(self.cur()["path"])))
        row.addWidget(b)
        b = QPushButton("Stop tracking")
        b.setToolTip("Remove from Classifier and do not find it again")
        b.clicked.connect(self.stop_tracking)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        return w

    def _prov_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        grid = QGridLayout()
        for k, (text, fn) in enumerate([
                ("Scan", self.scan_one), ("How the scan works…", self.scan_help),
                ("Review with Claude…", self.claude_review),
                ("Import Claude's review", self.import_review),
                ("Accept Claude's verdicts", self.accept_review)]):
            b = QPushButton(text)
            b.clicked.connect(fn)
            grid.addWidget(b, k // 3, k % 3)
            if text.startswith("Accept"):
                self.p_accept = b
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)
        srow = QHBoxLayout()
        self.p_signin = QLabel()
        srow.addWidget(self.p_signin)
        b = QPushButton("Sign in to Claude…")
        b.setToolTip("Open the Claude window on /login; the sign-in is saved when you close it")
        b.clicked.connect(self.claude_signin)
        srow.addWidget(b)
        srow.addStretch(1)
        lay.addLayout(srow)
        self.p_head = QLabel()
        self.p_head.setWordWrap(True)
        lay.addWidget(self.p_head)
        self.p_table = QTableWidget(0, 5)
        self.p_table.setHorizontalHeaderLabels(["Item", "Origin", "Licence", "Claude says",
                                                "Note"])
        self.p_table.verticalHeader().hide()
        self.p_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        ph = self.p_table.horizontalHeader()
        ph.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        ph.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        ph.resizeSection(1, 190)
        ph.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        ph.resizeSection(2, 120)
        ph.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        ph.resizeSection(3, 110)
        ph.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        ph.resizeSection(4, 110)
        ph.setMinimumSectionSize(60)
        self.p_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.p_table.setWordWrap(False)
        self.p_table.itemSelectionChanged.connect(self._prov_select)
        self.p_table.itemChanged.connect(self._prov_note_edit)
        self.p_table.setMinimumHeight(170)
        lay.addWidget(self.p_table, 3)
        self.p_detail = QTextBrowser()
        self.p_detail.setMaximumHeight(140)
        self.p_detail.setMinimumHeight(70)
        lay.addWidget(self.p_detail, 1)
        row = QHBoxLayout()
        self.p_attest = QCheckBox()
        self.p_attest.toggled.connect(self._attest)
        self.p_attest_text = QLabel()
        self.p_attest_text.setWordWrap(True)
        self.p_attest_text.mousePressEvent = lambda e: self.p_attest.toggle()
        row.addWidget(self.p_attest, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.p_attest_text, 1)
        lay.addLayout(row)
        return w

    def _lic_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.l_status = QLabel()
        self.l_status.setWordWrap(True)
        self.l_status.setFont(QFont(self.font().family(), 12))
        lay.addWidget(self.l_status)
        self.l_rec = QLabel()
        self.l_rec.setWordWrap(True)
        lay.addWidget(self.l_rec)
        row = QHBoxLayout()
        row.addWidget(QLabel("Licence to write:"))
        self.l_choice = QComboBox()
        self.l_choice.addItems(list(licences.WRITABLE))
        row.addWidget(self.l_choice)
        b = QPushButton("Preview")
        b.clicked.connect(self.preview_licence)
        row.addWidget(b)
        self.l_write = QPushButton("Write LICENSE")
        self.l_write.clicked.connect(self.write_licence)
        row.addWidget(self.l_write)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("Current licence file:"))
        self.l_text = QPlainTextEdit()
        self.l_text.setMinimumHeight(120)
        self.l_text.setReadOnly(True)
        self.l_text.setFont(QFont("monospace", 9))
        lay.addWidget(self.l_text, 1)
        return w

    def _plat_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        intro = QLabel("Set a track for each operating system. Evidence and blockers come "
                       "from the last scan (and from branch names after a GitHub check).")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        b = QPushButton("How platform review works…")
        b.clicked.connect(lambda: html_window(self, "How platform review works",
                                              helptext.PLATFORM_HTML).exec())
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.pl_table = QTableWidget(len(platforms.PLATFORMS), 4)
        self.pl_table.setHorizontalHeaderLabels(["Platform", "Track", "Evidence", "Blockers"])
        self.pl_table.verticalHeader().hide()
        self.pl_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pl_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.pl_table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.pl_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.pl_table.setWordWrap(False)
        self.pl_combos = {}
        for r, p in enumerate(platforms.PLATFORMS):
            self.pl_table.setItem(r, 0, QTableWidgetItem(p))
            cb = QComboBox()
            for k, v in platforms.TRACKS.items():
                cb.addItem(v, k)
            cb.currentIndexChanged.connect(lambda _i, p=p, cb=cb: self._set_track(p, cb.currentData()))
            self.pl_table.setCellWidget(r, 1, cb)
            self.pl_combos[p] = cb
        self.pl_table.verticalHeader().setDefaultSectionSize(28)
        self.pl_table.setMinimumHeight(6 * 28 + 34)
        self.pl_table.itemSelectionChanged.connect(self._plat_select)
        lay.addWidget(self.pl_table, 2)
        self.pl_detail = QTextBrowser()
        self.pl_detail.setMinimumHeight(90)
        lay.addWidget(self.pl_detail, 3)
        lay.addWidget(QLabel("Install notes for the catalogue (added under each platform's "
                             "steps; optional):"))
        self.pl_notes = QPlainTextEdit()
        self.pl_notes.setMinimumHeight(50)
        self.pl_notes.setMaximumHeight(80)
        self.pl_notes.textChanged.connect(self._edit_public)
        lay.addWidget(self.pl_notes)
        return w

    def _fill_plat(self, sw):
        rev = sw.get("platform_review") or {}
        for r, p in enumerate(platforms.PLATFORMS):
            t = sw.get("platforms", {}).get(p, {}).get("track", "")
            cb = self.pl_combos[p]
            cb.blockSignals(True)
            cb.setCurrentIndex(max(0, cb.findData(t)))
            cb.blockSignals(False)
            ev = rev.get("evidence", {}).get(p, [])
            bl = rev.get("blockers", {}).get(p, [])
            e = QTableWidgetItem(f"{len(ev)}" + (f": {ev[0]}" if ev else ""))
            e.setForeground(QColor("#2e7d32" if ev else "#757575"))
            b = QTableWidgetItem(f"{len(bl)}" + (f": {bl[0]}" if bl else ""))
            b.setForeground(QColor("#c62828" if bl and t in ("supported", "untested")
                                   else "#ef6c00" if bl else "#757575"))
            self.pl_table.setItem(r, 2, e)
            self.pl_table.setItem(r, 3, b)
        warn = platforms.warnings(sw)
        self.pl_detail.setHtml(
            ("<b>Not reviewed yet: scan the software.</b>" if not rev else
             f"Reviewed {rev.get('at', '')}. Select a platform for the details.")
            + ("<br><br>" + "<br>".join("⚠ " + html.escape(x) for x in warn) if warn else ""))

    def _plat_select(self):
        sw = self.cur()
        rows = self.pl_table.selectionModel().selectedRows()
        if not sw or not rows:
            return
        p = platforms.PLATFORMS[rows[0].row()]
        rev = sw.get("platform_review") or {}
        ev = rev.get("evidence", {}).get(p, [])
        bl = rev.get("blockers", {}).get(p, [])
        self.pl_detail.setHtml(
            f"<h3>{p}</h3><b>Evidence of support</b><br>"
            + ("<br>".join("✔ " + html.escape(x) for x in ev) or "<i>none</i>")
            + "<br><br><b>Blockers</b><br>"
            + ("<br>".join("✖ " + html.escape(x) for x in bl) or "<i>none found</i>"))

    def _set_track(self, plat, track):
        sw = self.cur()
        if self._loading or not sw:
            return
        sw.setdefault("platforms", {}).setdefault(plat, {})["track"] = track
        self.reg.save()
        self.refresh_all()

    def _gh_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.g_text = QTextBrowser()
        lay.addWidget(self.g_text)
        row = QHBoxLayout()
        b = QPushButton("Check GitHub…")
        b.clicked.connect(self.check_github)
        row.addWidget(b)
        b = QPushButton("Commit and push to GitHub…")
        b.setToolTip("Publish this software's files to GitHub, leaving out everything "
                     "Provenance excludes")
        b.clicked.connect(self.start_publish)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        return w

    # ---------------------------------------------------------------- helpers
    def cur(self) -> dict | None:
        return self.reg.software.get(self.current) if self.current else None

    def _run(self, label, fn, done):
        self.statusBar().showMessage(label + " …")
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        wk = Worker(fn, self)
        self.workers.append(wk)

        def fin(result):
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(label + ": done", 6000)
            done(result)

        def fail(msg):
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(label + ": failed", 6000)
            QMessageBox.warning(self, label, msg)

        wk.done.connect(fin)
        wk.failed.connect(fail)
        wk.progress.connect(lambda m: self.statusBar().showMessage(f"{label}: {m}"))
        wk.finished.connect(lambda: self.workers.remove(wk) if wk in self.workers else None)
        wk.start()

    def _first_run(self):
        if not self.reg.settings.get("licensor"):
            if not self.reg.settings.get("aliases"):
                login = model.gh_login()
                if login:
                    self.reg.settings["aliases"] = [login]
            if SettingsDialog(self.reg, self, first=True).exec() and not self.reg.software:
                self.reg.discover()
                self.reg.save()
            self.refresh_all(fresh=False)

    def _fingerprints(self):
        sws = list(self.reg.software.values())

        def work(progress):
            for sw in sws:
                progress(sw["name"])
                status.refresh_fingerprint(sw)
        self._run("Checking for changes", work, lambda _: self.refresh_all(fresh=False))

    def refresh_all(self, fresh=False):
        for n, sw in self.reg.software.items():
            self.statuses[n] = status.of(sw, self.reg.settings, fresh=fresh)
        counts = {g: 0 for g in self.summary_labels}
        for st in self.statuses.values():
            counts[st["group"]] = counts.get(st["group"], 0) + 1
        for g, lab in self.summary_labels.items():
            lab.setText(f"{g}: {counts.get(g, 0)}")
        self._fill_table()
        self._show_current()

    def _fill_table(self):
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        names = sorted(self.reg.software, key=str.lower)
        self.table.setRowCount(len(names))
        sel_row = None
        for r, n in enumerate(names):
            sw, st = self.reg.software[n], self.statuses[n]
            vals = [n, st["group"], "Cleared" if st["cleared"] else "No",
                    model.STAGES.get(sw["stage"], ""), sw.get("suite", ""),
                    licences.STATUS_SHORT.get(st["licence"], ""),
                    aiuse.SHORT.get(st["ai"], "") + ("?" if st["ai"] and not
                                                     (sw.get("ai") or {}).get("level") else ""),
                    platforms.summary(sw)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, n)
                if c == 1:
                    it.setForeground(QColor(GROUP_COLOURS.get(st["group"], "#000")))
                    it.setToolTip(st["group_note"])
                if c == 2:
                    it.setForeground(QColor("#2e7d32" if st["cleared"] else "#c62828"))
                    it.setToolTip(st["reason"])
                if c == 5:
                    it.setForeground(QColor(LIC_COLOURS.get(st["licence"], "#000")))
                    it.setToolTip(st["licence_info"]["message"])
                if c == 6:
                    it.setToolTip(aiuse.describe(sw) + ("\n(suggested by the scan; confirm "
                                  "it on the Classification tab)" if st["ai"] and not
                                  (sw.get("ai") or {}).get("level") else ""))
                if c == 7 and platforms.warnings(sw):
                    it.setForeground(QColor("#ef6c00"))
                    it.setToolTip("\n".join(platforms.warnings(sw)))
                self.table.setItem(r, c, it)
            if n == self.current:
                sel_row = r
        self.table.blockSignals(False)
        self.table.setSortingEnabled(True)
        if sel_row is None and names:
            self.current = None
            self.table.selectRow(0)
            self._select()
        elif sel_row is not None:
            for r in range(self.table.rowCount()):
                if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == self.current:
                    self.table.blockSignals(True)
                    self.table.selectRow(r)
                    self.table.blockSignals(False)

    def _select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        self.current = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        self._show_current()

    def _show_current(self):
        sw = self.cur()
        if not sw:
            return
        st = self.statuses.get(sw["name"]) or status.of(sw, self.reg.settings)
        self._loading = True
        self.c_title.setText(sw["name"])
        self.c_path.setText(sw["path"])
        self.c_stage.setCurrentIndex(max(0, self.c_stage.findData(sw.get("stage", "unset"))))
        self.c_suite.setText(sw.get("suite", ""))
        suites = sorted({s.get("suite", "") for s in self.reg.software.values()} - {""})
        self.c_suite.setCompleter(QCompleter(suites, self))
        self.c_override.setCurrentIndex(max(0, self.c_override.findData(sw.get("group_override", ""))))
        self.c_notes.setPlainText(sw.get("notes", ""))
        self.c_summary.setText(sw.get("public_summary", ""))
        ai = sw.get("ai") or {}
        self.c_ai.setCurrentIndex(max(0, self.c_ai.findData(ai.get("level", ""))))
        self.c_ai_what.setText(ai.get("what", ""))
        rev = sw.get("ai_review")
        if not rev:
            self.c_ai_info.setText("<i>Not checked yet: press Scan on the Provenance tab to "
                                   "find out what it connects to.</i>")
        else:
            files = "; ".join(f"{html.escape(k)}: {html.escape(', '.join(v[:3]))}"
                              + (" …" if len(v) > 3 else "") for k, v in rev["files"].items())
            if rev.get("mcp_files"):
                files += ("; " if files else "") + "MCP: " + html.escape(", ".join(rev["mcp_files"][:3]))
            self.c_ai_info.setText(
                f"<b>Now:</b> {html.escape(aiuse.describe(sw) or '-')}<br>"
                f"<b>The scan of {html.escape((sw.get('scan') or {}).get('at', '?'))} "
                f"suggests:</b> {html.escape(aiuse.LEVELS[rev['suggested']])}. "
                f"{html.escape(rev['why'])}"
                + (f"<br><small>{files}</small>" if files else ""))
        self.c_summary.setPlaceholderText(catalogue.readme_summary(Path(sw["path"]))[:200]
                                          or "One or two sentences for the catalogue")
        self.pl_notes.setPlainText(sw.get("install_notes", ""))
        g = st["group"]
        self.c_group.setText(f"<span style='font-size:14pt;color:{GROUP_COLOURS[g]}'><b>{g}</b></span>"
                             f"<br>{html.escape(model.GROUP_HELP[g])}<br><i>{html.escape(st['group_note'])}</i>")
        self.c_clear.setText(
            f"<b>Cleared for a licence:</b> "
            f"<span style='color:{'#2e7d32' if st['cleared'] else '#c62828'}'>"
            f"{'Yes' if st['cleared'] else 'No'}</span> ({html.escape(st['reason'])})")
        self._fill_prov(sw)
        self._fill_lic(sw, st)
        self._fill_plat(sw)
        self._fill_gh(sw, st)
        self._loading = False

    # ---------------------------------------------------------------- classification
    def _edit_class(self):
        sw = self.cur()
        if self._loading or not sw:
            return
        sw["stage"] = self.c_stage.currentData()
        sw["suite"] = self.c_suite.text().strip()
        ov = self.c_override.currentData()
        st = self.statuses.get(sw["name"], {})
        if ov in model.RELEASABLE_GROUPS and not st.get("cleared"):
            QMessageBox.information(self, "Not Cleared",
                                    f"{sw['name']} is not Cleared ({st.get('reason')}), so it "
                                    "cannot be put in a Releasable group. Clear it first on the "
                                    "Provenance tab.")
            ov = ""
        sw["group_override"] = ov
        self.reg.save()
        self.refresh_all()

    def _edit_ai(self):
        sw = self.cur()
        if self._loading or not sw:
            return
        sw["ai"] = {"level": self.c_ai.currentData(), "what": self.c_ai_what.text().strip()}
        self.reg.save()
        self.refresh_all()

    def _edit_public(self):
        sw = self.cur()
        if self._loading or not sw:
            return
        sw["public_summary"] = self.c_summary.text().strip()
        sw["install_notes"] = self.pl_notes.toPlainText().strip()
        self.reg.save()

    def _edit_notes(self):
        sw = self.cur()
        if self._loading or not sw:
            return
        sw["notes"] = self.c_notes.toPlainText()
        self.reg.save()

    def stop_tracking(self):
        sw = self.cur()
        if not sw:
            return
        if QMessageBox.question(self, "Stop tracking",
                                f"Remove {sw['name']} from Classifier? Its folder is not touched, "
                                "and Find software will skip it from now on.") != \
                QMessageBox.StandardButton.Yes:
            return
        self.reg.ignore(sw["name"])
        self.statuses.pop(sw["name"], None)
        self.current = None
        self.reg.save()
        self.refresh_all()

    # ---------------------------------------------------------------- provenance
    def _fill_signin(self):
        state, text = review.signin_state()
        col = {"ok": "#2e7d32", "renews": "#2e7d32", "expired": "#c62828",
               "none": "#ef6c00"}[state]
        self.p_signin.setText(f"<span style='color:{col}'>{html.escape(text)}</span>")

    def claude_signin(self):
        if not review.launcher():
            QMessageBox.warning(self, "Sign in", "claude-classifier-docker is not installed.")
            return
        SigninDialog(self).exec()
        self._fill_signin()
        self.statusBar().showMessage(review.signin_state()[1], 15000)

    def _fill_prov(self, sw):
        self._fill_signin()
        scan = sw.get("scan")
        self.p_table.blockSignals(True)
        self.p_table.setRowCount(0)
        rev = sw.get("claude_review") or {}
        claude = {it.get("key"): it for it in rev.get("items", [])}
        self.p_accept.setEnabled(bool(claude))
        if not scan:
            self.p_head.setText("Not scanned yet. Press <b>Scan</b>.")
        else:
            left = len(model.unresolved(sw))
            c = scan["counts"]
            self.p_head.setText(
                f"Scanned {scan['at']}: {scan['files']} files ({c['code']} code, {c['doc']} "
                f"docs, {c['config']} config, {c['asset']} assets). "
                + (("Languages (lines): " + ", ".join(f"{k} {v:,}" for k, v in
                                                      scan.get("languages", {}).items()) + ". ")
                   if scan.get("languages") else "")
                + f"<b>{len(model.findings(sw))} item(s)</b>, "
                f"<span style='color:{'#2e7d32' if not left else '#c62828'}'>{left} unresolved</span>."
                + (f" Claude's review imported {rev.get('imported_at', '')}: "
                   f"{html.escape(rev.get('summary', ''))}" if rev else ""))
        fs = model.findings(sw) if scan else []
        self.p_table.setRowCount(len(fs))
        for r, f in enumerate(fs):
            res = model.resolution_of(sw, f)
            it = QTableWidgetItem(f["title"])
            it.setData(Qt.ItemDataRole.UserRole, f)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            ok = f not in model.unresolved(sw)
            it.setForeground(QColor("#2e7d32" if ok else "#c62828"))
            self.p_table.setItem(r, 0, it)
            combo = QComboBox()
            for k, v in model.RESOLUTIONS.items():
                combo.addItem(v + (" (auto)" if res.get("by") == "auto" and k == res.get("kind") else ""), k)
            combo.setCurrentIndex(max(0, combo.findData(res.get("kind", ""))))
            combo.currentIndexChanged.connect(lambda _i, f=f, cb=combo: self._resolve(f, kind=cb.currentData()))
            self.p_table.setCellWidget(r, 1, combo)
            lic = QComboBox()
            lic.setEditable(True)
            lic.addItems([""] + model.OPEN_LICENCES)
            lic.setCurrentText(res.get("licence", ""))
            lic.setEnabled(res.get("kind") == "opensource")
            lic.lineEdit().editingFinished.connect(lambda f=f, cb=lic: self._resolve(f, licence=cb.currentText()))
            lic.activated.connect(lambda _i, f=f, cb=lic: self._resolve(f, licence=cb.currentText()))
            self.p_table.setCellWidget(r, 2, lic)
            cl = claude.get(f["key"])
            cit = QTableWidgetItem("" if not cl else
                                   model.RESOLUTIONS.get(cl.get("verdict"), cl.get("verdict", ""))
                                   + (f" ({cl['licence']})" if cl.get("licence") else ""))
            cit.setFlags(cit.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if cl:
                cit.setToolTip(cl.get("reason", ""))
            self.p_table.setItem(r, 3, cit)
            self.p_table.setItem(r, 4, QTableWidgetItem(res.get("note", "")))
        self.p_table.blockSignals(False)
        lic_name = self.reg.settings.get("licensor") or "me"
        self.p_attest.blockSignals(True)
        self.p_attest_text.setText(
            f"I confirm that everything in {sw['name']} not listed above was written by "
            f"{lic_name} or by {self.reg.settings.get('ai_system', 'Claude')}, or follows "
            "plainly from the programming language's own logic."
            + (f"  (confirmed {sw['attested_at']})" if sw.get("attested") else ""))
        self.p_attest.setChecked(bool(sw.get("attested")))
        self.p_attest.blockSignals(False)
        self.p_detail.clear()

    def _prov_select(self):
        rows = self.p_table.selectionModel().selectedRows()
        sw = self.cur()
        if not rows or not sw:
            return
        f = self.p_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        res = model.resolution_of(sw, f)
        cl = {it.get("key"): it for it in (sw.get("claude_review") or {}).get("items", [])}.get(f["key"])
        paths = "<br>".join(html.escape(p) for p in f["paths"][:60])
        more = f"<br>… and {f.get('count', 0) - 60} more" if f.get("count", 0) > 60 else ""
        self.p_detail.setHtml(
            f"<b>{html.escape(f['title'])}</b> <small>({f['key']})</small><br>"
            f"{html.escape(f['detail'])}<br>"
            + (f"<i>Resolved by {html.escape(res.get('by', 'you'))}</i>"
               f"{': ' + html.escape(res['note']) if res.get('note') else ''}<br>" if res else "")
            + (f"<b>Claude:</b> {html.escape(cl.get('verdict', ''))}: "
               f"{html.escape(cl.get('reason', ''))}<br>" if cl else "")
            + f"<small>{paths}{more}</small>")

    def _resolve(self, f, kind=None, licence=None):
        sw = self.cur()
        if self._loading or not sw:
            return
        cur = dict(model.resolution_of(sw, f))
        if kind is not None:
            cur["kind"] = kind
            if kind != "opensource":
                cur["licence"] = ""
        if licence is not None:
            cur["licence"] = licence.strip()
        cur["by"] = "you"
        if cur.get("kind"):
            sw.setdefault("resolutions", {})[f["key"]] = cur
        else:
            sw.get("resolutions", {}).pop(f["key"], None)
        self.reg.save()
        self.statuses[sw["name"]] = status.of(sw, self.reg.settings)
        QTimer.singleShot(0, self.refresh_all)

    def _prov_note_edit(self, item):
        sw = self.cur()
        if self._loading or not sw or item.column() != 4:
            return
        f = self.p_table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        cur = dict(model.resolution_of(sw, f))
        cur["note"] = item.text()
        cur.setdefault("by", "you")
        sw.setdefault("resolutions", {})[f["key"]] = cur
        self.reg.save()

    def _attest(self, on):
        sw = self.cur()
        if self._loading or not sw:
            return
        sw["attested"] = bool(on)
        sw["attested_at"] = model.now() if on else ""
        self.reg.save()
        self.refresh_all()

    def scan_help(self):
        html_window(self, "How the provenance scan works", helptext.SCAN_HTML).exec()

    def scan_one(self):
        sw = self.cur()
        if not sw:
            return
        owners, settings = self.reg.owner_names(), self.reg.settings

        def work(progress):
            return status.scan(sw, owners, settings,
                               progress=lambda i, n: progress(f"{sw['name']} {i}/{n} files"))
        self._run(f"Scanning {sw['name']}", work, lambda _: (self.reg.save(), self.refresh_all()))

    def scan_all(self):
        sws = list(self.reg.software.values())
        owners, settings = self.reg.owner_names(), self.reg.settings

        def work(progress):
            for sw in sws:
                progress(sw["name"])
                if Path(sw["path"]).is_dir():
                    status.scan(sw, owners, settings)
        self._run("Scanning all", work, lambda _: (self.reg.save(), self.refresh_all()))

    def claude_review(self):
        sw = self.cur()
        if not sw:
            return
        if not sw.get("scan"):
            QMessageBox.information(self, "Scan first", "Scan the software first; Claude's "
                                    "brief lists what the scan found.")
            return
        if review.launcher() and review.signin_state()[0] in ("expired", "none"):
            text = review.signin_state()[1]
            if QMessageBox.question(self, "Sign in first",
                                    f"{text}. Sign in to Claude now? (Afterwards, press "
                                    "Review with Claude… again.)") == \
                    QMessageBox.StandardButton.Yes:
                self.claude_signin()
                return
        if not review.launcher():
            QMessageBox.warning(self, "claude-classifier-docker missing",
                                "claude-classifier-docker is not installed. Run:\n\n"
                                f"python3 {APP_DIR / 'claude-classifier-docker' / 'make.py'}")
            return
        if QMessageBox.question(
                self, "Review with Claude",
                f"Open Claude in claude-classifier-docker on {sw['path']}?\n\n"
                "Claude sees the folder read-only and can write only in its "
                f"{review.DIR}/ subfolder"
                + (" (added to the folder's .gitignore)" if (Path(sw["path"]) / ".git").exists() else "")
                + ". When Claude has finished, press Import Claude's review."
                + review.login_note()) != \
                QMessageBox.StandardButton.Yes:
            return
        try:
            proc, log = review.launch(sw, self.reg.settings)
        except Exception as e:
            QMessageBox.warning(self, "Review with Claude", str(e))
            return
        self.statusBar().showMessage(f"Claude's review window is opening (log: {log})", 15000)

        def check():
            if proc.poll() not in (None, 0):
                try:
                    tail = Path(log).read_text(errors="replace")[-1500:]
                except OSError:
                    tail = ""
                QMessageBox.warning(self, "claude-classifier-docker",
                                    f"It stopped with code {proc.returncode}.\n\n{tail}")
        QTimer.singleShot(8000, check)

    def import_review(self):
        sw = self.cur()
        if not sw:
            return
        try:
            data = review.load(sw)
        except Exception as e:
            QMessageBox.warning(self, "Import", f"review.json could not be read: {e}")
            return
        if not data:
            QMessageBox.information(self, "Import", f"No review yet in {sw['path']}/{review.DIR}/.")
            return
        sw["claude_review"] = data
        self.reg.save()
        self.refresh_all()
        self.statusBar().showMessage(f"Imported {len(data['items'])} verdict(s) from Claude", 8000)

    def accept_review(self):
        sw = self.cur()
        if not sw or not sw.get("claude_review"):
            return
        if QMessageBox.question(self, "Accept Claude's verdicts",
                                "Use Claude's verdict as the origin of every item it judged "
                                "(skipping 'unsure')? Your earlier choices for those items are "
                                "replaced.") != QMessageBox.StandardButton.Yes:
            return
        n = review.accept(sw)
        self.reg.save()
        self.refresh_all()
        self.statusBar().showMessage(f"Accepted {n} verdict(s)", 8000)

    # ---------------------------------------------------------------- licence
    def _fill_lic(self, sw, st):
        info = st["licence_info"]
        col = LIC_COLOURS.get(info["status"], "#000")
        self.l_status.setText(f"<span style='color:{col}'><b>{licences.STATUS_SHORT[info['status']]}</b></span>"
                              f" — {html.escape(info['message'])}")
        spdx, why = licences.recommend(sw)
        self.l_rec.setText(f"Recommended: <b>{spdx}</b>, because {html.escape(why)}."
                           + ("" if st["cleared"] else
                              "<br><span style='color:#c62828'>Not Cleared: a licence can be "
                              "written once it is.</span>"))
        self.l_choice.setCurrentText(sw.get("licence") or (spdx if spdx in licences.WRITABLE
                                                           else self.reg.settings["default_licence"]))
        self.l_write.setEnabled(st["cleared"])
        p = licences.find(Path(sw["path"])) if Path(sw["path"]).is_dir() else None
        self.l_text.setPlainText(p.read_text(encoding="utf-8", errors="replace")[:8000] if p else "")

    def preview_licence(self):
        sw = self.cur()
        if not sw:
            return
        try:
            text = licences.build(self.l_choice.currentText(),
                                  self.reg.settings.get("licensor") or "YOUR NAME",
                                  self.reg.settings["ai_system"], self.reg.settings["ai_maker"],
                                  sw["name"], ai_use=aiuse.describe(sw, self.reg.settings["ai_system"]))
        except Exception as e:
            QMessageBox.warning(self, "Preview", str(e))
            return
        html_window(self, "LICENSE preview", f"<pre>{html.escape(text)}</pre>").exec()

    def write_licence(self):
        sw = self.cur()
        if not sw:
            return
        if not self.reg.settings.get("licensor"):
            QMessageBox.information(self, "Licence", "Set your name as licence creator in Settings first.")
            return
        spdx = self.l_choice.currentText()
        existing = licences.find(Path(sw["path"]))
        if QMessageBox.question(self, "Write LICENSE",
                                f"Write {spdx} with the AI disclosure to "
                                f"{existing or Path(sw['path']) / 'LICENSE'}"
                                + (" (replacing what is there)" if existing else "")
                                + ", plus THIRD-PARTY.md if any open-source sources were named?") != \
                QMessageBox.StandardButton.Yes:
            return
        try:
            written = licences.write(sw, self.reg.settings, spdx)
        except Exception as e:
            QMessageBox.warning(self, "Write LICENSE", str(e))
            return
        sw["licence"] = spdx
        status.refresh_fingerprint(sw)
        # Writing the licence changes the folder; the scan stays valid for it.
        if sw.get("scan"):
            sw["scan"]["fingerprint"] = status._fp_cache.get(sw["path"], sw["scan"]["fingerprint"])
        self.reg.save()
        self.refresh_all()
        self.statusBar().showMessage("Wrote " + ", ".join(str(p) for p in written), 10000)

    # ---------------------------------------------------------------- GitHub
    def _fill_gh(self, sw, st):
        locs = self.placements.get(sw["name"])
        g = st["group"]
        rule = {
            model.RELEASABLE: "May be on GitHub, public or private. A public repository needs a passing licence.",
            model.RELEASABLE_SUITE: "May be on GitHub (usually in its suite's repository). Public needs a passing licence.",
            model.FINISHED_PRIVATE: "May be on GitHub only in a private repository.",
            model.SUITE_PRIVATE: "May be on GitHub only in a private repository.",
            model.DEV_PRIVATE: "Must not be on GitHub at all.",
            model.UNCLASSIFIED: "No rule until its stage is set.",
        }[g]
        if locs is None:
            where = "<i>Not checked yet this session: press Check GitHub….</i>"
        elif not locs:
            where = "Not found in any of your repositories."
        else:
            where = "<br>".join(f"{l['repo']}{'/' + l['path'] if l['path'] else ''}: "
                                f"<b>{l['visibility'].lower()}</b>, branches {', '.join(l['branches'])} "
                                f"<small>(found by {html.escape(l['how'])})</small>" for l in locs)
        self.g_text.setHtml(f"<p><b>{g}</b>: {rule}</p><p>{where}</p>")

    def start_publish(self):
        sw = self.cur()
        if not sw:
            return
        owner = self.reg.settings.get("github_owner")

        def work(progress):
            o = owner or model.gh_login()
            if not o:
                raise github.GhError("gh is not logged in. Run `gh auth login` in a terminal.")
            status.refresh_fingerprint(sw)
            return o, github.snapshot(o, progress=lambda i, n, r: progress(f"{r} ({i + 1}/{n})"))

        def done(res):
            o, snap = res
            self.snap = snap
            self.refresh_all()
            _, placements = github.audit(self.reg, snap, self.statuses)
            self.placements = placements
            PublishDialog(self, sw, o, snap, placements).exec()
            self.refresh_all()
        self._run("Reading GitHub", work, done)

    def check_github(self):
        owner = self.reg.settings.get("github_owner")

        def work(progress):
            o = owner or model.gh_login()
            if not o:
                raise github.GhError("gh is not logged in. Run `gh auth login` in a terminal.")
            for sw in self.reg.software.values():
                status.refresh_fingerprint(sw)
            snap = github.snapshot(o, progress=lambda i, n, r: progress(f"{r} ({i + 1}/{n})"))
            return snap

        def done(snap):
            self.snap = snap
            self.refresh_all()
            issues, placements = github.audit(self.reg, snap, self.statuses)
            self.placements = placements
            for n, sw in self.reg.software.items():
                if sw.get("scan"):
                    status.review_platforms(sw, placements.get(n, []))
            self.reg.save()
            self.refresh_all()
            if not any(i["action"] for i in issues):
                self.auto_catalogue(snap)       # nothing to change on GitHub: refresh now
            GithubDialog(self, issues, placements).exec()
        self._run("Reading GitHub", work, done)

    # ---------------------------------------------------------------- catalogue
    def auto_catalogue(self, snap=None):
        """Republish the catalogue in the background if it would change."""
        if not catalogue.auto_enabled(self.reg.settings):
            return
        reg = self.reg

        def work(progress):
            return catalogue.refresh(reg, progress=progress, snap=snap)
        wk = Worker(work, self)
        self.workers.append(wk)
        wk.progress.connect(lambda m: self.statusBar().showMessage(m))
        wk.done.connect(lambda r: self.statusBar().showMessage(r, 12000))
        wk.failed.connect(lambda e: self.statusBar().showMessage(
            "The catalogue could not be updated: " + e, 20000))
        wk.finished.connect(lambda: self.workers.remove(wk) if wk in self.workers else None)
        wk.start()

    def show_catalogue(self):
        owner = self.reg.settings.get("github_owner")

        def work(progress):
            o = owner or model.gh_login()
            if not o:
                raise github.GhError("gh is not logged in. Run `gh auth login` in a terminal.")
            return github.snapshot(o, progress=lambda i, n, r: progress(f"{r} ({i + 1}/{n})"))

        def done(snap):
            self.snap = snap
            self.refresh_all()
            _, self.placements = github.audit(self.reg, snap, self.statuses)
            self._catalogue_dialog()
        self._run("Reading GitHub", work, done)

    def _catalogue_dialog(self):
        text = catalogue.page(self.reg, self.statuses, self.placements)
        n = len(catalogue.listed(self.reg, self.statuses, self.placements))
        d = QDialog(self)
        d.setWindowTitle("Catalogue")
        fit(d, 900, 640)
        lay = QVBoxLayout(d)
        info = QLabel(
            f"<b>{n} software</b> listed: only what is publicly published (Releasable and "
            "in a public GitHub repository right now). Install steps come from each "
            "software's files and its platform tracks; edit its Public description "
            "(Classification tab) and Install notes (Platforms tab) to change them.")
        info.setWordWrap(True)
        lay.addWidget(info)
        tabs = QTabWidget()
        prev = QTextBrowser()
        prev.setOpenExternalLinks(True)
        import re as _re

        def preview(md):
            # Qt's Markdown has no <details>; GitHub shows them as fold-outs.
            md = _re.sub(r"<details><summary>(.*?)</summary>", r"**\1** (a fold-out on GitHub)", md)
            prev.setMarkdown(md.replace("</details>", ""))
        preview(text)
        raw = QPlainTextEdit(text)
        raw.setFont(QFont("monospace", 9))
        raw.textChanged.connect(lambda: preview(raw.toPlainText()))
        tabs.addTab(prev, "Preview")
        tabs.addTab(raw, "Markdown (editable before saving or publishing)")
        lay.addWidget(tabs, 1)
        bb = QDialogButtonBox()
        save = bb.addButton("Save as…", QDialogButtonBox.ButtonRole.ActionRole)
        pub = bb.addButton("Publish to GitHub…", QDialogButtonBox.ButtonRole.ActionRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(d.reject)

        def do_save():
            f, _ = QFileDialog.getSaveFileName(d, "Save catalogue",
                                               str(Path.home() / "CATALOGUE.md"),
                                               "Markdown (*.md)")
            if f:
                Path(f).write_text(raw.toPlainText(), encoding="utf-8")
                self.statusBar().showMessage(f"Saved {f}", 8000)

        def do_publish():
            login = self.reg.settings.get("github_owner") or model.gh_login()
            default = self.reg.settings.get("catalogue_repo") or (f"{login}/{login}" if login else "")
            repo, ok = QInputDialog.getText(
                d, "Publish catalogue",
                "Repository to put the page in, as its README.md.\n"
                f"{default} is your GitHub profile README: GitHub shows it on your "
                "profile page.", text=default)
            repo = repo.strip()
            if not ok or "/" not in repo:
                return
            exists = catalogue.repo_exists(repo)
            msg = (f"Publish the catalogue as README.md in {repo}?" if exists else
                   f"{repo} does not exist. Create it as a PUBLIC repository and publish "
                   "the catalogue as its README.md?")
            if QMessageBox.question(d, "Publish catalogue", msg + "\n\nAnyone will be "
                                    "able to read it.") != QMessageBox.StandardButton.Yes:
                return
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                url = catalogue.publish(repo, raw.toPlainText(), create=not exists)
            except Exception as e:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(d, "Publish catalogue", str(e))
                return
            QApplication.restoreOverrideCursor()
            self.reg.settings["catalogue_repo"] = repo
            self.reg.save()
            QMessageBox.information(d, "Publish catalogue", f"Published: {url}")

        save.clicked.connect(do_save)
        pub.clicked.connect(do_publish)
        lay.addWidget(bb)
        d.exec()

    # ---------------------------------------------------------------- toolbar
    def discover(self):
        if not self.reg.settings.get("roots"):
            QMessageBox.information(self, "Find software",
                                    "First list the folders that hold your software, in "
                                    "Settings… (or add one software folder with Add folder…).")
            return
        added = self.reg.discover()
        self.reg.save()
        self.refresh_all()
        QMessageBox.information(self, "Find software",
                                ("Added: " + ", ".join(added)) if added else "No new software found.")

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Software folder", str(Path.home()))
        if d:
            self.current = self.reg.add(d)
            self.reg.save()
            self.refresh_all()

    def settings(self):
        if SettingsDialog(self.reg, self).exec():
            self.refresh_all()


def run(argv):
    import os
    os.environ.setdefault("RESOURCE_NAME", "classifier")
    app = QApplication(argv)
    app.setApplicationName("Classifier")
    app.setDesktopFileName("classifier")
    w = MainWindow()
    w.show()
    return app.exec()
