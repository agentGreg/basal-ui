"""The Polish dictionary of basal-ui (basal/ui/static/i18n.js) against the strings the frontend passes to t() / tp()."""
import json
import re
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
