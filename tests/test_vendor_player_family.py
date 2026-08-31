import pytest

from backend.app.drivers.yoosee.p2p.player_family import PlayerFamily, player_family


@pytest.mark.parametrize("device_id", (1, 0xFFFFFFFF, "4294967295"))
def test_unsigned_32_bit_ids_use_legacy_player(device_id: str | int) -> None:
    assert player_family(device_id) is PlayerFamily.LEGACY_GW


@pytest.mark.parametrize("device_id", (0x100000000, "7443576841", 7443576841))
def test_larger_ids_use_iotvideo_player(device_id: str | int) -> None:
    assert player_family(device_id) is PlayerFamily.IOTVIDEO


@pytest.mark.parametrize("device_id", (0, -1, "camera-three", None))
def test_invalid_device_ids_fail_closed(device_id: object) -> None:
    with pytest.raises(ValueError, match="device ID"):
        player_family(device_id)  # type: ignore[arg-type]
