# Changelog

All notable changes to the Data Intake application. Newest entries on top.
Entries below the marker are appended automatically by `build.py` on each release.

<!-- BUILD-LOG -->
## v2.4.1 — 07/01/2026
- Fixed 3dr classify bug

## v2.4 — 06/29/2026
- Added classification launch changes (still in progress)

## v2.3.1 — 06/29/2026
- Fixed vertical EPSG bug, added overried base position info support

## v2.2.1 — 06/22/2026
- Fixed EPSG auto-detect popup window bug

## v2.2 — 06/17/2026
- Added .csv TLT detection and implementation

## v2.2 — 06/17/2026
- Added EPSG functinality project location adjustment, and small ui changes

## v2.1.2 — 06/15/2026
- Added epsg ui check

## v2.1.1 — 06/15/2026
- Build automation test


## v2.1.1 — 06/15/2026
- Repository restructured: single source of truth at `src/data_intake.py`.
- Previous versions preserved under `_archive/`.
- Build artifacts (exe, build/, *.spec) and large sample data no longer tracked in git.
- `build.py` now commits, tags (`vX.Y.Z`), and pushes each release.
