"""Fixed local receive classifications, never raw network/error text."""

PROTOCOL_CODES = {
    'KCP assembly deadline exceeded': 'kcp_assembly_timeout',
    'invalid KCP receive segment': 'kcp_invalid_segment',
    'conflicting KCP duplicate': 'kcp_conflicting_duplicate',
    'KCP receive budget exceeded': 'kcp_receive_budget',
    'invalid KCP fragment chain': 'kcp_fragment_chain',
    'TCP AV session deadline exceeded': 'tcp_session_timeout',
    'AV handshake readiness deadline exceeded': 'av_handshake_timeout',
    'AV control receipt deadline exceeded': 'av_control_timeout',
    'receive initialization deadline exceeded': 'media_initialization_timeout',
    'receive progress deadline exceeded': 'media_idle_timeout',
    'cooperative TCP output deadline exceeded': 'tcp_io_timeout',
    'cooperative TCP media deadline exceeded': 'tcp_io_timeout',
    'cooperative TCP step deadline exceeded': 'tcp_io_timeout',
    'TCP media read exceeded deadline': 'tcp_io_timeout',
    'TCP media EOF before CLOSE receipt': 'tcp_eof',
}

TRANSIENT_CODES = frozenset({
    'kcp_assembly_timeout', 'tcp_session_timeout', 'av_handshake_timeout',
    'av_control_timeout', 'media_initialization_timeout', 'media_idle_timeout',
    'tcp_io_timeout', 'tcp_eof',
})
