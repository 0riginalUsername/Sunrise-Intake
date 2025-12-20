#!/usr/bin/env python3
"""
ppp_header_check.py

Compares REDtoolbox static-ppp solution to the RINEX header "APPROX POSITION XYZ".
- Reads header ECEF XYZ (meters).
- Parses PPP result (ECEF preferred; falls back to Lat/Lon/Height).
 - Computes Delta N/E/U, Horizontal(2D), and 3D distances.

Usage examples:

# 1) End-to-end: run REDtoolbox, then compare
python ppp_header_check.py ^
  --obs F:\staticppk\03992480.obs --nav F:\staticppk\03992480.nav ^
  --cli "C:\Program Files\REDtoolbox\resources\assets\REDtoolboxCLI\REDtoolboxCLI.exe" ^
  --work F:\staticppk\proc --results F:\staticppk\results --result-file result.txt

# 2) Parse-only: PPP already computed in F:\staticppk\results\result.txt
python ppp_header_check.py ^
  --obs F:\staticppk\03992480.obs ^
  --result-full "F:\staticppk\results\result.txt" --skip-run
"""

import argparse
import math
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------
# WGS84 constants & helpers
# ---------------------------
WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)

def geodetic_to_ecef(lat_deg, lon_deg, h):
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    N = WGS84_A / math.sqrt(1 - WGS84_E2 * sin_lat * sin_lat)
    X = (N + h) * cos_lat * math.cos(lon)
    Y = (N + h) * cos_lat * math.sin(lon)
    Z = (N * (1 - WGS84_E2) + h) * sin_lat
    return X, Y, Z

def ecef_to_geodetic(X, Y, Z):
# Bowring's method (iterative but fast)
    lon = math.atan2(Y, X)
    p = math.hypot(X, Y)
    lat = math.atan2(Z, p * (1 - WGS84_E2))
    for _ in range(5):
        sin_lat = math.sin(lat)
        N = WGS84_A / math.sqrt(1 - WGS84_E2 * sin_lat * sin_lat)
        h = p / math.cos(lat) - N
        lat = math.atan2(Z, p * (1 - WGS84_E2 * (N / (N + h))))
    sin_lat = math.sin(lat)
    N = WGS84_A / math.sqrt(1 - WGS84_E2 * sin_lat * sin_lat)
    h = p / math.cos(lat) - N
    return math.degrees(lat), math.degrees(lon), h

def ecef_to_enu(dX, dY, dZ, lat0_deg, lon0_deg):
    # Rotation from ECEF delta to local ENU at (lat0, lon0)
    lat = math.radians(lat0_deg)
    lon = math.radians(lon0_deg)
    sin_lat = math.sin(lat); cos_lat = math.cos(lat)
    sin_lon = math.sin(lon); cos_lon = math.cos(lon)

    t = [
        [-sin_lon,              cos_lon,               0],
        [-sin_lat*cos_lon, -sin_lat*sin_lon,  cos_lat],
        [ cos_lat*cos_lon,  cos_lat*sin_lon,  sin_lat]
    ]
    # East, North, Up
    E = t[0][0]*dX + t[0][1]*dY + t[0][2]*dZ
    N = t[1][0]*dX + t[1][1]*dY + t[1][2]*dZ
    U = t[2][0]*dX + t[2][1]*dY + t[2][2]*dZ
    return N, E, U

# ---------------------------
# Parsers
# ---------------------------
def parse_rinex_header_xyz(rinex_path: Path):
    """
    Returns (X, Y, Z) in meters from 'APPROX POSITION XYZ' header line.
    Supports RINEX 2/3 standard formatting.
    """
    patt = re.compile(r'^\s*([+-]?\d+\.\d+)\s+([+-]?\d+\.\d+)\s+([+-]?\d+\.\d+)\s+APPROX POSITION XYZ')
    with rinex_path.open('r', errors='ignore') as f:
        for line in f:
            m = patt.search(line)
            if m:
                X, Y, Z = map(float, m.groups())
                return X, Y, Z
            # header ends at 'END OF HEADER'
            if 'END OF HEADER' in line:
                break
    raise ValueError(f"APPROX POSITION XYZ not found in header: {rinex_path}")

