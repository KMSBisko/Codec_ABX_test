"""Every translation key the code asks for must exist in BOTH languages.

`self._t(key)` raises KeyError, so a missing key is a crash in the desktop app
at the moment a user opens the diagnostics panel. PyQt6 cannot be imported in
CI (it needs libGL), so this checks the dictionaries by parsing the source.
"""
import ast
import pathlib
import re

MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"
REPORT = pathlib.Path(__file__).resolve().parents[1] / "core" / "report.py"
JS = pathlib.Path(__file__).resolve().parents[1] / "web" / "static" / "js" / "app.js"


def _translation_dicts():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    dicts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if "pvalue" in keys:  # the EN and VI UI dictionaries
                dicts.append(keys)
    return dicts


def _referenced_keys():
    src = MAIN.read_text(encoding="utf-8")
    keys = set(re.findall(r"self\._t\(['\"]([^'\"]+)['\"]\)", src))
    keys |= set(
        re.findall(r"tr\(['\"]([^'\"]+)['\"]\)", REPORT.read_text(encoding="utf-8"))
    )
    from core.report import DEFAULT_LABELS

    keys |= set(DEFAULT_LABELS)
    return keys


def test_both_language_dicts_exist():
    dicts = _translation_dicts()
    assert len(dicts) == 2, f"expected EN + VI dictionaries, found {len(dicts)}"


def test_no_key_is_referenced_without_a_translation():
    dicts = _translation_dicts()
    referenced = _referenced_keys()
    for index, keys in enumerate(dicts):
        missing = sorted(referenced - keys)
        assert not missing, f"translation dict #{index} is missing: {missing}"


def test_languages_stay_in_key_parity():
    en, vi = _translation_dicts()
    assert en - vi == set(), f"VI dictionary missing {sorted(en - vi)}"
    assert vi - en == set(), f"EN dictionary missing {sorted(vi - en)}"


def test_web_js_translation_languages_have_key_parity():
    """The web i18n dictionaries are a separate, hand-maintained surface."""
    src = JS.read_text(encoding="utf-8")
    en = set(re.findall(r"^\s{8}([a-z_][a-z0-9_]*):", src.split("    vi: {")[0], re.M))
    vi_src = src.split("    vi: {")[1].split("\n    },")[0]
    vi = set(re.findall(r"^\s{8}([a-z_][a-z0-9_]*):", vi_src, re.M))
    missing_vi = sorted(k for k in en - vi if not k.startswith("_"))
    assert not missing_vi, f"web VI dictionary missing {missing_vi}"
