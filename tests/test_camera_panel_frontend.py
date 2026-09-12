"""Exercise panel DOM behavior with bounded Node memory, never camera media/browser."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_camera_panel_dom_contracts():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the dependency-free DOM harness")
    result = subprocess.run(
        [node, "--max-old-space-size=64", str(Path(__file__).parent / "frontend/camera-controls.cjs")],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
