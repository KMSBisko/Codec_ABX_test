"""Architecture guard: `core/` must stay shell-independent.

Both shells are supposed to be thin over core. That only holds if core never
imports Qt, Flask, or an audio backend -- otherwise the desktop engine and the
web engine silently diverge again, which is exactly the state Phase 0 started
from.
"""
import ast
import pathlib

CORE = pathlib.Path(__file__).resolve().parents[1] / "core"

FORBIDDEN = {
    "PyQt6", "PySide6", "flask", "sounddevice", "soundfile", "pyloudnorm",
    "scipy", "tkinter", "matplotlib",
}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(node.module.split(".")[0])
    return found


def test_core_exists_and_is_populated():
    modules = list(CORE.glob("*.py"))
    assert len(modules) >= 3
    names = {m.stem for m in modules}
    assert {"abx", "stats", "alignment"} <= names


def test_core_never_imports_a_gui_or_web_framework():
    offenders = {}
    for module in CORE.glob("*.py"):
        bad = _imports(module) & FORBIDDEN
        if bad:
            offenders[module.name] = sorted(bad)
    assert not offenders, f"core modules importing shell/UI deps: {offenders}"


def test_core_is_importable_with_stdlib_plus_numpy_only():
    import importlib

    for name in ("core.stats", "core.abx", "core.alignment"):
        importlib.import_module(name)


def _code_idents(path):
    """Identifiers used in real code only -- docstrings must not trip guards."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def _root():
    return pathlib.Path(__file__).resolve().parents[1]


def test_desktop_shim_uses_the_canonical_engine():
    """app.abx_engine must delegate, not re-implement trial logic."""
    src = (_root() / "app" / "abx_engine.py").read_text(encoding="utf-8")
    assert "from core.abx import" in src
    idents = _code_idents(_root() / "app" / "abx_engine.py")
    imports = _imports(_root() / "app" / "abx_engine.py")
    assert "random" not in imports, "shim must not own randomness"
    assert "comb" not in idents, "shim must not own statistics"


def test_web_shell_uses_the_canonical_engine():
    src = (_root() / "web" / "app.py").read_text(encoding="utf-8")
    assert "from core.abx import" in src
    assert "_blind_no_change_streak" not in _code_idents(_root() / "web" / "app.py"), (
        "streak-based blinding heuristic must be gone"
    )
    idents = _code_idents(_root() / "web" / "app.py")
    assert "comb" not in idents, "web must not own statistics"
