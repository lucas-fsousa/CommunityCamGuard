import errno
from unittest.mock import Mock

import pytest

from backend.app.drivers.yoosee.p2p.udp_media_send import send_media, socket_error_label


@pytest.mark.parametrize('error,outcome',[
    (TimeoutError('private message'),'timeout'),
    (BlockingIOError(errno.EAGAIN,'private message'),'would_block'),
    (OSError(errno.ENOBUFS,'private message'),'no_buffers'),
])
def test_local_media_congestion_drops_one_datagram_without_queue_or_retry(error,outcome):
    sock=Mock()
    sock.sendto.side_effect=error
    assert send_media(sock,b'wire',('192.0.2.1',1234))==outcome
    sock.sendto.assert_called_once()
    sock.close.assert_not_called()


@pytest.mark.parametrize('number',[errno.EBADF,errno.ENETUNREACH,errno.EINVAL])
def test_noncongestion_socket_errors_still_propagate(number):
    sock=Mock();sock.sendto.side_effect=OSError(number,'private message')
    with pytest.raises(OSError) as result:
        send_media(sock,b'wire',('192.0.2.1',1234))
    assert result.value.errno==number


def test_partial_datagrams_are_not_silently_treated_as_congestion():
    sock=Mock();sock.sendto.return_value=3
    with pytest.raises(OSError,match='partial'):
        send_media(sock,b'wire',('192.0.2.1',1234))
    sock.sendto.return_value=4
    assert send_media(sock,b'wire',('192.0.2.1',1234))=='sent'


def test_error_diagnostics_do_not_include_exception_text_or_arbitrary_types():
    assert socket_error_label(TimeoutError('secret'))==('TimeoutError',None)
    assert socket_error_label(OSError(errno.ENETUNREACH,'secret'))==('OSError',errno.ENETUNREACH)
    assert socket_error_label(ValueError('secret'))==('unknown',None)
