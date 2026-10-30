"""Six synthetic noisy HD frames; no camera, microphone or network."""

import shutil
import subprocess

import pytest

from backend.app.media.decoder_command import decoder_command
from backend.app.media.fragmented_mp4 import MAX_BYTES, FragmentedMp4, Mp4SizeError


def parse(data):
    output = FragmentedMp4();chunks=[]
    for start in range(0,len(data),16384):
        output.feed(data[start:start+16384])
        while (chunk:=output.take()) is not None:chunks.append(chunk)
    return chunks


def test_noisy_hd_fragment_fits_existing_budget_without_resizing_or_dropping_frames():
    if shutil.which('ffmpeg') is None:pytest.skip('FFmpeg fixture tool unavailable')
    fixture = subprocess.run(['ffmpeg','-v','error','-filter_threads','1','-f','lavfi',
        '-i','testsrc2=size=1920x1080:rate=2,noise=alls=100:allf=t+u','-frames:v','6',
        '-an','-c:v','libx264','-preset','ultrafast','-qp','0','-threads','1',
        '-f','mpegts','pipe:1'],capture_output=True,timeout=30,check=True).stdout
    command = decoder_command(output=True,live_timing=True,frame_rate=2,transport='mpegts')
    # The production consumer retires before EOF. Suppress only the finite
    # fixture's mfra trailer so this check isolates complete live fragments.
    command[command.index('-movflags')+1] += '+skip_trailer'
    previous = list(command)
    for option in ('-force_key_frames','-maxrate','-bufsize'):
        index=previous.index(option);del previous[index:index+2]
    old = subprocess.run(previous,input=fixture,capture_output=True,timeout=30,check=True)
    with pytest.raises(Mp4SizeError):parse(old.stdout)
    new = subprocess.run(command,input=fixture,capture_output=True,timeout=30,check=True)
    chunks=parse(new.stdout)
    assert chunks[0].initialization and len(chunks)>=4
    assert max(len(chunk.data) for chunk in chunks)<MAX_BYTES
    assert b'frame=6\n' in new.stderr.replace(b' ',b'')
    assert not any(option in command for option in ('-s','-vf','-r'))


def test_live_budget_does_not_apply_to_original_archive(tmp_path):
    command=decoder_command(output=True,live_timing=False,frame_rate=15,
        transport='mpegts',audio=True,archive=tmp_path/'%Y%m%d_%H%M%S.mp4')
    boundary=command.index('pipe:1')
    assert '-maxrate' in command[:boundary] and '-force_key_frames' in command[:boundary]
    assert '-maxrate' not in command[boundary:] and '-force_key_frames' not in command[boundary:]
    assert command[command.index('-c')+1] == 'copy'
