# Native archive DTS collision —2026-10-29

The camera-backed storage pause/resume acceptance uncovered a real failure that
the short generated-media tests did not cover. Trial O kept live H264/AAC while
paused and the archive stopped growing, but resuming killed the shared decoder.
Q/R reproduced the resume failure. The MP4 parser expected `moof` and received
the terminal `mfra`, not an additional initialization or unknown media payload.
Do not accept/ignore that terminal box as a repair: the process had already ended.

A bounded operator-only stderr capture in trial T located the upstream cause:
the copied-video segment muxer rejected duplicate DTS (`previous4500,current4500`)
and exited under `-xerror`. This also ended the live output because both outputs
correctly share one FFmpeg child. The protocol failures in P/S were separate;
missing TCP retirement acknowledgements deferred rollback until the trial group
exited. None of these attempts established storage acceptance.

The repair is confined to the archive's video packet metadata. A `setts` bitstream
filter preserves the first DTS and advances only non-increasing DTS by one tick;
PTS stays unchanged unless the repaired DTS would exceed it. The MP4 video track
uses the same90000Hz timescale as native MPEG-TS so that this minimal adjustment
is not rounded back into a collision. The live clock, copied AAC, original HEVC
payloads, recording duration and UTC filenames are not replaced. There is no
fixed-FPS retiming, additional capture/encoder or removal of `-xerror`.
See the [FFmpeg setts contract](https://ffmpeg.org/ffmpeg-bitstream-filters.html#setts).

The real local regression generates five HEVC frames plus16kHz mono AAC, rewrites
one MPEG-TS video PTS to repeat the preceding timestamp and feeds the exact same
packets to old/new commands. The old command exits nonzero; the repaired command
completes, retains five HEVC packets/AAC and produces strictly increasing video
DTS. The collision moves by one90000Hz tick and subsequent valid timing remains
unchanged.75 focused tests passed under256MiB/no swap/50% CPU (118.6MiB peak);
Mypy363 files passed (50.6MiB peak), Ruff passed.

Fixed-label MP4 order diagnostics now distinguish expected/actual known box types
without logging arbitrary media bytes. The bounded trial also preserves the
consumer failure site after the source returns. Generated clips and private
operator artifacts remain ignored. Live acceptance of the repaired path is tracked
in the storage-policy document; default native boot remains disabled.
