import pytest

from backend.app.media.decoder_failure import classify, timeout_category
from backend.app.media.hevc_decoder import DecoderExitedError


@pytest.mark.parametrize('message,category', [
    (b'No space left on device /private/path', 'storage_full'),
    (b'Cannot allocate memory', 'resource_limit'),
    (b'Permission denied', 'output_path'),
    (b'Application provided invalid, non monotonically increasing dts', 'timestamp_order'),
    (b'Error parsing ADTS frame header', 'audio_bitstream'),
    (b'PPS id out of range', 'input_headers'),
    (b'Invalid data found when processing input', 'input_bitstream'),
    (b'[hevc @ 0x123] The cu_qp_delta is outside the valid range', 'input_bitstream'),
    (b'[mpegts @ 0x123] PES packet size mismatch', 'input_transport'),
    (b'Could not write header for output file: Invalid argument', 'muxer_setup'),
    (b'Too many packets buffered for output stream 0:0', 'muxer_queue'),
    (b'Could not find codec parameters for stream', 'input_headers'),
    (b'Error while opening encoder', 'encoder_setup'),
    (b'Error writing trailer of pipe:1: Broken pipe', 'output_write'),
    (b'frame=12\nprogress=continue\n', 'unknown'),
])
def test_only_fixed_categories_are_retained(message, category):
    assert classify(message) == category


def test_classifier_is_bounded_and_exit_metadata_cannot_hold_raw_error_text():
    assert classify(b'Permission denied'+bytes(16384)) == 'unknown'
    error = DecoderExitedError(returncode=99999, category='SECRET')
    assert error.returncode is None and error.category == 'unknown'
    assert 'SECRET' not in str(error)


@pytest.mark.parametrize('message,category', [
    ('decoder input stalled', 'stdin_timeout'),
    ('continuous decoder made no frame progress', 'progress_timeout'),
    ('PRIVATE arbitrary timeout', 'unknown'),
])
def test_timeout_stage_uses_only_fixed_local_signatures(message, category):
    assert timeout_category(TimeoutError(message)) == category
