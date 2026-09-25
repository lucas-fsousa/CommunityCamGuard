# Revocable recording delivery — 2026-09-25

**Implemented/tested in source, not deployed. Public temporary login is still disabled.**

`recording/delivery.py` wraps Starlette `FileResponse`; archive routes keep the
existing root/path checks, codec/cache selection and friendly download filename.
Range/206 (including multipart/suffix ranges), If-Range, Content-Length and 416 are
handled by the existing response implementation, not a new custom range parser.
No whole-file buffering or extra ffmpeg job is added.

## Authority and cancellation

All deliveries recheck their cookie before response work; invalid sessions get
401/no-store. Primary/legacy responses then follow ordinary FileResponse behavior
without a recurring key-store query. Their already-authorized transfers do not gain
revocation semantics: primary sessions are still stateless.

For temporary sessions, the response owns both file delivery and the existing
periodic key-validity watcher. Its checks run once per second, with a five-second
verification timeout. Invalidity/error/timeout cancels the delivery task even when
the client is slow and ASGI send is blocked. Both tasks are reaped on completion,
client cancellation or invalidation. Chunking remains bounded by FileResponse's
64 KiB chunks in the tested version. No verifier query is done for every chunk.

The `http.response.pathsend` extension is removed from a copied temporary-request
scope, preventing zero-copy handoff from bypassing cancellation ownership. Primary
delivery retains that optimization. Temporary file/range/error responses carry
`Cache-Control: private, no-store`.

Before headers, invalidity can return 401. After 200/206 headers are sent, the code
raises a content-free ConnectionAbortedError so the ASGI server aborts the incomplete
transfer; it does not emit a false successful tail or attempt to change status.
The server may log an aborted-response error. New Range/retry requests are denied by
normal authorization after revocation. Clients should treat partial downloads as
incomplete. No code can recall bytes already delivered or buffered downstream.

This is periodic invalidation, not zero-latency revocation: allow the next check,
verification time and event-loop scheduling; blocked verification adds its timeout.
Very short transfers may finish before the next check. Proxies/browsers may already
hold bytes. Production proxy/disconnect behavior remains an end-to-end validation item.

## Staged permission change

Temporary sessions may now use GET `/api/recordings/file` and `/api/recordings/download`
plus POST `/api/recordings/prepare`, alongside existing archive metadata reads.
Preparation keeps the shared bounded conversion budget; revocation does not cancel
a conversion already accepted for other viewers/cache reuse. Unknown operations
still deny by default, key/settings management stays primary-only, all temporary
intercom sockets remain denied and `/api/login` still does not issue temporary sessions.
Live media now has a separate [restricted MSE bridge](temporary-live-media.md).

## Evidence and next steps

84 focused tests passed across guarded delivery, existing HTTP ranges, staged
sessions, channel cleanup and route contracts. Tests use a synthetic 192 KiB file,
an isolated database and fake ASGI backpressure. Coverage includes revoke/expiry/
store failure mid-transfer, task cleanup, client cancellation, pathsend containment,
primary independence, original vs cached output, single/multipart/suffix ranges,
If-Range/416, filenames, preparation permission and traversal/auth denial.

Mypy passed (203 files, 107.4 MiB peak/no swap); tests used a 512 MiB address-space
cap. No camera, production file/key, encoder or container was touched. Backend
rebuild is pending. Next: temporary MSE protocol/stream restrictions and ordinary
control permissions, abuse protection, full browser/proxy validation and login/UI
activation. See [staged sessions](temporary-sessions.md).
