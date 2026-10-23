"""Build artifact integrity/architecture checks; never download or execute here."""

import hashlib
import io
from unittest.mock import Mock

import pytest

from build_tools import install_go2rtc as installer

PAYLOAD = b"\x7fELFsynthetic-go2rtc-artifact"


@pytest.fixture
def artifact(monkeypatch):
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    monkeypatch.setattr(installer, "ARTIFACTS", {
        "amd64": ("go2rtc_linux_amd64", digest), "arm64": ("go2rtc_linux_arm64", digest),
    })
    return digest


@pytest.mark.parametrize("value,expected", [("amd64", "amd64"), ("x86_64", "amd64"),
                                           ("arm64", "arm64"), ("aarch64", "arm64")])
def test_correct_architecture_asset_is_verified_before_publication(tmp_path, artifact, value, expected):
    output = tmp_path / "bin/go2rtc"
    opener = Mock(side_effect=lambda *_args, **_kwargs: io.BytesIO(PAYLOAD))
    assert installer.install(value, output, opener=opener) == expected
    assert opener.call_args.args[0].endswith(f"go2rtc_linux_{expected}")
    assert output.read_bytes() == PAYLOAD and output.stat().st_mode & 0o777 == 0o755
    opener.reset_mock()
    installer.install(value, output, opener=opener)
    opener.assert_not_called()  # matching output is reused without network I/O


@pytest.mark.parametrize("failure", ["digest", "oversize", "disconnect", "deadline"])
def test_failed_transfer_never_publishes_or_leaves_partial_files(tmp_path, artifact, monkeypatch, failure):
    output = tmp_path / "bin/go2rtc"
    if failure == "oversize":
        monkeypatch.setattr(installer, "MAX_BYTES", 4)
    clock = iter((0.0, 61.0)) if failure == "deadline" else None
    payload = b"wrong" if failure == "digest" else PAYLOAD

    def opener(*_args, **_kwargs):
        if failure == "disconnect":
            raise OSError("synthetic network failure")
        return io.BytesIO(payload)

    with pytest.raises((ValueError, OSError)):
        installer.install("amd64", output, opener=opener,
                          clock=lambda: next(clock) if clock is not None else 0.0)
    assert not output.exists() and not list(output.parent.iterdir())


def test_unknown_arch_and_existing_custom_binary_are_not_modified(tmp_path, artifact):
    output = tmp_path / "go2rtc"
    output.write_bytes(b"user-custom-binary")
    opener = Mock(side_effect=AssertionError("unexpected download"))
    with pytest.raises(ValueError, match="new path"):
        installer.install("amd64", output, opener=opener)
    with pytest.raises(ValueError, match="supports"):
        installer.install("mips", output, opener=opener)
    assert output.read_bytes() == b"user-custom-binary"
    opener.assert_not_called()


def test_matching_nonexecutable_artifact_gets_owner_execute_without_redownload(tmp_path, artifact):
    output = tmp_path / "go2rtc"
    output.write_bytes(PAYLOAD)
    output.chmod(0o600)
    opener = Mock(side_effect=AssertionError("unexpected download"))
    installer.install("amd64", output, opener=opener)
    assert output.read_bytes() == PAYLOAD and output.stat().st_mode & 0o100
    opener.assert_not_called()


def test_concurrent_file_creation_cannot_be_overwritten(tmp_path, artifact):
    output = tmp_path / "go2rtc"

    def opener(*_args, **_kwargs):
        output.write_bytes(b"concurrent-custom-binary")
        return io.BytesIO(PAYLOAD)

    with pytest.raises(ValueError, match="changed"):
        installer.install("amd64", output, opener=opener)
    assert output.read_bytes() == b"concurrent-custom-binary"
    assert list(tmp_path.iterdir()) == [output]


def test_output_symlink_never_changes_its_target(tmp_path, artifact):
    target = tmp_path / "original"
    target.write_bytes(b"user-data")
    output = tmp_path / "go2rtc"
    output.symlink_to(target)
    with pytest.raises(ValueError):
        installer.install("amd64", output)
    assert target.read_bytes() == b"user-data" and output.is_symlink()
