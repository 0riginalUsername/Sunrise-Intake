#!/usr/bin/env python3
"""
Build & publish script for the Data-Intake EXE.

Usage:
  py build.py

It will:
  1. Prompt for version, publish date, and a one-line release note.
  2. Patch APP_VERSION / APP_BUILD_DATE in the source file.
  3. Build the EXE with PyInstaller.
  4. Append the note to CHANGELOG.md.
  5. (Optional, on confirm) commit, tag (vX.Y.Z), and push to GitHub.

All settings other than version/date/notes come from build_config.ini.
"""
import configparser
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Use the raw __file__ path so we stay on the mapped drive letter (Z:)
# rather than following the Egnyte UNC symlink that resolve() would produce.
SCRIPT_DIR    = Path(__file__).parent.absolute()
CONFIG_PATH   = SCRIPT_DIR / "build_config.ini"
CHANGELOG     = SCRIPT_DIR / "CHANGELOG.md"
CHANGELOG_MARK = "<!-- BUILD-LOG -->"


def _safe_name(s: str) -> str:
    """Strip characters that are invalid in Windows file/path names."""
    return re.sub(r'[\\/:*?"<>|]', '-', s)


def _load_ini() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH, encoding="utf-8")
    if "build" not in cfg:
        cfg["build"] = {}
    return cfg


def _save_ini(cfg: configparser.ConfigParser) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        cfg.write(fh)


def _resolve_script(script_str: str) -> Path:
    """Resolve last_script_path; relative paths are taken from SCRIPT_DIR."""
    p = Path(script_str)
    if not p.is_absolute():
        p = SCRIPT_DIR / p
    return p


def prompt_version_info(cfg: configparser.ConfigParser) -> tuple[str, str, str]:
    sec = cfg["build"]
    cur_ver  = sec.get("version",      "")
    cur_date = sec.get("publish_date", "")

    ver  = input(f"Version number [{cur_ver}]: "  if cur_ver  else "Version number: ").strip() or cur_ver
    date = input(f"Publish date   [{cur_date}]: " if cur_date else "Publish date:   ").strip() or cur_date
    notes = input("Release note (one line, optional): ").strip()

    if not ver:
        print("[error] Version number is required.")
        sys.exit(1)
    if not date:
        print("[error] Publish date is required.")
        sys.exit(1)

    sec["version"]      = ver
    sec["publish_date"] = date
    _save_ini(cfg)
    return ver, date, notes


def patch_intake_version(script_path: Path, version: str, date: str) -> None:
    content = script_path.read_text(encoding="utf-8")
    patched = re.sub(
        r'(APP_VERSION\s*=\s*")[^"]*(")',
        rf'\g<1>Data Intake v{version}\g<2>',
        content,
    )
    patched = re.sub(
        r'(APP_BUILD_DATE\s*=\s*")[^"]*(")',
        rf'\g<1>{date}\g<2>',
        patched,
    )
    if patched == content:
        print("[skip] Intake UI version already up to date")
        return
    script_path.write_text(patched, encoding="utf-8")
    print(f"[ok] Patched source -> 'Data Intake v{version}  |  {date}'")


def append_changelog(version: str, date: str, notes: str) -> None:
    """Insert a new entry just below the BUILD-LOG marker (newest on top)."""
    note_line = notes if notes else "(no release note provided)"
    entry = f"\n## v{version} — {date}\n- {note_line}\n"

    if not CHANGELOG.exists():
        CHANGELOG.write_text(
            f"# Changelog\n\n{CHANGELOG_MARK}\n{entry}", encoding="utf-8"
        )
        print("[ok] Created CHANGELOG.md")
        return

    text = CHANGELOG.read_text(encoding="utf-8")
    if CHANGELOG_MARK in text:
        text = text.replace(CHANGELOG_MARK, CHANGELOG_MARK + entry, 1)
    else:  # no marker — just prepend after the first heading
        text = text.rstrip() + "\n" + entry
    CHANGELOG.write_text(text, encoding="utf-8")
    print(f"[ok] Logged v{version} in CHANGELOG.md")


def _git(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(SCRIPT_DIR),
        capture_output=True, text=True, check=check,
    )