def parse_ppp_result_xyz_or_llh(result_path: Path):
    """
    Tries to pull ECEF first; if not found, looks for Lat/Lon/Height.
    Returns dict like:
        {"type": "ecef", "X":..., "Y":..., "Z":...}
    or  {"type": "llh", "lat":..., "lon":..., "h":...}
    Patterns are loose to accommodate vendor formatting.
    """
    txt = result_path.read_text(errors='ignore')

    # Try ECEF triplet in meters
    # Common variants seen in tools:
    #   X:  -1234567.890 m
    #   X(m): -1234567.890
    #   ECEF X = -1234567.890
    ecef_patterns = [
        r'X[^0-9\-+]*([\-+]?\d+\.\d+)\s*m',
        r'Y[^0-9\-+]*([\-+]?\d+\.\d+)\s*m',
        r'Z[^0-9\-+]*([\-+]?\d+\.\d+)\s*m',
    ]
    # Pull in order, but we’ll be more robust using a single pass per axis
    Xm = re.search(r'(?:ECEF|^|\n)\s*X[^\d\-+]*([\-+]?\d+\.\d+)', txt, re.IGNORECASE)
    Ym = re.search(r'(?:ECEF|^|\n)\s*Y[^\d\-+]*([\-+]?\d+\.\d+)', txt, re.IGNORECASE)
    Zm = re.search(r'(?:ECEF|^|\n)\s*Z[^\d\-+]*([\-+]?\d+\.\d+)', txt, re.IGNORECASE)
    if Xm and Ym and Zm:
        try:
            X = float(Xm.group(1)); Y = float(Ym.group(1)); Z = float(Zm.group(1))
            # Heuristic sanity check: magnitude ~ Earth radius range
            if 6.0e6 > abs(X) or 6.0e6 > abs(Y) or 6.0e6 > abs(Z):
                # Values might still be valid (depends on axis), so don't over-prune.
                pass
            return {"type": "ecef", "X": X, "Y": Y, "Z": Z}
        except Exception:
            pass

    # Try Lat/Lon/Height (deg, deg, m)
    # Variants:
    #   Latitude: 40.123456 deg
    #   Lon = -111.987654 deg
    #   Height 1507.321 m
    latm = re.search(r'Lat(?:itude)?[^0-9\-+]*([\-+]?\d+\.\d+)', txt, re.IGNORECASE)
    lonm = re.search(r'Lon(?:gitude)?[^0-9\-+]*([\-+]?\d+\.\d+)', txt, re.IGNORECASE)
    hm = re.search(r'(?:Hgt|Height|Alt|Altitude|Ellip(?:soidal)?\s*Height)[^\d\-+]*([\-+]?\d+\.\d+)', txt, re.IGNORECASE)
    if latm and lonm and hm:
        return {"type": "llh", "lat": float(latm.group(1)), "lon": float(lonm.group(1)), "h": float(hm.group(1))}

    raise ValueError(f"Could not find PPP coordinates in: {result_path}")

