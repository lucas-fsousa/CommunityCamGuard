import errno
import socket
from unittest.mock import Mock, call

import pytest

from backend.app.drivers.yoosee.p2p import udp_receive_buffer as buffer


def test_standard_per_socket_headroom_needs_no_force_option():
    sock=Mock();sock.getsockopt.return_value=2*buffer.MIN_REQUEST
    result=buffer.configure_receive_buffer(sock,streams=3)
    assert result==buffer.ReceiveBuffer(buffer.MIN_REQUEST,2*buffer.MIN_REQUEST,False)
    sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET,socket.SO_RCVBUF,buffer.MIN_REQUEST)
    sock.close.assert_not_called()


def test_existing_linux_capability_can_bypass_only_this_sockets_clamp(monkeypatch):
    sock=Mock();sock.getsockopt.side_effect=(425984,2*buffer.MIN_REQUEST)
    monkeypatch.setattr(buffer,'_force_option',lambda:33)
    result=buffer.configure_receive_buffer(sock,streams=3)
    assert result.forced and result.effective==2*buffer.MIN_REQUEST
    assert sock.setsockopt.call_args_list==[
        call(socket.SOL_SOCKET,socket.SO_RCVBUF,buffer.MIN_REQUEST),
        call(socket.SOL_SOCKET,33,buffer.MIN_REQUEST)]


@pytest.mark.parametrize('force',[None,33])
def test_missing_permission_or_platform_retains_standard_socket_without_failure(monkeypatch,force,caplog):
    sock=Mock();sock.getsockopt.return_value=425984
    sock.setsockopt.side_effect=[None,PermissionError(errno.EPERM,'private details')]
    monkeypatch.setattr(buffer,'_force_option',lambda:force)
    result=buffer.configure_receive_buffer(sock,streams=3)
    assert result.effective==425984 and not result.forced
    assert 'effective=425984' in caplog.text and 'private' not in caplog.text
    sock.close.assert_not_called()


def test_buffer_memory_is_bounded_without_limiting_number_of_admitted_streams():
    sock=Mock();sock.getsockopt.return_value=2*buffer.MAX_REQUEST
    result=buffer.configure_receive_buffer(sock,streams=1000)
    assert result.requested==buffer.MAX_REQUEST


@pytest.mark.parametrize('streams',[0,True])
def test_bad_counts_do_not_touch_socket(streams):
    sock=Mock()
    with pytest.raises(ValueError):buffer.configure_receive_buffer(sock,streams=streams)
    sock.setsockopt.assert_not_called()
