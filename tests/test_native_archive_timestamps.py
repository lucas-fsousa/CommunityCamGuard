"""Real bounded muxer regression: repeated native video DTS must not end live A/V."""

import json
import shutil
import subprocess
from itertools import pairwise

import pytest

from backend.app.media.decoder_command import decoder_command


def duplicate_video_timestamp(data):
    data=bytearray(data)
    first=None
    found=0
    for start in range(0,len(data),188):
        packet=data[start:start+188]
        assert len(packet)==188 and packet[0]==0x47
        if ((packet[1]&31)<<8|packet[2])!=257 or not packet[1]&64:
            continue
        control=(packet[3]>>4)&3
        if not control&1: continue
        offset=4+(1+packet[4] if control&2 else 0)
        if packet[offset:offset+4]!=b'\0\0\1\xe0': continue
        assert packet[offset+7]&0xC0==0x80  # no B frames in this fixture
        position=start+offset+9
        found+=1
        if first is None:
            first=bytes(data[position:position+5])
        elif found==2:
            data[position:position+5]=first
    assert found==5
    return bytes(data)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('prlimit'),reason='local media tools required')
def test_original_archive_repairs_duplicate_dts_without_disabling_xerror(tmp_path):
    fixture=subprocess.run(['ffmpeg','-v','error','-nostdin','-filter_threads','1',
        '-f','lavfi','-i','testsrc=size=64x64:rate=5',
        '-f','lavfi','-i','anullsrc=r=16000:cl=mono','-t','1',
        '-c:v','libx265','-threads','1','-pix_fmt','yuv420p',
        '-x265-params','pools=none:frame-threads=1:bframes=0:log-level=error',
        '-c:a','aac','-b:a','32k','-mpegts_start_pid','257','-f','mpegts','pipe:1'],
        capture_output=True,check=True,timeout=15).stdout
    assert len(fixture)<256*1024
    broken=duplicate_video_timestamp(fixture)
    archive=tmp_path/'repaired.mp4'
    command=decoder_command(output=True,live_timing=False,frame_rate=5,
        transport='mpegts',audio=True,archive=archive)
    # Prove the old pipeline fails with exactly the same packets, not a mock.
    old=command.copy()
    position=old.index('-bsf:v'); del old[position:position+2]
    old[old.index('-segment_format_options')+1]=old[old.index('-segment_format_options')+1].split(':')[0]
    old[-1]=str(tmp_path/'old.mp4')
    failed=subprocess.run(old,input=broken,capture_output=True,timeout=15)
    assert failed.returncode!=0
    result=subprocess.run(command,input=broken,capture_output=True,timeout=15,check=True)
    assert '-xerror' in command and b'ftyp' in result.stdout[:32]
    probe=subprocess.run(['ffprobe','-v','error','-show_packets','-show_streams',
        '-show_entries','packet=stream_index,dts:stream=codec_name,time_base,sample_rate,channels',
        '-of','json',str(archive)],capture_output=True,check=True,timeout=10)
    info=json.loads(probe.stdout)
    assert info['streams'][0]['codec_name']=='hevc'
    assert info['streams'][0]['time_base']=='1/90000'
    assert info['streams'][1]['codec_name']=='aac'
    assert info['streams'][1]['sample_rate']=='16000' and info['streams'][1]['channels']==1
    dts=[int(p['dts']) for p in info['packets'] if p['stream_index']==0]
    assert len(dts)==5 and all(a<b for a,b in pairwise(dts))
    assert dts[1]==dts[0]+1  # the collision moves by just one90kHz tick
    assert dts[2]-dts[0]==36000  # subsequent valid timing is not flattened to a fixed FPS
