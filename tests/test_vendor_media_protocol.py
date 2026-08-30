import pytest

from backend.app.drivers.yoosee.p2p.media_protocol import (
    KCP_ACK,
    KCP_PUSH,
    build_av_control,
    build_av_init,
    build_kcp_ack,
    build_kcp_push,
    build_media_meter_ack,
    build_media_meter_request,
    build_mtp_frame,
    mtp_frame_length,
    parse_kcp_segments,
    parse_media_meter,
    verify_mtp_frame,
)
from backend.app.drivers.yoosee.p2p.stream_protocol import (
    build_v1_audio_packet,
    decrypt_command_tlv,
    decrypt_media_tlv,
    encrypt_command_tlv,
    encrypt_media_tlv,
    pack_legacy_capture_header,
    pack_legacy_talk_control,
    unpack_v1_encoding_header,
)

GOLDEN_METER = bytes.fromhex(
    "c09006095a0a0001440035ddfb00080000006304cb8801000080"
    "6464f3b901000000010000009b69660f000000000000000000000000"
    "0400000048000000000000000000000002010000654f71ef"
)
GOLDEN_KCP = bytes.fromhex(
    "c010020d929b35ddfb8051000001fd69660f00000000000000004c000000"
    "03024c00654f71ef010000000000000001000000010000000100000000000000"
    "0000000000000000000000000000001200000000000000000000000000000000"
    "090000000000000000000000"
)


def test_mtp_and_meter_builders_reproduce_capture() -> None:
    assert mtp_frame_length(GOLDEN_METER) == len(GOLDEN_METER)
    assert verify_mtp_frame(GOLDEN_METER)
    assert build_mtp_frame(0x90, GOLDEN_METER[6:]) == GOLDEN_METER
    assert (
        build_media_meter_request(
            0x8000000188CB0463,
            0x00000001B9F36464,
            0x00FBDD35,
            0xEF714F65,
            sequence=1,
            timestamp=0x0F66699B,
        )
        == GOLDEN_METER
    )


def test_media_meter_ack_swaps_route() -> None:
    camera_request = bytes.fromhex(
        "c0900209174a0001440035ddfb00000000006464f3b901000000"
        "6304cb8801000080010000003b200200000000000000000000000000"
        "0400000044000000000000000000000000010000"
    )
    parsed = parse_media_meter(camera_request)
    assert parsed is not None and parsed.kind == 1
    ack = parse_media_meter(build_media_meter_ack(camera_request))
    assert ack is not None and ack.kind == 2
    assert ack.source_id == parsed.destination_id
    assert ack.destination_id == parsed.source_id


def test_kcp_and_av_golden_frames() -> None:
    segments = parse_kcp_segments(GOLDEN_KCP)
    assert len(segments) == 1
    assert segments[0].command == KCP_PUSH
    assert segments[0].body[:4] == bytes.fromhex("03024c00")
    assert (
        build_kcp_push(
            0x80FBDD35,
            0,
            build_av_init(0xEF714F65),
            timestamp=0x0F6669FD,
        )
        == GOLDEN_KCP
    )
    ack = parse_kcp_segments(build_kcp_ack(7, 3, 11, unacknowledged=4))[0]
    assert (ack.command, ack.sequence, ack.unacknowledged, ack.body) == (KCP_ACK, 3, 4, b"")


def test_av_start_body_matches_capture() -> None:
    assert build_av_control(0xEF714F65, 6) == bytes.fromhex(
        "03004c00654f71ef060000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000"
        "090000000000000000000000"
    )


def test_stream_tlv_round_trips() -> None:
    cookie = bytes.fromhex("aa17cd6974f58b1e")
    command = bytes.fromhex("8200020009000000003200007856341201")
    encoded_command = encrypt_command_tlv(command, cookie)
    assert encoded_command[:4] == bytes.fromhex("02021500")
    assert decrypt_command_tlv(encoded_command, cookie) == command
    media = bytes.fromhex("ffffff880800010000000000000000000000000040e2010000000000")
    encoded_media = encrypt_media_tlv(media, cookie)
    assert encoded_media[:4] == bytes((4, 2, len(encoded_media), 0))
    assert decrypt_media_tlv(encoded_media, cookie) == media


def test_legacy_talk_records_match_native_sender() -> None:
    assert pack_legacy_capture_header() == bytes.fromhex(
        "ff ff ff 88 00 01 05 21 01 14 f0 3c "
        "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert pack_legacy_talk_control(True) == bytes.fromhex(
        "ff ff ff 88 00 02 05 01 "
        "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert pack_legacy_talk_control(False)[7] == 0
    with pytest.raises(ValueError, match="frame rate"):
        pack_legacy_capture_header(0)


def test_camera_encoding_header_and_audio_packet() -> None:
    header = unpack_v1_encoding_header(
        bytes.fromhex(
            "ff ff ff 88 08 01 00 00 04 02 00 01 80 3e 00 00 "
            "00 04 05 0f 80 02 00 00 68 01 00 00"
        )
    )
    assert (header.audio_codec, header.audio_sample_rate) == (4, 16_000)
    assert (header.video_codec, header.video_width, header.video_height) == (5, 640, 360)
    audio = bytes.fromhex("fff960401f5ffc0110359c564841")
    packet = build_v1_audio_packet((audio,), 139_812_000, record_marker=8)
    assert packet[:30] == bytes.fromhex(
        "ffffff8808000100000000000000000000000000a05c5508000000000e00"
    )
    assert packet[30:] == audio


def test_malformed_frames_fail_closed() -> None:
    damaged = bytearray(GOLDEN_METER)
    damaged[15] ^= 1
    assert not verify_mtp_frame(bytes(damaged))
    assert parse_media_meter(bytes(damaged)) is None
    with pytest.raises(ValueError, match="valid c0/10"):
        parse_kcp_segments(GOLDEN_METER)
    with pytest.raises(ValueError, match="eight bytes"):
        encrypt_media_tlv(b"payload", b"short")
