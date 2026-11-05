# Native video/audio input fairness

The native source previously called `VideoConsumer.step()` before checking whether
AAC could be written. That step pumps the child and immediately offers another
queued video AU. Under a sustained video backlog, every newly free stdin slot
could therefore be claimed by video. AAC got a chance only when no video remained
or a decoder implementation left stdin empty after an offer. This is starvation,
not a requirement to lower resolution or create a second FFmpeg/camera connection.

`VideoConsumer.step(accept_frame=False)` now pumps decoder output/progress/input
without taking the next video frame; its return value identifies an actual video
offer. After each video AU, the source reserves the next free slot for at most one
AAC AU before admitting another video. With no video ready, audio can continue;
with no audio ready, video continues on the following iteration. Partial writes
still complete before a different AU is offered. Epoch retirement, pipe/queue
limits, source clocks, AAC copy and video resolution are unchanged.

A synthetic source-loop regression preloads both tracks, simulates pipe writes
that need two pump iterations, and requires video/audio/video/audio ordering on
one decoder. It sends no camera traffic and starts no FFmpeg. Pump-only consumer
coverage also checks that the queued video remains untouched.

Real `muxer_setup`/234 failures occurred after failed KCP attempts, often during
subsequent startup. Starvation can prevent the mandatory audio track from being
available to the MP4 muxer, but this has not yet been established as the sole
cause of those real failures. `muxer_queue` now distinguishes the fixed
"too many packets buffered" signature from other muxer setup failures without
retaining raw stderr. Sustained deployment observation is still required.
