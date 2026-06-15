import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple, List


WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


def parse_rinex_header_approx_position_xyz(rinex_path: str) -> Tuple[float, float, float]:
	"""
	Parse RINEX header to extract APPROX POSITION XYZ (ECEF meters).
	Supports RINEX 2/3 header style labels in columns 61-80 but will
	also match label anywhere in the line for robustness.
	"""
	x = y = z = None

	with open(rinex_path, "r", encoding="utf-8", errors="ignore") as f:
		for raw_line in f:
			line = raw_line.rstrip("\n")
			label = line[60:80].strip() if len(line) >= 80 else ""
			if label == "APPROX POSITION XYZ" or "APPROX POSITION XYZ" in line:
				# Values are typically in columns 1-60 with free format floats
				# Try robust float extraction: take first three floats in the line
				nums = _extract_floats(line[:60])
				if len(nums) >= 3:
					x, y, z = nums[0], nums[1], nums[2]
					break
			if label == "END OF HEADER" or line.strip() == "END OF HEADER":
				break

	if x is None or y is None or z is None:
		raise ValueError("Failed to find 'APPROX POSITION XYZ' in RINEX header.")
	return x, y, z


def _extract_floats(text: str) -> List[float]:
	pattern = r"[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?"
	return [float(m[0]) for m in re.finditer(pattern, text)]


def ecef_to_geodetic(x: float, y: float, z: float) -> Tuple[float, float, float]:
	"""
	Convert ECEF (m) to geodetic latitude (rad), longitude (rad), and ellipsoidal height (m).
	"""
	lon = math.atan2(y, x)
	p = math.hypot(x, y)
	theta = math.atan2(z * WGS84_A, p * (WGS84_A * (1.0 - WGS84_F)))
	sin_theta = math.sin(theta)
	cos_theta = math.cos(theta)

	# Bowring's formula for initial latitude
	lat = math.atan2(z + (1.0 - WGS84_F) * (1.0 - WGS84_F) * WGS84_A * (WGS84_E2 / (1.0 - WGS84_E2)) * sin_theta**3,
	                 p - WGS84_E2 * WGS84_A * cos_theta**3)

	# Iterate to refine latitude and height
	for _ in range(5):
		sin_lat = math.sin(lat)
		N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
		h = p / math.cos(lat) - N
		lat_new = math.atan2(z, p * (1.0 - WGS84_E2 * N / (N + h)))
		if abs(lat_new - lat) < 1e-12:
			lat = lat_new
			break
		lat = lat_new

	sin_lat = math.sin(lat)
	N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
	h = p / math.cos(lat) - N
	return lat, lon, h


def geodetic_to_ecef(lat: float, lon: float, h: float) -> Tuple[float, float, float]:
	"""
	Convert geodetic latitude (rad), longitude (rad), and height (m) to ECEF (m).
	"""
	sin_lat = math.sin(lat)
	cos_lat = math.cos(lat)
	sin_lon = math.sin(lon)
	cos_lon = math.cos(lon)
	N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
	x = (N + h) * cos_lat * cos_lon
	y = (N + h) * cos_lat * sin_lon
	z = (N * (1.0 - WGS84_E2) + h) * sin_lat
	return x, y, z


def ecef_delta_to_enu(dx: float, dy: float, dz: float, lat_ref: float, lon_ref: float) -> Tuple[float, float, float]:
	"""
	Rotate ECEF delta into local ENU at reference geodetic lat/lon (radians).
	"""
	sin_lat = math.sin(lat_ref)
	cos_lat = math.cos(lat_ref)
	sin_lon = math.sin(lon_ref)
	cos_lon = math.cos(lon_ref)

	e = -sin_lon * dx + cos_lon * dy
	n = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
	u =  cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz
	return e, n, u


def run_redtoolbox_cli(cli_exe: str, rinex_path: str, output_path: Optional[str], cli_args_template: Optional[str]) -> Optional[str]:
	"""
	Run REDtoolbox CLI using a user-provided args template.
	- cli_exe: path to REDtoolboxCLI.exe (or command name in PATH).
	- rinex_path: input RINEX file path.
	- output_path: path to an output file the CLI will write (JSON/CSV recommended). If provided,
	               this path will be injected into the template as {output}.
	- cli_args_template: a template string where {rinex} and {output} will be formatted.
	Returns the path to the output file if known, otherwise None (if results are expected on stdout).
	"""
	if not cli_exe:
		raise ValueError("CLI executable path/name must be provided via --cli-path")
	if not cli_args_template:
		# If template not provided, we cannot guess subcommands; require user to pass it.
		raise ValueError("CLI arguments template is required via --cli-args")

	template = cli_args_template
	if "{rinex}" not in template:
		raise ValueError("CLI args template must include {rinex}")
	if "{output}" in template and not output_path:
		raise ValueError("CLI args template uses {output} but --cli-output not provided")

	formatted_args = template.format(rinex=quote_path(rinex_path), output=quote_path(output_path) if output_path else "")

	cmd = f"{quote_path(cli_exe)} {formatted_args}"
	try:
		result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
	except subprocess.CalledProcessError as e:
		stdout = e.stdout or ""
		stderr = e.stderr or ""
		raise RuntimeError(f"REDtoolbox CLI failed with code {e.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")

	return output_path


