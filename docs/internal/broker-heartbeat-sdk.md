# Heartbeat response and scheduling evidence — 2026-10-12

Scope: targeted ARM64 inspection only; no camera traffic, whole-app decompilation
or change to the production heartbeat. Existing Python sends A0 and accepts A1;
its `heartbeat_node` loop does not prove request-sequence correlation for a future
shared reader. Do not use same-kind transport receipt matching for that response.

## Pinned native evidence

Local `libiotvideomulti.so`, SHA-256
`b4d6f72168b666f80b08ebdd2eb210f57b5fd8199d8b375963f2568c5e96900a`:

- `gat_on_rcvfrm_HeartResp`, VA `0x1f8960`, size `0x7dc`: the ordinary response
  branch derives a frame pointer from the third argument plus `0x1b0`
  (`0x1f8bd8`). This is an SDK object-to-frame offset, not a UDP payload offset.
- At `0x1f8d20`–`0x1f8d64`, reads frame byte `0x21`, accepts only values 10–60
  inclusive, and stores a changed value at account-state offset `0x16f4`.
  Out-of-range values leave the previous state unchanged.
- `gat_send_heart_frm_35s` (`0x1f9688`) invokes the common send target only when
  that account field equals 35. `_40s` (`0x1f963c`) does so only for 40.
  `_50s` (`0x1f95dc`) skips 35/40 and calls it otherwise. These branch predicates
  are direct evidence; timer registration/cadence still needs separate inspection.
- The response also uses flags at frame `0x18` to parse optional fields starting
  at `0x28`. It is not a fixed 32-byte transport ACK. Optional fields are not
  implemented or exposed by this research step.

Do **not** infer that every accepted value 10–60 becomes a timer of that many
seconds. The inspected wrappers instead select 35, 40 or a fallback path; the
timer registrations and default state remain to be verified. This distinction
matters before choosing keepalive scheduling or retiring a session as dead.

## Outstanding correlation boundary

The inspected response handler receives an already-selected SDK callback/context.
It does not, by itself, establish which incoming frame/header field selected the
pending A0 request. Before implementing a shared heartbeat waiter, inspect that
dispatcher/request registration and confirm peer, session, sequence, flags and
length rules. Preserve unknown fields rather than inventing capability meanings.

Bounded inspection used at most 31.1 MiB without swap, under a 128-MiB cgroup cap
and half a CPU. Only the named handler and a 248-byte wrapper range were decoded.
No new persistent worker, timer interval, credential refresh or camera action was
enabled. The next RE targets are the common sender/registration and the callback
dispatcher; the existing finite read pilot remains the live-validated baseline.
