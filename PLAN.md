# Repair Plan

Status: idle

- [x] Restore legacy CLI imports for `xgkb_sync_dir.py` and `xgkb_retry.py`.
- [x] Make pull/sync safe: dry-run must not mutate SQLite, failed cloud walks must not delete local files, conflicts must honor `--conflict`.
- [x] Prevent binary pull corruption by skipping unsupported non-text downloads until a binary download API is implemented.
- [x] Fix SQLite migration so migrated state is readable by the v2.1 hash-key state loader.
- [x] Fix API/version correctness: URL-encode GET params and record real cloud version after push.
- [x] Add focused regression tests and run local verification.
- [x] Run Claude Code second audit after fixes.
