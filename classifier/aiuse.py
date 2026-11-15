"""Does the software connect to an artificial intelligence system, and does it
still work without it?

The scan looks for connections (AI client libraries, the Claude command line or
claude-docker, AI service addresses and API-key variables, MCP servers that offer
tools to AI assistants) and suggests one of:

  none      no part connects to an AI system
  optional  a part connects to an AI system; the rest works without it
  required  the software does not work without an AI system
  for_ai    made to be used by AI assistants (e.g. an MCP server)

The suggestion is a judgement from where the connections sit; the licensor
confirms or changes it, and says what the AI part does.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from . import scanner

LEVELS = {
    "": "Automatic (use the suggestion)",
    "none": "No AI connection",
    "optional": "Works without AI; has a part that connects to AI",
    "required": "Needs AI to work",
    "for_ai": "Made for AI assistants to use (e.g. an MCP server)",
}
SHORT = {"none": "None", "optional": "Optional part", "required": "Required",
         "for_ai": "For AI assistants", "": ""}

# Python import name -> AI system.  Extend in ~/.config/classifier/ai-systems.json.
PY_MODULES = {
    # Anthropic
    "anthropic": "Claude (Anthropic API)", "claude_agent_sdk": "Claude (Agent SDK)",
    "claude_code_sdk": "Claude (Code SDK)",
    # OpenAI and Microsoft
    "openai": "OpenAI (or an OpenAI-compatible service)", "tiktoken": "OpenAI (tokeniser)",
    "azure.ai.inference": "Azure AI", "azure.ai.openai": "Azure OpenAI",
    "semantic_kernel": "an AI framework (Semantic Kernel)", "autogen": "an AI framework (AutoGen)",
    # Google
    "google.generativeai": "Gemini", "google.genai": "Gemini",
    "vertexai": "Google Vertex AI", "google.cloud.aiplatform": "Google Vertex AI",
    # Other hosted models
    "mistralai": "Mistral", "cohere": "Cohere", "groq": "Groq", "replicate": "Replicate",
    "together": "Together AI", "fireworks": "Fireworks AI", "xai_sdk": "Grok (xAI)",
    "perplexity": "Perplexity", "ai21": "AI21", "reka": "Reka", "voyageai": "Voyage AI",
    "huggingface_hub": "Hugging Face (models or inference)",
    "boto3": None,   # only counts with Bedrock, see TEXT_SIGNS
    # Local models
    "ollama": "a local model (Ollama)", "llama_cpp": "a local model (llama.cpp)",
    "gpt4all": "a local model (GPT4All)", "vllm": "a local model (vLLM)",
    "mlx_lm": "a local model (MLX)", "ctransformers": "a local model (ctransformers)",
    "transformers": "a machine-learning model (Hugging Face Transformers)",
    "sentence_transformers": "a machine-learning model (Sentence Transformers)",
    "whisper": "a speech model (Whisper)", "faster_whisper": "a speech model (Whisper)",
    "diffusers": "an image model (Diffusers)",
    # Frameworks and gateways
    "langchain": "an AI framework (LangChain)", "langchain_core": "an AI framework (LangChain)",
    "langchain_openai": "OpenAI (via LangChain)", "langchain_anthropic": "Claude (via LangChain)",
    "llama_index": "an AI framework (LlamaIndex)", "litellm": "an AI gateway (LiteLLM)",
    "dspy": "an AI framework (DSPy)", "instructor": "an AI framework (Instructor)",
    "pydantic_ai": "an AI framework (Pydantic AI)", "smolagents": "an AI framework (smolagents)",
    "crewai": "an AI framework (CrewAI)", "haystack": "an AI framework (Haystack)",
}
PY_MODULES = {k: v for k, v in PY_MODULES.items() if v}
MCP_MODULES = {"mcp", "fastmcp"}
JS_PACKAGES = {
    "@anthropic-ai/sdk": "Claude (Anthropic API)",
    "@anthropic-ai/claude-agent-sdk": "Claude (Agent SDK)",
    "openai": "OpenAI (or an OpenAI-compatible service)", "@azure/openai": "Azure OpenAI",
    "@google/generative-ai": "Gemini", "@google/genai": "Gemini",
    "@google-cloud/vertexai": "Google Vertex AI", "@mistralai/mistralai": "Mistral",
    "cohere-ai": "Cohere", "groq-sdk": "Groq", "replicate": "Replicate",
    "together-ai": "Together AI", "@huggingface/inference": "Hugging Face (inference)",
    "@huggingface/transformers": "a machine-learning model (Transformers.js)",
    "@xenova/transformers": "a machine-learning model (Transformers.js)",
    "ollama": "a local model (Ollama)", "@mlc-ai/web-llm": "a local model (WebLLM)",
    "ai": "an AI framework (Vercel AI SDK)", "langchain": "an AI framework (LangChain)",
    "@langchain/core": "an AI framework (LangChain)", "llamaindex": "an AI framework (LlamaIndex)",
    "@modelcontextprotocol/sdk": "MCP",
}
TEXT_SIGNS = [
    # command-line assistants started by the program
    (r"""["'](claude-docker|claude-classifier-docker)["']""", "Claude (in a container)"),
    (r"""\[\s*["']claude["']\s*,|which\(\s*["']claude["']\s*\)|["']claude\s+-p\b""",
     "Claude (Claude Code command line)"),
    (r"""\[\s*["'](codex|openai)["']\s*,|which\(\s*["']codex["']\s*\)""",
     "OpenAI (Codex / OpenAI command line)"),
    (r"""\[\s*["']gemini["']\s*,|which\(\s*["']gemini["']\s*\)""", "Gemini (Gemini command line)"),
    (r"""\[\s*["']ollama["']\s*,|which\(\s*["']ollama["']\s*\)""", "a local model (Ollama)"),
    (r"""\[\s*["']llm["']\s*,""", "an AI model (llm command line)"),
    (r"""\[\s*["']gh["']\s*,\s*["']copilot["']""", "GitHub Copilot"),
    # service addresses
    (r"api\.anthropic\.com", "Claude (Anthropic API)"),
    (r"api\.openai\.com", "OpenAI"),
    (r"\.openai\.azure\.com", "Azure OpenAI"),
    (r"generativelanguage\.googleapis\.com", "Gemini"),
    (r"aiplatform\.googleapis\.com", "Google Vertex AI"),
    (r"bedrock-runtime|[\"']bedrock-runtime[\"']", "Amazon Bedrock"),
    (r"api\.mistral\.ai", "Mistral"), (r"api\.cohere\.(ai|com)", "Cohere"),
    (r"api\.groq\.com", "Groq"), (r"api\.x\.ai", "Grok (xAI)"),
    (r"api\.deepseek\.com", "DeepSeek"), (r"api\.perplexity\.ai", "Perplexity"),
    (r"openrouter\.ai/api", "an AI gateway (OpenRouter)"),
    (r"api\.together\.(xyz|ai)", "Together AI"), (r"api\.fireworks\.ai", "Fireworks AI"),
    (r"api-inference\.huggingface\.co|router\.huggingface\.co", "Hugging Face (inference)"),
    (r"localhost:11434|127\.0\.0\.1:11434", "a local model (Ollama)"),
    (r"localhost:1234/v1|127\.0\.0\.1:1234/v1", "a local model (LM Studio)"),
    (r"/v1/chat/completions", "an OpenAI-compatible model service"),
    # API-key variables
    (r"\bANTHROPIC_API_KEY\b", "Claude (Anthropic API)"),
    (r"\bOPENAI_API_KEY\b", "OpenAI"), (r"\bAZURE_OPENAI_(API_)?KEY\b", "Azure OpenAI"),
    (r"\b(GEMINI|GOOGLE)_API_KEY\b", "Gemini"), (r"\bMISTRAL_API_KEY\b", "Mistral"),
    (r"\b(COHERE|CO)_API_KEY\b", "Cohere"), (r"\bGROQ_API_KEY\b", "Groq"),
    (r"\bXAI_API_KEY\b", "Grok (xAI)"), (r"\bDEEPSEEK_API_KEY\b", "DeepSeek"),
    (r"\bPERPLEXITY_API_KEY\b", "Perplexity"), (r"\bOPENROUTER_API_KEY\b",
                                                   "an AI gateway (OpenRouter)"),
    (r"\bTOGETHER_API_KEY\b", "Together AI"), (r"\bREPLICATE_API_TOKEN\b", "Replicate"),
    (r"\bHF_TOKEN\b|\bHUGGINGFACEHUB_API_TOKEN\b", "Hugging Face (models or inference)"),
]
USER_TABLE = Path.home() / ".config" / "classifier" / "ai-systems.json"
USER_TABLE_EXAMPLE = {
    "_help": "Add AI systems Classifier should recognise. python/js: import name -> "
             "system; text: regular expression -> system (matched in code, not comments).",
    "python": {"example_ai_sdk": "Example AI"},
    "js": {"@example/ai": "Example AI"},
    "text": {"api\\.example-ai\\.com": "Example AI"},
}


