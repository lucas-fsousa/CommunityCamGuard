"""Executable dependency rules for the driver-oriented application structure."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
APP = ROOT / "backend" / "app"
YOOSEE_P2P = APP / "drivers" / "yoosee" / "p2p"


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


def test_yoosee_protocol_layers_do_not_depend_on_compatibility_client():
    layered_modules = [
        *YOOSEE_P2P.glob("*_protocol.py"),
        *YOOSEE_P2P.glob("*_session.py"),
        YOOSEE_P2P / "auth.py",
        YOOSEE_P2P / "contracts.py",
        YOOSEE_P2P / "crypto.py",
        YOOSEE_P2P / "session_io.py",
        YOOSEE_P2P / "wire.py",
    ]
    violations = []
    for path in layered_modules:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = {alias.name for alias in node.names}
                if node.module == "client" or (node.module is None and "client" in names):
                    violations.append(path.name)

    assert violations == []


def test_yoosee_feature_modules_do_not_depend_on_compatibility_client():
    feature_modules = ("orientation.py", "rtsp_setup.py", "white_light.py")
    violations = []
    for name in feature_modules:
        path = YOOSEE_P2P / name
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            names = {alias.name for alias in node.names}
            if node.module == "client" or (node.module is None and "client" in names):
                violations.append(name)

    assert violations == []


def test_yoosee_client_defines_only_public_compatibility_operations():
    client = YOOSEE_P2P / "client.py"
    tree = ast.parse(client.read_text(), filename=str(client))
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert functions == {
        "probe_account_inventory",
        "probe_camera_route",
        "read_camera_property",
    }
