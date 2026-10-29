# Speaker volume on the existing native owner

The Yoosee driver now implements the existing speaker-volume control on the
native stream's authenticated account/socket. Normal generic camera permissions
and exact-unit capability/profile checks remain unchanged; the HTTP API does not
gain a raw vendor-command endpoint.

`shared_volume` reads the current raw value, skips a redundant change, submits
one typed D2 request, requires its correlated D3 application response, then reads
the same camera's raw value back. A transport ACK alone is not success. Only the
five existing 0/25/50/75/100 positions are accepted. The generic camera operation
lock spans this sequence. No alternate login or action replay occurs on failure.

Separate small job, one-slot inbox and pump modules run on the existing sole
account reader. Writes share the generation/sequence allocator with reads,
heartbeat, media and PTZ. Generation/peer/session/message correlation, cancellation,
bounded deadlines and confirmed motor-stop admission are preserved. A cancelled
claimed submission remains reserved until the reader retires it. D3's target
layout is not guessed: command correlation is combined with exact-device B8
readback. This control does not play any sound.

121 targeted tests passed under192MiB/no swap (100.9MiB peak); Mypy373 passed
under256MiB. These include encrypted D2/D3 through the actual account worker,
one send only, no login fallback, rejection/readback failure, stale responses,
generation/cancellation and unchanged-volume handling. Camera-backed acceptance
and deployment are pending: work was interrupted to prioritize the reported
camera3 black placeholder. The deployed UI fix is separate from these changes.
