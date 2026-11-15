"""Reading and writing a software's licence.

A licence passes when it is (1) an open-source licence and (2) makes clear the
software was created using the named artificial intelligence system.  The
licence Classifier writes is the standard text, preceded by an AI DISCLOSURE
section, with a THIRD-PARTY.md listing every open-source source the
provenance review named.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import model

LICENCE_NAMES = ["LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md",
                 "LICENCE.txt", "COPYING", "COPYING.md", "COPYING.txt"]
COMMON = Path("/usr/share/common-licenses")

# Licences Classifier can write, and where the full text comes from.
WRITABLE = {
    "MIT": "built-in",
    "BSD-3-Clause": "built-in",
    "Apache-2.0": "Apache-2.0",
    "GPL-3.0-or-later": "GPL-3",
    "GPL-3.0-only": "GPL-3",
    "LGPL-3.0-or-later": "LGPL-3",
    "MPL-2.0": "MPL-2.0",
}

MIT_TEXT = """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

BSD3_TEXT = """BSD 3-Clause License

Copyright (c) {year}, {holder}

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

DETECT = [
    (r"GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL-3.0"),
    (r"GNU LESSER GENERAL PUBLIC LICENSE\s+Version 3", "LGPL-3.0"),
    (r"GNU LESSER GENERAL PUBLIC LICENSE\s+Version 2\.1", "LGPL-2.1"),
    (r"GNU GENERAL PUBLIC LICENSE\s+Version 3", "GPL-3.0"),
    (r"GNU GENERAL PUBLIC LICENSE\s+Version 2", "GPL-2.0"),
    (r"Apache License\s+Version 2\.0", "Apache-2.0"),
    (r"Mozilla Public License,? [Vv]ersion 2\.0", "MPL-2.0"),
    (r"Permission is hereby granted, free of charge", "MIT"),
    (r"Redistribution and use in source and binary forms[\s\S]*Neither the name", "BSD-3-Clause"),
    (r"Redistribution and use in source and binary forms", "BSD-2-Clause"),
    (r"Permission to use, copy, modify, and(/or)? distribute this software for any", "ISC"),
    (r"This is free and unencumbered software released into the public domain", "Unlicense"),
    (r"Boost Software License", "BSL-1.0"),
    (r"Eclipse Public License - v 2\.0", "EPL-2.0"),
    (r"SIL OPEN FONT LICENSE", "OFL-1.1"),
    (r"CC0 1\.0 Universal", "CC0-1.0"),
    (r"zlib License|This software is provided 'as-is', without any express or implied", "Zlib"),
]


def find(root: Path) -> Path | None:
    for n in LICENCE_NAMES:
        p = Path(root) / n
        if p.is_file():
            return p
    return None


def detect(text: str) -> str:
    m = re.search(r"SPDX-License-Identifier:\s*([A-Za-z0-9.\-+ ()]+)", text)
    if m:
        return m.group(1).strip()
    for pat, spdx in DETECT:
        if re.search(pat, text, re.I if spdx in {"MIT", "ISC"} else 0):
            return spdx
    return ""


def ai_disclosed(text: str, ai: str) -> bool:
    return (re.search(rf"\b{re.escape(ai)}\b", text, re.I) is not None and
            re.search(r"artificial intelligence|\bAI\b", text) is not None)


def check(root: Path, ai: str = "Claude") -> dict:
    """{'status': ok|missing|unknown|not-open|no-ai, 'spdx', 'file', 'message'}."""
    p = find(root)
    if not p:
        return {"status": "missing", "spdx": "", "file": "",
                "message": "No LICENSE file."}
    text = p.read_text(encoding="utf-8", errors="replace")
    spdx = detect(text)
    out = {"spdx": spdx, "file": p.name}
    if not spdx:
        out.update(status="unknown", message=f"{p.name}: licence not recognised.")
    elif not model.is_open_licence(spdx):
        out.update(status="not-open", message=f"{p.name}: {spdx} is not an open-source licence.")
    elif not ai_disclosed(text, ai):
        out.update(status="no-ai", message=f"{p.name}: {spdx}, but it does not say the "
                                            f"software was created using {ai}.")
    else:
        out.update(status="ok", message=f"{p.name}: {spdx}, open-source, AI use disclosed.")
    return out


STATUS_SHORT = {"ok": "OK", "missing": "None", "unknown": "Unrecognised",
                "not-open": "Not open", "no-ai": "No AI notice"}


def recommend(sw: dict) -> tuple[str, str]:
    """(SPDX, why) for a software, from the licences of what it imports."""
    deps = []
    for f in model.findings(sw):
        if f["kind"] == "dep":
            r = model.resolution_of(sw, f)
            if r.get("kind") == "opensource":
                deps.append((f["title"].split()[-1], r.get("licence", "")))
    strong = [d for d, lic in deps if re.match(r"^(A?GPL)", lic)]
    if any(lic.startswith("AGPL") for _, lic in deps):
        return "AGPL-3.0-or-later", "it imports AGPL code (" + ", ".join(strong) + ")"
    if strong:
        return ("GPL-3.0-or-later",
                "it imports GPL code (" + ", ".join(strong) + "); a release that includes "
                "or links GPL libraries is safest under the GPL. MIT also works for your "
                "own files, but the program as a whole is then covered by the GPL.")
    return "MIT", "nothing it uses requires a copyleft licence"


def disclosure(licensor: str, ai: str, maker: str, name: str, spdx: str,
               ai_use: str = "") -> str:
    use = ""
    if ai_use:
        import textwrap
        use = "\n".join(textwrap.wrap("Use of AI when running: " + ai_use, 79)) + "\n\n"
    return (
        "AI DISCLOSURE\n"
        "=============\n\n"
        f"{name} was created by {licensor} using the artificial intelligence system\n"
        f"{ai}, made by {maker}. Its code was written by {licensor} and by {ai}\n"
        "working together, or follows from open-source sources used under their own\n"
        "open-source licences (listed in THIRD-PARTY.md where there are any).\n\n"
        f"It is released under the open-source licence below ({spdx}). Anyone using,\n"
        "copying, changing or redistributing it should know that it was made with an\n"
        "artificial intelligence system, and should keep this notice with it.\n\n"
        + use +
        "-------------------------------------------------------------------------------\n\n")


def build(spdx: str, licensor: str, ai: str, maker: str, name: str,
          year: int | None = None, ai_use: str = "") -> str:
    year = year or int(time.strftime("%Y"))
    src = WRITABLE.get(spdx)
    if not src:
        raise ValueError(f"Classifier cannot write {spdx}; choose one of {', '.join(WRITABLE)}")
    head = disclosure(licensor, ai, maker, name, spdx, ai_use)
    if spdx == "MIT":
        body = MIT_TEXT.format(year=year, holder=licensor)
    elif spdx == "BSD-3-Clause":
        body = BSD3_TEXT.format(year=year, holder=licensor)
    else:
        f = COMMON / src
        if not f.is_file():
            raise FileNotFoundError(f"the full text of {spdx} is not on this system ({f})")
        full = f.read_text(encoding="utf-8")
        if spdx.startswith(("GPL", "LGPL")):
            later = spdx.endswith("or-later")
            kind = "Lesser General" if spdx.startswith("LGPL") else "General"
            body = (f"{name}\nCopyright (C) {year} {licensor}\n\n"
                    "This program is free software: you can redistribute it and/or modify\n"
                    f"it under the terms of the GNU {kind} Public License as published by\n"
                    "the Free Software Foundation, version 3"
                    + (" of the License, or\n(at your option) any later version.\n\n" if later
                       else " of the License.\n\n")
                    + "This program is distributed in the hope that it will be useful,\n"
                    "but WITHOUT ANY WARRANTY; without even the implied warranty of\n"
                    "MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the\n"
                    f"GNU {kind} Public License for more details.\n\n"
                    "-------------------------------------------------------------------------------\n\n"
                    + full)
            if spdx.startswith("LGPL") and (COMMON / "GPL-3").is_file():
                body += ("\n-------------------------------------------------------------------------------\n"
                         "The LGPL supplements the GNU General Public License, version 3:\n\n"
                         + (COMMON / "GPL-3").read_text(encoding="utf-8"))
        else:
            body = f"Copyright {year} {licensor}\n\n" + full
    return f"SPDX-License-Identifier: {spdx}\n\n" + head + body


def third_party(sw: dict) -> str:
    rows = []
    for f in model.findings(sw):
        r = model.resolution_of(sw, f)
        if r.get("kind") == "opensource":
            rows.append(f"| {f['title']} | {r.get('licence', '')} | {r.get('note', '')} |")
    if not rows:
        return ""
    return ("# Third-party sources\n\n"
            f"{sw['name']} uses these open-source sources, each under its own licence.\n\n"
            "| Source | Licence | Note |\n|---|---|---|\n" + "\n".join(rows) + "\n")


def write(sw: dict, settings: dict, spdx: str) -> list[Path]:
    """Write LICENSE (replacing any LICENSE-like file's content) and THIRD-PARTY.md."""
    root = Path(sw["path"])
    target = find(root) or root / "LICENSE"
    from . import aiuse
    text = build(spdx, settings["licensor"], settings["ai_system"], settings["ai_maker"],
                 sw["name"], ai_use=aiuse.describe(sw, settings["ai_system"]))
    target.write_text(text, encoding="utf-8")
    out = [target]
    tp = third_party(sw)
    if tp:
        (root / "THIRD-PARTY.md").write_text(tp, encoding="utf-8")
        out.append(root / "THIRD-PARTY.md")
    return out