def load_user_table():
    """Merge the licensor's own additions (ai-systems.json) into the tables."""
    import json
    import os
    path = Path(os.environ.get("CLASSIFIER_CONFIG", USER_TABLE.parent)) / USER_TABLE.name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    PY_MODULES.update({k: v for k, v in (data.get("python") or {}).items()
                       if not k.startswith("example")})
    JS_PACKAGES.update({k: v for k, v in (data.get("js") or {}).items()
                        if not k.startswith("@example")})
    for pat, system in (data.get("text") or {}).items():
        if "example" in pat:
            continue
        try:
            re.compile(pat)
        except re.error:
            continue
        TEXT_SIGNS.append((pat, system))


load_user_table()
GUARD = re.compile(r"except\s+(ImportError|ModuleNotFoundError|FileNotFoundError|OSError)"
                   r"|shutil\.which|if\s+not\s+\w*(launcher|exe|key|available)\w*|is None")


def _py_ai_imports(text: str) -> tuple[set, set, bool]:
    """(AI systems, MCP?, any AI import unguarded at module top level)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set(), False, False
    systems, mcp, top_unguarded = set(), False, False

    def names(node):
        if isinstance(node, ast.Import):
            return [a.name for a in node.names]
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            return [node.module]
        return []

    def hit(n):
        for mod, system in PY_MODULES.items():
            if n == mod or n.startswith(mod + "."):
                return system
        return None

    for node in ast.walk(tree):
        for n in names(node):
            s = hit(n)
            if s:
                systems.add(s)
            if n.split(".")[0] in MCP_MODULES:
                mcp = True
    for node in tree.body:                       # module level, not inside try/def
        for n in names(node):
            if hit(n):
                top_unguarded = True
    return systems, mcp, top_unguarded


def review(root: Path | str, web_pages=()) -> dict:
    root = Path(root)
    signs: dict[str, set] = {}          # system -> files
    mcp_files, unguarded_files, guarded_files = set(), set(), set()
    code_files = []
    for rel, p in scanner._walk(root):
        ext = p.suffix.lower()
        if ext not in scanner.CODE_EXT or ext in {".sh", ".ps1", ".bat", ".cmd"}:
            continue
        if Path(rel).name.startswith(("test_", "_")) or "/tests/" in f"/{rel}":
            continue                   # tests and self-checks are not the program
        text = scanner._read_text(p)
        if text is None:
            continue
        code_files.append(rel)
        found = set()
        if ext == ".py":
            systems, mcp, top = _py_ai_imports(text)
            found |= systems
            if mcp:
                mcp_files.add(rel)
            if top:
                unguarded_files.add(rel)
        if ext in {".js", ".mjs", ".ts", ".html", ".htm"}:
            for m in scanner.JS_IMPORT.finditer(text):
                spec = next(g for g in m.groups() if g)
                for pkg, system in JS_PACKAGES.items():
                    if spec == pkg or spec.startswith(pkg + "/"):
                        (mcp_files.add(rel) if system == "MCP" else found.add(system))
        code_text = _without_docs(text) if ext == ".py" else text
        for pat, system in TEXT_SIGNS:
            for m in re.finditer(pat, code_text):
                line = code_text[code_text.rfind("\n", 0, m.start()) + 1:
                                 code_text.find("\n", m.end())]
                if re.search(r"""\br["']""", line):
                    continue           # a pattern written as a raw string, not a use
                found.add(system)
                break
        if found:
            for s in found:
                signs.setdefault(s, set()).add(rel)
            if GUARD.search(text):
                guarded_files.add(rel)
    ai_files = set().union(*signs.values()) if signs else set()
    systems = sorted(signs)
    n_code = max(len(code_files), 1)

    # The suggestion and why.
    if not ai_files and not mcp_files:
        level, why = "none", "No code connects to an AI system."
    elif mcp_files and len(mcp_files) * 2 >= n_code:
        level, why = "for_ai", (f"{len(mcp_files)} of {n_code} code files are MCP code: "
                                "it mainly offers tools to AI assistants.")
    else:
        entry = _entry_files(root, code_files)
        share = len(ai_files) / n_code
        entry_ai = sorted(set(entry) & (unguarded_files | (ai_files - guarded_files)))
        if entry_ai and share >= 0.5:
            level, why = "required", (f"The program's main file ({entry_ai[0]}) connects to "
                                      f"AI directly, and {len(ai_files)} of {n_code} code "
                                      "files do.")
        elif share >= 0.6:
            level, why = "required", (f"{len(ai_files)} of {n_code} code files connect to "
                                      "AI: most of the program depends on it.")
        else:
            bits = [f"AI connections are in {len(ai_files)} of {n_code} code files"]
            if guarded_files & ai_files:
                bits.append("and are checked for before use (missing tool or library is "
                            "handled)")
            if entry and not entry_ai:
                bits.append(f"while the main file ({entry[0]}) starts without them")
            if mcp_files:
                bits.append(f"plus {len(mcp_files)} MCP file(s) offering tools to AI assistants")
            level, why = "optional", ", ".join(bits) + "."
    return {
        "suggested": level, "why": why, "systems": systems,
        "files": {s: sorted(f)[:20] for s, f in signs.items()},
        "mcp_files": sorted(mcp_files)[:20],
    }


