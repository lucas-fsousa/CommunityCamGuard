import pytest

from backend.app.media.decoder_failure import classify
from backend.app.media.hevc_decoder import DecoderExitedError


@pytest.mark.parametrize('message,category', [
    (b'No space left on device /private/path', 'storage_full'),
    (b'Cannot allocate memory', 'resource_limit'),
    (b'Permission denied', 'output_path'),
    (b'Application provided invalid, non monotonically increasing dts', 'timestamp_order'),
    (b'Error parsing ADTS frame header', 'audio_bitstream'),
    (b'PPS id out of range', 'input_headers'),
    (b'Invalid data found when processing input', 'input_bitstream'),
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
