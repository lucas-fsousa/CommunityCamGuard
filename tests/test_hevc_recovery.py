import pytest

from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from backend.app.media.hevc_access import MAX_ACCESS_UNIT, inspect_access_unit
from backend.app.media.hevc_recovery import HevcRecovery
from scripts.validate_native_recovery import RecoveryAudit
from tests.test_captured_media import header


def nal(kind, body=b"\x80", *, start=b"\x00\x00\x00\x01"):
    return start + bytes((kind << 1, 1)) + body


def idr(config=b"\x80", kind=19):
    return b"".join(nal(k, config) for k in (32, 33, 34)) + nal(kind)


def test_recovery_never_uses_old_configuration_after_gap():
    gate = HevcRecovery()
    assert gate.inspect(idr(), 1).restart_decoder
    assert gate.inspect(nal(1), 2).emit
    gate.discontinuity()
    assert not gate.inspect(nal(1), 3).emit
    assert not gate.inspect(nal(19), 4).emit
    result = gate.inspect(idr(), 5)
    assert result.emit and result.restart_decoder and result.epoch == 2


def test_configuration_change_restarts_decoder_but_repeat_does_not():
    gate = HevcRecovery()
    assert gate.inspect(idr(), 1).restart_decoder
    assert not gate.inspect(idr(), 2).restart_decoder
    decision = gate.inspect(idr(b"\x81"), 3)
    assert decision.restart_decoder and decision.epoch == 2


@pytest.mark.parametrize("frame", [nal(1), nal(19), nal(32) + nal(19),
                                   idr(kind=21), nal(19) + b"".join(nal(k) for k in (32, 33, 34))])
def test_no_restart_without_complete_inband_configured_idr(frame):
    gate = HevcRecovery()
    assert not gate.inspect(frame, 1).emit
    assert gate.inspect(idr(), 2).restart_decoder


def test_timestamp_regression_and_corruption_force_reacquisition():
    gate = HevcRecovery()
    gate.inspect(idr(), 100)
    assert not gate.inspect(nal(1), 99).emit
    assert gate.inspect(idr(), 101).restart_decoder
    assert not gate.inspect(b"garbage", 102).emit
    assert not gate.inspect(nal(1), 103).emit
    assert gate.inspect(idr(), 104).restart_decoder


@pytest.mark.parametrize("frame", [b"", b"bad" + nal(1), bytes(MAX_ACCESS_UNIT + 1),
                                   nal(1) * 129, b"\x00\x00\x01\x02\x00\x80",
                                   b"\x00\x00\x01\x02\x09\x80", nal(1) + nal(1),
                                   nal(1, b"\x01")])
def test_malformed_or_unbounded_access_units_are_rejected(frame):
    with pytest.raises(ValueError):
        inspect_access_unit(frame)


def test_three_byte_start_and_multiple_slices_of_one_picture():
    units = inspect_access_unit(nal(1, start=b"\x00\x00\x01") + nal(1, b"\x01"))
    assert len(units) == 2 and units[0].first_slice and not units[1].first_slice


def test_offline_loss_audit_collects_only_new_decoder_epoch():
    audit = RecoveryAudit("flow5", 2, 1, max_frames=2)
    audit.consume("flow5", V1Record(encoding=unpack_v1_encoding_header(header())))
    for index, payload in enumerate((idr(), nal(1), nal(1), idr(), nal(1), nal(1)), 1):
        audit.consume("flow5", V1Record(video=payload, video_timestamp=index * 100))
    assert audit.sample.frames == 2
    assert audit.resumed_at == 4 and audit.discarded == 1 and audit.resume_delta == 200


@pytest.mark.parametrize("args", [(0, 1), (1, 0), (10001, 1)])
def test_invalid_scenario_budgets(args):
    with pytest.raises(ValueError):
        RecoveryAudit("flow5", *args)
