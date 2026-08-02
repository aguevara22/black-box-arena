"""AST lint for declared independent implementation routes."""

import ast
from pathlib import Path
import tempfile

from .manifest import IndependencePair


def _module_file(module_name: str, package_root: Path) -> Path:
    stem = package_root.joinpath(*module_name.split("."))
    module_file = stem.with_suffix(".py")
    if module_file.is_file():
        return module_file
    package_file = stem / "__init__.py"
    if package_file.is_file():
        return package_file
    raise FileNotFoundError(f"cannot resolve module {module_name!r}")


def _resolve_from(module_name: str, level: int, imported: str | None) -> str:
    package = module_name.split(".")[:-1]
    if level:
        keep = len(package) - (level - 1)
        base = package[:max(0, keep)]
    else:
        base = []
    if imported:
        base.extend(imported.split("."))
    return ".".join(base)


def _imports(module_name: str, path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(module_name, node.level, node.module)
            if base:
                found.add(base)
            for alias in node.names:
                if alias.name != "*":
                    found.add(".".join(part for part in (base, alias.name) if part))
    return found


def _matches(imported: set[str], target: str) -> bool:
    return any(name == target or name.startswith(target + ".") for name in imported)


def lint(pairs, package_root) -> list[str]:
    """Return direct-route and forbidden-import violations."""
    root = Path(package_root)
    violations = []
    for pair in pairs:
        try:
            path_a = _module_file(pair.module_a, root)
            path_b = _module_file(pair.module_b, root)
            imports_a = _imports(pair.module_a, path_a)
            imports_b = _imports(pair.module_b, path_b)
        except (OSError, SyntaxError) as exc:
            violations.append(f"independence pair unreadable: {exc}")
            continue
        if _matches(imports_a, pair.module_b):
            violations.append(f"{pair.module_a} imports paired route {pair.module_b}")
        if _matches(imports_b, pair.module_a):
            violations.append(f"{pair.module_b} imports paired route {pair.module_a}")
        for forbidden in pair.forbidden_shared:
            if _matches(imports_a, forbidden):
                violations.append(f"{pair.module_a} imports forbidden module {forbidden}")
            if _matches(imports_b, forbidden):
                violations.append(f"{pair.module_b} imports forbidden module {forbidden}")
    return violations


def _selftest() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        package = root / "tiny"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "a.py").write_text(
            "from . import b\nimport forbidden.bits\n", encoding="utf-8"
        )
        (package / "b.py").write_text("VALUE = 1\n", encoding="utf-8")
        pair = IndependencePair("tiny.a", "tiny.b", ["forbidden"], "selftest")
        problems = lint([pair], root)
        assert any("paired route" in problem for problem in problems), problems
        assert any("forbidden module" in problem for problem in problems), problems

        (package / "c.py").write_text("import math\n", encoding="utf-8")
        (package / "d.py").write_text("import json\n", encoding="utf-8")
        clean = IndependencePair("tiny.c", "tiny.d", ["forbidden"], "clean")
        assert lint([clean], root) == []
    print("INDEPENDENCE-SELFTEST OK")


if __name__ == "__main__":
    _selftest()
