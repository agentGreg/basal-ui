"""The Polish dictionary of basal-ui (basal/ui/static/i18n.js) against the strings the frontend passes to t() / tp()."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).parents[1] / "basal/ui/static"
JS_STRING = r"""(['"])((?:\\.|(?!\1).)*)\1"""  # a single- or double-quoted JS string literal


def dictionary(name):
    """The `const <name> = {...};` object of i18n.js: JSON string keys, one entry per line, `//` comment lines."""
    block = re.search(rf"^const {name} = \{{\n(.*?)^\}};", (STATIC / "i18n.js").read_text(), re.DOTALL | re.MULTILINE)
    entries = [line for line in block.group(1).splitlines() if not line.lstrip().startswith("//")]
    return dict(json.loads("{" + "\n".join(entries).rstrip().rstrip(",") + "}"))


def literals(pattern):
    """(file, key) of every literal first argument the frontend scripts pass to t( / tp(n, ."""
    out = []
    for f in sorted(STATIC.glob("*.js")):
        if f.name == "i18n.js":
            continue
        for m in re.finditer(pattern + JS_STRING, f.read_text()):
            out.append((f.name, re.sub(r"\\(.)", r"\1", m.group(2))))
    return out


def placeholders(text):
    return sorted(re.findall(r"\{(\w+)\}", text))


def test_every_t_key_in_the_frontend_has_a_polish_translation():
    pl = dictionary("PL")
    keys = literals(r"(?<![\w.$])t\(\s*")
    assert len(keys) > 300  # the scan sees the frontend's strings, not an empty pattern
    missing = sorted({f"{f}: {k}" for f, k in keys if k not in pl})
    assert not missing, "\n".join(missing)


def test_every_tp_key_has_three_polish_plural_forms():
    plural = dictionary("PL_PLURAL")
    keys = literals(r"(?<![\w.$])tp\([^,()]+(?:\([^()]*\))?[^,()]*,\s*")
    assert keys
    missing = sorted({f"{f}: {k}" for f, k in keys if k not in plural})
    assert not missing, "\n".join(missing)
    for key, forms in plural.items():
        assert len(forms) == 3, key
        assert all(placeholders(form) == placeholders(key) for form in forms), key


def test_polish_translations_keep_placeholders_and_are_safe_in_html():
    for key, value in dictionary("PL").items():
        if key.startswith("code:") or "<" in key:  # server templates: tests/test_ui_catalog.py; literal <model> paths
            continue
        assert placeholders(value) == placeholders(key), key
        assert not set('<>&"') & set(value), key  # t() does not escape and its output goes into innerHTML
        assert value.strip(), key


SAME_IN_POLISH = {  # names, code, paths and keys: translateStatic leaves them as they are
    "basal", "basal-viz", "API", "Python", "curl", "TypeSafe SDK", "OpenAI SDK", "LiteLLM", "Port", "System",
    "/v1/systemone", "docs/SERVING.md#inference-on-a-jsonl-file", '{"state", "question", "options", "gold"?}',
    "Alt + 0", "Alt + 1–9", "Ctrl/⌘ + Enter", "https://app.example.com", "~/basal-decisions.jsonl",
}


class StaticTexts(HTMLParser):
    """The text nodes and title / placeholder / aria-label values translateStatic looks up in index.html."""
    def __init__(self):
        super().__init__()
        self.texts, self.code = set(), 0

    def handle_starttag(self, tag, attrs):
        self.code += tag in ("script", "style")
        self.texts |= {v.strip() for k, v in attrs if k in ("title", "placeholder", "aria-label") and v and v.strip()}

    def handle_endtag(self, tag):
        self.code -= tag in ("script", "style")

    def handle_data(self, data):
        if not self.code and data.strip():
            self.texts.add(data.strip())


def test_every_static_text_of_the_page_has_a_polish_translation():
    page = StaticTexts()
    page.feed((STATIC / "index.html").read_text())
    assert len(page.texts) > 150
    pl = dictionary("PL")
    missing = sorted(t for t in page.texts - SAME_IN_POLISH if t not in pl and re.search(r"[A-Za-z]{2}", t))
    assert not missing, "\n".join(missing)
