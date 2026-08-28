"""Executable dependency rules for the driver-oriented application structure."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
APP = ROOT / "backend" / "app"


def _python_files(directory: Path):
    return sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    return imported


def test_generic_http_and_services_do_not_import_camera_family_implementations():
    violations = []
    for layer in (APP / "api", APP / "services"):
        for path in _python_files(layer):
            for module in _imports(path):
                if "drivers.yoosee" in module or "vendor_p2p" in module:
                    violations.append(f"{path.relative_to(ROOT)} -> {module}")

    assert violations == []


def test_generic_provisioning_package_contains_only_shared_wifi_selection():
    modules = {path.name for path in (APP / "provisioning").glob("*.py")}

    assert modules == {"__init__.py", "wifi.py"}


def test_each_packaged_driver_exposes_one_package_entrypoint():
    packaged = [
        path
        for path in (APP / "drivers").iterdir()
        if path.is_dir() and not path.name.startswith("__")
    ]

    assert packaged
    for package in packaged:
        assert (package / "__init__.py").is_file(), package.name