def quote_path(path: str) -> str:
	if not path:
		return path
	if " " in path or "(" in path or ")" in path:
		return f"\"{path}\""
	return path


def parse_cli_position_output(output_path: str) -> Tuple[float, float, float]:
	"""
	Parse computed base position from REDtoolbox CLI output.
	Supports:
	- JSON with fields: latitude/lat (deg), longitude/lon (deg), height/h (m), or x/y/z (ECEF m)
	- CSV with header containing these field names
	Returns ECEF (m).
	"""
	if not output_path or not os.path.exists(output_path):
		raise FileNotFoundError(f"CLI output not found: {output_path}")

	ext = os.path.splitext(output_path)[1].lower()
	if ext == ".json":
		with open(output_path, "r", encoding="utf-8") as f:
			data = json.load(f)
		return _extract_position_from_dictlike(data)

	# Very simple CSV parsing
	with open(output_path, "r", encoding="utf-8") as f:
		lines = [ln.strip() for ln in f.readlines() if ln.strip()]
	if not lines:
		raise ValueError("CLI output file is empty")
	header = [h.strip().lower() for h in lines[0].split(",")]
	values = [v.strip() for v in lines[1].split(",")] if len(lines) > 1 else []
	row: Dict[str, str] = {header[i]: values[i] for i in range(min(len(header), len(values)))}

	return _extract_position_from_dictlike(row)


def _extract_position_from_dictlike(obj: Dict) -> Tuple[float, float, float]:
	# Try lat/lon/height first
	lat_keys = ["latitude", "lat"]
	lon_keys = ["longitude", "lon", "lng"]
	h_keys = ["height", "ellipsoidal_height", "h", "elevation", "ellh"]

	def find_first(keys: List[str]) -> Optional[float]:
		for k in keys:
			if k in obj:
				try:
					return float(obj[k])
				except Exception:
					# nested or string
					try:
						return float(obj.get(k, None))
					except Exception:
						continue
		return None

	lat_deg = find_first(lat_keys)
	lon_deg = find_first(lon_keys)
	h_m = find_first(h_keys)

	if lat_deg is not None and lon_deg is not None and h_m is not None:
		lat = math.radians(lat_deg)
		lon = math.radians(lon_deg)
		return geodetic_to_ecef(lat, lon, h_m)

	# Try ECEF XYZ
	x_keys = ["x", "ecef_x", "x_m"]
	y_keys = ["y", "ecef_y", "y_m"]
	z_keys = ["z", "ecef_z", "z_m"]
	x_val = find_first(x_keys)
	y_val = find_first(y_keys)
	z_val = find_first(z_keys)
	if x_val is not None and y_val is not None and z_val is not None:
		return x_val, y_val, z_val

	raise ValueError("Unable to find lat/lon/height or ECEF x/y/z in CLI output.")


