"""
Scan a folder recursively for JPEGs and report DJI Rtk Flag via ExifTool (Phil Harvey).

Run with a path argument, or with no arguments to be prompted for the folder path.
Requires exiftool on PATH or at EXIFTOOL_EXE below.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

# Match Data-Intake1.13 — override with env EXIFTOOL if set
def _default_exiftool_path() -> str:
    return os.path.normpath(
        os.path.join(
            os.environ.get("APPDATA", ""),
            "REDcatch GmBH",
            "REDToolbox",
            "exif",
            "exiftool.exe",
        )
    )


EXIFTOOL_EXE = os.environ.get("EXIFTOOL") or _default_exiftool_path()
SUBPROCESS_TIMEOUT = 1800


def resolve_exiftool() -> Optional[str]:
    red_local = os.path.normpath(
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "REDcatch GmBH",
            "REDToolbox",
            "exif",
            "exiftool.exe",
        )
    )
    for p in (
        EXIFTOOL_EXE,
        red_local,
        r"C:\Program Files\exiftool\exiftool.exe",
        shutil.which("exiftool"),
        shutil.which("exiftool.exe"),
    ):
        if p and os.path.isfile(p):
            return p
    return None


def _parse_rtk_flag_value(raw: object) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    s = str(raw).strip()
    if not s or s.lower() in ("none", "null", "n/a", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _rtk_flag_from_record(rec: Dict[str, Any]) -> Tuple[Optional[float], str]:
    """Return (numeric value if any, display string for CSV)."""
    direct_keys = (
        "RtkFlag",
        "Rtk Flag",
        "DJI:RtkFlag",
        "MakerNotes:RtkFlag",
    )
    for k in direct_keys:
        if k in rec:
            raw = rec.get(k)
            num = _parse_rtk_flag_value(raw)
            if num is not None:
                return num, str(num).rstrip("0").rstrip(".") if "." in str(num) else str(int(num))
            if raw is not None and str(raw).strip():
                return None, str(raw).strip()
    for k, v in rec.items():
        if k == "SourceFile":
            continue
        compact = k.replace(" ", "").replace(":", "").lower()
        if "rtkflag" in compact:
            num = _parse_rtk_flag_value(v)
            if num is not None:
                s = str(num).rstrip("0").rstrip(".") if "." in str(num) else str(int(num))
                return num, s
            if v is not None and str(v).strip():
                return None, str(v).strip()
    return None, ""


def run_exiftool_rtk(folder: str, exiftool: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    cmd = [
        exiftool,
        "-json",
        "-n",
        "-RtkFlag",
        "-ext",
        "jpg",
        "-ext",
        "jpeg",
        "-ext",
        "JPG",
        "-ext",
        "JPEG",
        "-r",
        folder,
    ]
    run_kw: Dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": SUBPROCESS_TIMEOUT,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        run_kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        proc = subprocess.run(cmd, **run_kw)
    except subprocess.TimeoutExpired:
        return [], "ExifTool timed out."
    except Exception as e:
        return [], f"ExifTool failed: {e}"

    if proc.returncode != 0 and not (proc.stdout or "").strip():
        err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        return [], err

    try:
        records = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as e:
        return [], f"Could not parse ExifTool JSON: {e}"

    if not isinstance(records, list):
        records = [records]
    return [r for r in records if isinstance(r, dict)], None


def _normalize_folder_arg(raw: Optional[str]) -> str:
    """Strip whitespace and surrounding quotes from a path string."""
    if not raw:
        return ""
    s = raw.strip().strip('"').strip("'")
    return s.strip()


def main() -> int:
    p = argparse.ArgumentParser(description="List all JPEGs under a folder with Rtk Flag (ExifTool).")
    p.add_argument(
        "folder",
        nargs="?",
        default=None,
        metavar="FOLDER",
        help="Root folder to scan recursively. If omitted, you are prompted to type or paste a path.",
    )
    p.add_argument(
        "-o",
        "--output",
        default="",
        help="CSV output path (default: <folder>/rtk_flag_report.csv)",
    )
    args = p.parse_args()

    folder = _normalize_folder_arg(args.folder)
    if not folder:
        try:
            folder = _normalize_folder_arg(
                input("Folder path to scan (recursive JPGs): ")
            )
        except EOFError:
            folder = ""
    if not folder:
        print("No folder path given.", file=sys.stderr)
        return 1

    root = os.path.abspath(folder)
    if not os.path.isdir(root):
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    exiftool = resolve_exiftool()
    if not exiftool:
        print(
            "ExifTool not found. Install it and/or set EXIFTOOL env or EXIFTOOL_EXE in tester.py.",
            file=sys.stderr,
        )
        return 1

    records, err = run_exiftool_rtk(root, exiftool)
    if err:
        print(err, file=sys.stderr)
        if not records:
            return 1

    out_path = args.output.strip() or os.path.join(root, "rtk_flag_report.csv")
    rows: List[Tuple[str, str]] = []
    for rec in records:
        path = rec.get("SourceFile") or ""
        _, rtk_display = _rtk_flag_from_record(rec)
        rows.append((path, rtk_display))

    rows.sort(key=lambda r: r[0].lower())

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "rtk_flag"])
        w.writerows(rows)

    # Print CSV to stdout as well
    w = csv.writer(sys.stdout, lineterminator="\n")
    w.writerow(["path", "rtk_flag"])
    w.writerows(rows)

    print(f"Wrote {len(rows)} row(s) to {out_path}", file=sys.stderr)
    if err:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