def git_publish(version: str, notes: str) -> None:
    """Commit all source changes, tag the release, and push to GitHub."""
    tag = f"v{version}"

    # Anything to commit?
    status = _git(["status", "--porcelain"]).stdout.strip()
    if not status:
        print("[git] No source changes to commit.")
    else:
        print("\n[git] Changes to be published:")
        print(status)

    ans = input(f"\nCommit, tag {tag}, and push to GitHub? [Y/n]: ").strip().lower()
    if ans in ("n", "no"):
        print("[git] Skipped. EXE is built; nothing pushed.")
        return

    # Commit (only if there is something staged after add)
    if status:
        _git(["add", "-A"])
        subject = f"Release {tag}"
        if notes:
            subject += f" — {notes}"
        commit = _git(["commit", "-m", subject], check=False)
        if commit.returncode != 0:
            print("[git] Nothing committed:", commit.stdout.strip() or commit.stderr.strip())

    # Tag (skip if it already exists)
    existing = _git(["tag", "--list", tag]).stdout.strip()
    if existing:
        print(f"[git] Tag {tag} already exists — leaving it as is.")
    else:
        msg = notes or f"Release {tag}"
        _git(["tag", "-a", tag, "-m", msg])
        print(f"[git] Created tag {tag}")

    # Push branch + this tag
    push = _git(["push"], check=False)
    print((push.stdout + push.stderr).strip())
    push_tag = _git(["push", "origin", tag], check=False)
    print((push_tag.stdout + push_tag.stderr).strip())

    if push.returncode == 0 and push_tag.returncode == 0:
        print(f"[git] Published {tag} to GitHub.")
    else:
        print("[git] Push reported a problem — check the output above.")

    # --- Optional: attach the .exe to a GitHub Release (needs the `gh` CLI) ---
    # Not enabled because `gh` is not installed. Once you `winget install GitHub.cli`
    # and run `gh auth login`, you can publish the binary without bloating git:
    #
    #   gh release create v{version} "dist/<exe-name>.exe" --title v{version} --notes "<notes>"


def main():
    cfg = _load_ini()
    sec = cfg["build"]

    exe_base   = (sec.get("exe_name")         or "").strip()
    icon_path  = (sec.get("icon_path")        or "").strip()
    script_str = (sec.get("last_script_path") or "").strip()
    onefile    = sec.getboolean("onefile",  True)
    windowed   = sec.getboolean("windowed", False)

    if not script_str:
        print("[error] last_script_path in build_config.ini is not set.")
        sys.exit(1)

    script_path = _resolve_script(script_str)
    if not script_path.is_file():
        print(f"[error] Source file not found: {script_path}")
        sys.exit(1)

    if not exe_base:
        exe_base = script_path.stem

    version, date, notes = prompt_version_info(cfg)
    patch_intake_version(script_path, version, date)

    exe_name = _safe_name(f"{exe_base}_v{version}_{date}")

    icon_abs = ""
    if icon_path:
        p = Path(icon_path)
        if not p.is_absolute():
            p = SCRIPT_DIR / p
        if p.exists():
            icon_abs = str(p)

    build_dir = SCRIPT_DIR / "build"
    build_dir.mkdir(exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name",     exe_name,
        "--distpath", str(SCRIPT_DIR / "dist"),
        "--workpath", str(build_dir),
        "--specpath", str(build_dir),   # keep generated .spec out of the repo root
    ]
    if onefile:
        cmd.append("--onefile")
    if windowed:
        cmd.append("--windowed")
    if icon_abs:
        cmd.extend(["--icon", icon_abs])
    cmd.append(str(script_path))

    print(f"\n[build] {exe_name}.exe  <-  {script_path.name}")
    print("Command:", " ".join(cmd))

    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    if result.returncode != 0:
        print(f"[error] Build failed (exit {result.returncode})")
        sys.exit(result.returncode)

    print(f"[ok] Output: {SCRIPT_DIR / 'dist' / (exe_name + '.exe')}")

    append_changelog(version, date, notes)
    git_publish(version, notes)
    sys.exit(0)


if __name__ == "__main__":
    main()