def parse_ppp_summary_text(summary_path: str) -> Tuple[float, float, float]:
    """
    Parse the text summary produced by static-ppp.py (ppp_summary.txt).
    Expects lines containing either:
      - PPP geodetic: lat <val>, lon <val>, h <val> m
      - PPP ECEF (m): X, Y, Z
    Returns ECEF tuple (x, y, z).
    """
    if not summary_path or not os.path.exists(summary_path):
        raise FileNotFoundError(f"PPP summary not found: {summary_path}")

    with open(summary_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [ln.strip() for ln in f.readlines() if ln.strip()]

    lat = lon = h = None
    x = y = z = None

    for line in lines:
        if "PPP geodetic:" in line:
            # e.g., PPP geodetic: lat 40.123..., lon -111.456..., h 1234.567 m
            parts = line.replace("PPP geodetic:", "").replace("lat", "").replace("lon", "").replace("h", "")
            parts = parts.replace("m", "").replace(",", " ")
            nums = _extract_floats(parts)
            if len(nums) >= 3:
                lat, lon, h = nums[0], nums[1], nums[2]
        if "PPP ECEF (m):" in line:
            # e.g., PPP ECEF (m): X, Y, Z
            nums = _extract_floats(line)
            if len(nums) >= 3:
                x, y, z = nums[0], nums[1], nums[2]

    if x is not None and y is not None and z is not None:
        return x, y, z
    if lat is not None and lon is not None and h is not None:
        return geodetic_to_ecef(math.radians(lat), math.radians(lon), h)

    raise ValueError(f"Could not parse PPP position from summary: {summary_path}")


def norm3(dx: float, dy: float, dz: float) -> float:
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare RINEX header base position to calculated base position (via REDtoolbox CLI).")
    parser.add_argument("rinex", help="Path to RINEX observation file")
    parser.add_argument("--cli-path", help="Path to REDtoolboxCLI.exe (or command if in PATH)")
    parser.add_argument("--cli-args", help="CLI args template including {rinex} and optionally {output}. Example: \"ppp --input {rinex} --export-json {output}\"")
    parser.add_argument("--cli-output", help="Path to CLI output file (JSON or CSV) that contains the calculated base position")
    parser.add_argument("--summary", help="Optional ppp_summary.txt path to parse if CLI output lacks coordinates")
    parser.add_argument("--skip-run", action="store_true", help="Skip running CLI; only parse existing --cli-output")
    parser.add_argument("--max-3d-m", type=float, default=None, help="If provided, fail (exit 2) when 3D difference exceeds this (meters)")
    args = parser.parse_args()

    rinex_path = os.path.abspath(args.rinex)
    if not os.path.exists(rinex_path):
        print(f"RINEX file not found: {rinex_path}", file=sys.stderr)
        sys.exit(1)

    try:
        header_x, header_y, header_z = parse_rinex_header_approx_position_xyz(rinex_path)
    except Exception as e:
        print(f"Error parsing RINEX header: {e}", file=sys.stderr)
        sys.exit(1)

    calculated_output_path = args.cli_output
    if not args.skip_run:
        if not args.cli_path:
            print("Missing --cli-path to REDtoolboxCLI.exe (or command in PATH). Use --skip-run to only parse --cli-output.", file=sys.stderr)
            sys.exit(1)
        if not args.cli_args:
            print("Missing --cli-args template. Use --skip-run to only parse --cli-output.", file=sys.stderr)
            sys.exit(1)
        try:
            run_redtoolbox_cli(args.cli_path, rinex_path, calculated_output_path, args.cli_args)
        except Exception as e:
            print(f"Error running REDtoolbox CLI: {e}", file=sys.stderr)
            sys.exit(1)

    if not calculated_output_path:
        print("No --cli-output provided; cannot parse calculated base position.", file=sys.stderr)
        sys.exit(1)

    calc_x = calc_y = calc_z = None
    parse_errors = []
    # 1) Try CLI output
    try:
        calc_x, calc_y, calc_z = parse_cli_position_output(calculated_output_path)
    except Exception as e:
        parse_errors.append(str(e))
    # 2) Try summary (explicit or inferred)
    if calc_x is None:
        summary_path = args.summary
        if not summary_path and calculated_output_path:
            summary_path = str(Path(calculated_output_path).parent / "ppp_summary.txt")
        if summary_path:
            try:
                calc_x, calc_y, calc_z = parse_ppp_summary_text(summary_path)
            except Exception as e:
                parse_errors.append(f"Summary parse error: {e}")

    if calc_x is None:
        print("Error parsing PPP output; tried CLI output and summary.", file=sys.stderr)
        for err in parse_errors:
            print(f"- {err}", file=sys.stderr)
        sys.exit(1)

    # Differences
    dx = calc_x - header_x
    dy = calc_y - header_y
    dz = calc_z - header_z
    diff_3d = norm3(dx, dy, dz)

    # Also provide ENU at calculated position
    calc_lat, calc_lon, calc_h = ecef_to_geodetic(calc_x, calc_y, calc_z)
    e, n, u = ecef_delta_to_enu(dx, dy, dz, calc_lat, calc_lon)

    print("RINEX Header Approx Position (ECEF, m):")
    print(f"  X: {header_x:.4f}  Y: {header_y:.4f}  Z: {header_z:.4f}")
    print("Calculated Base Position (ECEF, m):")
    print(f"  X: {calc_x:.4f}  Y: {calc_y:.4f}  Z: {calc_z:.4f}")
    print(f"ECEF Delta (m):  dX: {dx:.4f}  dY: {dy:.4f}  dZ: {dz:.4f}  |dXYZ|: {diff_3d:.4f}")
    print(f"ENU Delta (m) @ calculated position:  E: {e:.4f}  N: {n:.4f}  U: {u:.4f}")
    print("Calculated Base Position (geodetic):")
    print(f"  Lat: {math.degrees(calc_lat):.9f}°  Lon: {math.degrees(calc_lon):.9f}°  H: {calc_h:.4f} m")

    if args.max_3d_m is not None:
        if diff_3d > args.max_3d_m:
            print(f"FAIL: 3D difference {diff_3d:.3f} m exceeds threshold {args.max_3d_m:.3f} m")
            sys.exit(2)
        else:
            print(f"PASS: 3D difference {diff_3d:.3f} m within threshold {args.max_3d_m:.3f} m")


if __name__ == "__main__":
    main()


