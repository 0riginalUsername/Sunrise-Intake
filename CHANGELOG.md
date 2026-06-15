# Changelog

All notable changes to the Data Intake application. Newest entries on top.
Entries below the marker are appended automatically by `build.py` on each release.

<!-- BUILD-LOG -->

## v2.1.1 — 06/15/2026
- Repository restructured: single source of truth at `src/data_intake.py`.
- Previous versions preserved under `_archive/`.
- Build artifacts (exe, build/, *.spec) and large sample data no longer tracked in git.
- `build.py` now commits, tags (`vX.Y.Z`), and pushes each release.
