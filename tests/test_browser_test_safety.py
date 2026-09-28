"""The opt-in browser runner must reject missing/oversized caps before spawning."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("browser_safety", Path(__file__).parents[1] / "scripts/browser_test_safety.py")
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)


@pytest.mark.parametrize("override", [None, ("memory.max", "max"), ("memory.max", str(513 * 1024 * 1024)),
    ("memory.swap.max", "1"), ("pids.max", "129"), ("cpu.max", "max 100000"), ("cpu.max", "150000 100000")])
def test_limits_are_explicit_and_bounded(tmp_path, override):
    for name, value in {"memory.max": str(512 * 1024 * 1024), "memory.swap.max": "0",
                        "pids.max": "128", "cpu.max": "75000 100000"}.items():
        (tmp_path / name).write_text(value)
    if override:
        (tmp_path / override[0]).write_text(override[1])
        with pytest.raises(RuntimeError, match="requires explicit cgroup caps"):
            safety.validate_limits(tmp_path)
    else:
        safety.validate_limits(tmp_path)


def test_missing_cgroup_files_deny(tmp_path):
    with pytest.raises(RuntimeError):
        safety.validate_limits(tmp_path)