def _without_docs(text: str) -> str:
    """Python source with comments, docstrings and long triple-quoted text (help
    pages, briefs) blanked out: mentioning an AI service is not connecting to it."""
    import io
    import tokenize
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    lines = text.splitlines(keepends=True)
    cut = []
    for t in toks:
        if t.type == tokenize.COMMENT:
            cut.append((t.start, t.end))
        elif t.type == tokenize.STRING and t.string.lstrip("rRbBuUfF").startswith(('"""', "\'\'\'")):
            cut.append((t.start, t.end))
    for (sr, sc), (er, ec) in reversed(cut):
        if sr == er:
            ln = lines[sr - 1]
            lines[sr - 1] = ln[:sc] + " " * (ec - sc) + ln[ec:]
        else:
            lines[sr - 1] = lines[sr - 1][:sc] + "\n"
            for k in range(sr, er - 1):
                lines[k] = "\n"
            lines[er - 1] = " " * ec + lines[er - 1][ec:]
    return "".join(lines)


def _entry_files(root: Path, code_files: list[str]) -> list[str]:
    top = [f for f in code_files if "/" not in f]
    pref = [f for f in top if f.endswith("_app.py")]
    pref += [f for f in top if Path(f).stem.lower() == root.name.lower()]
    pref += [f for f in top if f in ("main.py", "app.py", "index.html", "game.html")]
    return pref or top[:1]


