# Private generated media configuration

Checkpoint: 2026-09-27. Source and temporary-file tests only; production files and
containers were not touched.

The generated go2rtc configuration contains camera credentials even though its API
and streams bind loopback. Both write paths now use one private-file writer:

- Creates the file with mode 0600, independently of permissive umask.
- Restricts an existing file before writing; even the unchanged-content fast path
  repairs mode without rewriting contents or changing the file's mtime.
- Rejects final-component symlinks and non-regular files on the supported POSIX
  Linux/WSL/Docker runtime. FIFO opening is nonblocking, then rejected.
- Preserves the inode: Docker mounts the config as a file, so atomic rename would
  leave the existing container bound to the old inode. This intentionally retains
  in-place write semantics, not crash-atomic replacement.
- Bounds old-content comparison to the newly generated text length; permission
  failure aborts before overwriting contents. Invalid old encoding can be replaced.

The app writer and go2rtc reader must use compatible ownership. The supplied Compose
services run without separate user overrides; custom deployments using different
UIDs must plan a shared, restricted access arrangement rather than chmod 0644.
Existing symlink-based configurations require migration to a regular file. Parent
directories must remain trusted; this is not an untrusted-directory sandbox. Native
Windows ACL enforcement is not provided by this POSIX helper; use Linux/WSL/Docker.

No encryption-at-rest claim: the engine needs these plaintext credentials. Keep
`data/` out of public file serving, shared support bundles and unprotected backups.
This patch does not repair historical copies or alter permissions of live files
until an updated backend actually executes the writer.

49 focused config/lifecycle tests passed, Ruff/mypy passed (220 source files).
Serial 512 MiB / 75% CPU limits: test peak 83.4 MiB, zero swap. Tests verify mode,
inode/open-reader continuity, unchanged mtime, symlink/FIFO rejection, invalid old
encoding and permission-error behavior without starting a media process.
