from pathlib import Path


def test_runtime_dependencies_are_cached_before_application_sources() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()

    dependency_install = dockerfile.index("requirements-runtime.txt")
    backend_copy = dockerfile.index("COPY backend ./backend")
    project_install = dockerfile.index("--no-deps --no-build-isolation .")

    assert dependency_install < backend_copy < project_install
    assert 'data["project"]["dependencies"]' in dockerfile
    assert 'data["build-system"]["requires"]' in dockerfile