def level(sw: dict) -> str:
    chosen = (sw.get("ai") or {}).get("level", "")
    if chosen:
        return chosen
    return (sw.get("ai_review") or {}).get("suggested", "")


def describe(sw: dict, ai_name: str = "Claude") -> str:
    """One sentence for people: e.g. 'Runs without AI; an optional part connects
    to Claude (reviews provenance).'"""
    lv = level(sw)
    rev = sw.get("ai_review") or {}
    what = (sw.get("ai") or {}).get("what", "").strip()
    systems = _plain_systems(rev.get("systems", [])) or "an AI system"
    tail = f" ({what})" if what else ""
    if lv == "none":
        return "Does not connect to any AI system."
    if lv == "optional":
        return f"Runs without AI; an optional part connects to {systems}{tail}."
    if lv == "required":
        return f"Needs {systems} to work{tail}."
    if lv == "for_ai":
        return f"Made for AI assistants to use, e.g. as an MCP server{tail}."
    return ""


def _plain_systems(systems: list[str]) -> str:
    names = []
    for s in systems:
        # "Claude (Anthropic API)" -> "Claude"; "a local model (Ollama)" stays whole
        base = s if s.startswith(("a ", "an ")) else s.split(" (")[0]
        if base not in names:
            names.append(base)
    return " and ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1 else names)