# ---------------------------
# Runner
# ---------------------------
def run_redtoolbox_static_ppp(cli, obs, nav, work, results_dir, result_file):
    env = os.environ.copy()
    if work:
        env["REDTOOLBOX_INTERNAL_PROC_FOLDER"] = str(work)
    Path(results_dir).mkdir(parents=True, exist_ok=True)
    cmd = [
        str(cli), "static-ppp", "-log", "verbose",
        "-b", str(obs), "-n", str(nav),
        "-result", str(Path(results_dir) / result_file),
        "-k", "-o", str(results_dir)
    ]
    print("Running:", " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    subprocess.check_call(cmd, env=env)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--obs", required=True, help="RINEX observation file (.obs)")
    ap.add_argument("--nav", help="Navigation file (.nav); required unless --skip-run")
    ap.add_argument("--cli", help="Path to REDtoolboxCLI.exe")
    ap.add_argument("--work", help="Processing/work folder (sets REDTOOLBOX_INTERNAL_PROC_FOLDER)")
    ap.add_argument("--results", help="Results folder", default=".")
    ap.add_argument("--result-file", help="PPP result filename (if running CLI)", default="result.txt")
    ap.add_argument("--result-full", help="Full path to an existing PPP result to parse")
    ap.add_argument("--skip-run", action="store_true", help="Skip running REDtoolbox; just parse files")
    args = ap.parse_args()

    obs = Path(args.obs)
    if not obs.exists():
        print(f"Missing obs: {obs}", file=sys.stderr); sys.exit(2)

    # 1) Parse header XYZ from RINEX
    Xh, Yh, Zh = parse_rinex_header_xyz(obs)
    lat_h, lon_h, h_h = ecef_to_geodetic(Xh, Yh, Zh)

    # 2) Obtain PPP result (run CLI or read existing file)
    if args.skip_run:
        if not args.result_full:
            print("--skip-run requires --result-full", file=sys.stderr)
            sys.exit(2)
        result_path = Path(args.result_full)
    else:
        if not args.cli or not args.nav:
            print("When not using --skip-run, both --cli and --nav are required.", file=sys.stderr)
            sys.exit(2)
        cli = Path(args.cli)
        if not cli.exists():
            print(f"Missing CLI: {cli}", file=sys.stderr); sys.exit(2)
        results_dir = Path(args.results) if args.results else Path(".")
        run_redtoolbox_static_ppp(cli, obs, Path(args.nav), Path(args.work) if args.work else None, results_dir, args.result_file)
        result_path = results_dir / args.result_file

    if not result_path.exists():
        print(f"PPP result not found: {result_path}", file=sys.stderr); sys.exit(2)

    # 3) Parse PPP coordinates
    ppp = parse_ppp_result_xyz_or_llh(result_path)
    if ppp["type"] == "ecef":
        Xp, Yp, Zp = ppp["X"], ppp["Y"], ppp["Z"]
        lat_p, lon_p, h_p = ecef_to_geodetic(Xp, Yp, Zp)
    else:
        lat_p, lon_p, h_p = ppp["lat"], ppp["lon"], ppp["h"]
        Xp, Yp, Zp = geodetic_to_ecef(lat_p, lon_p, h_p)

    # 4) Differences
    dX, dY, dZ = (Xp - Xh, Yp - Yh, Zp - Zh)
    # ENU at PPP position (you could choose header position; PPP anchor is typical)
    N, E, U = ecef_to_enu(dX, dY, dZ, lat_p, lon_p)
    horiz = math.hypot(N, E)
    dist3d = math.sqrt(N*N + E*E + U*U)

    # 5) Report
    print("\n=== RINEX Header (APPROX POSITION XYZ) ===")
    print(f"ECEF X/Y/Z (m): {Xh:.4f}, {Yh:.4f}, {Zh:.4f}")
    print(f"Geodetic lat/lon/h: {lat_h:.9f}, {lon_h:.9f}, {h_h:.3f} m")

    print("\n=== PPP Solution ===")
    print(f"ECEF X/Y/Z (m): {Xp:.4f}, {Yp:.4f}, {Zp:.4f}")
    print(f"Geodetic lat/lon/h: {lat_p:.9f}, {lon_p:.9f}, {h_p:.3f} m")
    print(f"Calculated height (ellipsoidal, m): {h_p:.3f}")

    print("\n=== Differences (PPP minus Header) ===")
    print(f"dX/dY/dZ (m): {dX:.4f}, {dY:.4f}, {dZ:.4f}")
    print(f"N/E/U (m):   {N:.4f}, {E:.4f}, {U:.4f}")
    print(f"Horizontal (2D) = {horiz:.4f} m")
    print(f"3D distance    = {dist3d:.4f} m")

    # Explicit summary for quick QA comparison.
    print(f"\nDistance between calculated and initial position (3D): {dist3d:.4f} m")

    # Optional pass/fail threshold (edit to taste)
    THRESH_2D = 1.0  # meters
    if horiz <= THRESH_2D:
        print(f"\nQA: PASS (2D <= {THRESH_2D} m)")
    else:
        print(f"\nQA: FAIL (2D > {THRESH_2D} m)")

    # Persist a concise summary (including height) alongside PPP results
    try:
        summary_lines = [
            "PPP Summary",
            f"RINEX header ECEF (m): {Xh:.4f}, {Yh:.4f}, {Zh:.4f}",
            f"RINEX header geodetic: lat {lat_h:.9f}, lon {lon_h:.9f}, h {h_h:.3f} m",
            f"PPP ECEF (m): {Xp:.4f}, {Yp:.4f}, {Zp:.4f}",
            f"PPP geodetic: lat {lat_p:.9f}, lon {lon_p:.9f}, h {h_p:.3f} m",
            f"Calculated height (ellipsoidal, m): {h_p:.3f}",
            f"dX/dY/dZ (m): {dX:.4f}, {dY:.4f}, {dZ:.4f}",
            f"N/E/U (m): {N:.4f}, {E:.4f}, {U:.4f}",
            f"Horizontal (2D) = {horiz:.4f} m",
            f"3D distance = {dist3d:.4f} m",
        ]
        summary_path = Path(args.results if args.results else ".") / "ppp_summary.txt"
        summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
        print(f"\nPPP summary written to: {summary_path}")
    except Exception as e:
        print(f"Warning: could not write PPP summary file: {e}")

if __name__ == "__main__":
    main()
