"""
test_gps_epsg.py  —  standalone GPS / State Plane zone tester

Tests the same coordinate-extraction logic used in data_intake.py:
  • Reads GPS from a drone image (EXIF)
  • Reads GPS from a T02/T04 base file (via convertToRINEX → RINEX header)
  • Queries the State Plane shapefile for the EPSG zone

Run directly:  python tools/test_gps_epsg.py
"""

import math
import os
import shutil
import subprocess
import tempfile
import tkinter as tk
from tkinter import filedialog, scrolledtext

from PIL import Image

import struct

# ── Constants (mirror data_intake.py) ────────────────────────────────────────
CONVERT_TO_RINEX_EXE = r"C:\Program Files (x86)\Trimble\convertToRINEX\convertToRinex.exe"
STATEPLANE_SHAPEFILE = r"Z:/Survey/UT/_Scripts/SunrisePhoto/resources/NAD83SPCEPSG.shp"


# ── Coordinate helpers (exact copies of data_intake.py functions) ─────────────

def _ecef_to_geodetic(x: float, y: float, z: float):
    """ECEF metres → (lat_deg, lon_deg) via iterative WGS84."""
    a  = 6378137.0
    e2 = 0.00669437999014
    lon = math.degrees(math.atan2(y, x))
    p   = math.sqrt(x ** 2 + y ** 2)
    lat = math.degrees(math.atan2(z, p * (1 - e2)))
    for _ in range(10):
        sin_lat = math.sin(math.radians(lat))
        N   = a / math.sqrt(1 - e2 * sin_lat ** 2)
        lat = math.degrees(math.atan2(z + e2 * N * sin_lat, p))
    return lat, lon


def _gps_from_image(image_path: str, log):
    """
    Extract (lat, lon) from image GPS EXIF.
    Handles DMS 3-element tuples, single decimal-degree rationals, and plain floats.
    Prints diagnostics to `log` so you can see exactly what PIL returned.
    """
    log(f"  Opening: {image_path}")
    try:
        with Image.open(image_path) as img:
            exif_raw = img._getexif()
    except Exception as e:
        log(f"  ERROR opening image: {e}")
        return None

    if not exif_raw:
        log("  No EXIF data found.")
        return None

    gps = exif_raw.get(34853)
    if not gps:
        log("  GPS IFD (tag 34853) not present in EXIF.")
        return None

    log(f"  GPS IFD keys present: {sorted(gps.keys())}")

    if 2 not in gps or 4 not in gps:
        log("  GPSLatitude (2) or GPSLongitude (4) missing from GPS IFD.")
        return None

    lat_raw = gps[2]
    lon_raw = gps[4]
    lat_ref = gps.get(1, "N")
    lon_ref = gps.get(3, "E")

    log(f"  GPSLatitudeRef  : {lat_ref!r}")
    log(f"  GPSLatitude raw : {lat_raw!r}  (type: {type(lat_raw).__name__})")
    log(f"  GPSLongitudeRef : {lon_ref!r}")
    log(f"  GPSLongitude raw: {lon_raw!r}  (type: {type(lon_raw).__name__})")

    def _rat(v):
        if isinstance(v, tuple) and len(v) == 2:
            result = v[0] / v[1]
            log(f"    _rat({v!r}) → {result}  [num/denom tuple]")
            return result
        result = float(v)
        log(f"    _rat({v!r}) → {result}  [float/IFDRational]")
        return result

    def _scalar(vals):
        if hasattr(vals, '__len__') and len(vals) == 3:
            log(f"    Interpreting as DMS 3-element sequence")
            d = _rat(vals[0])
            m = _rat(vals[1])
            s = _rat(vals[2])
            deg = d + m / 60.0 + s / 3600.0
            log(f"    DMS → {d}° {m}' {s}\" = {deg}°")
            return deg
        if hasattr(vals, '__len__') and len(vals) == 1:
            log(f"    Interpreting as single-element decimal-degree wrapper")
            return _rat(vals[0])
        log(f"    Interpreting as scalar decimal degree")
        return _rat(vals)

    try:
        lat = _scalar(lat_raw)
        if str(lat_ref).upper() == "S":
            lat = -lat
        lon = _scalar(lon_raw)
        if str(lon_ref).upper() == "W":
            lon = -lon
    except Exception as e:
        log(f"  ERROR converting GPS values: {e}")
        return None

    log(f"  → Decimal degrees: lat={lat:.7f}  lon={lon:.7f}")
    return lat, lon


def _gps_from_rinex(rinex_path: str, log):
    """Parse APPROX POSITION XYZ from a RINEX obs header → (lat, lon)."""
    log(f"  Scanning RINEX header: {rinex_path}")
    try:
        with open(rinex_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if "APPROX POSITION XYZ" in line:
                    parts = line.split()
                    x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
                    log(f"  ECEF X={x}  Y={y}  Z={z}")
                    lat, lon = _ecef_to_geodetic(x, y, z)
                    log(f"  → Geodetic: lat={lat:.7f}  lon={lon:.7f}")
                    return lat, lon
                if "END OF HEADER" in line:
                    log("  APPROX POSITION XYZ not found before END OF HEADER.")
                    return None
    except Exception as e:
        log(f"  ERROR reading RINEX: {e}")
    return None


def _gps_from_t02(t02_path: str, log):
    """Convert T02/T04 → RINEX in a temp dir, then call _gps_from_rinex."""
    log(f"  T02 path: {t02_path}")
    if not os.path.isfile(CONVERT_TO_RINEX_EXE):
        log(f"  ERROR: convertToRINEX.exe not found at:\n    {CONVERT_TO_RINEX_EXE}")
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_src = os.path.join(tmp, os.path.basename(t02_path))
            shutil.copy2(t02_path, tmp_src)
            log(f"  Running convertToRINEX on temp copy...")
            result = subprocess.run(
                [CONVERT_TO_RINEX_EXE, tmp_src],
                capture_output=True,
                timeout=120,
            )
            log(f"  Converter exit code: {result.returncode}")
            obs_found = None
            for root, _, files in os.walk(tmp):
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    # Normalise year-prefixed RINEX ext, e.g. .25o → .o
                    norm = ("." + ext[3:]) if len(ext) > 3 and ext[1:3].isdigit() else ext
                    if norm in (".o", ".obs"):
                        log(f"  Found obs file: {fname}")
                        obs_found = os.path.join(root, fname)
                        break
                if obs_found:
                    break
            if not obs_found:
                log("  No RINEX obs file produced by converter.")
                return None
            return _gps_from_rinex(obs_found, log)
    except Exception as e:
        log(f"  ERROR during T02 conversion: {e}")
    return None


def _shp_polygons(shp_path):
    result = []
    with open(shp_path, "rb") as fh:
        fh.read(100)
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            rec_bytes = struct.unpack(">i", hdr[4:])[0] * 2
            data = fh.read(rec_bytes)
            if len(data) < 4:
                break
            stype = struct.unpack("<i", data[:4])[0]
            if stype not in (5, 15, 25, 31):
                result.append([])
                continue
            n_parts, n_pts = struct.unpack("<ii", data[36:44])
            parts = list(struct.unpack(f"<{n_parts}i", data[44:44 + 4 * n_parts]))
            # MultiPatch (31) has a PartTypes int32 array after Parts before Points
            base = 44 + 4 * n_parts + (4 * n_parts if stype == 31 else 0)
            flat = struct.unpack(f"<{n_pts * 2}d", data[base:base + n_pts * 16])
            pts = [(flat[i * 2], flat[i * 2 + 1]) for i in range(n_pts)]
            parts.append(n_pts)
            result.append([pts[parts[i]:parts[i + 1]] for i in range(n_parts)])
    return result


def _dbf_rows(dbf_path, want):
    rows = []
    with open(dbf_path, "rb") as fh:
        fh.read(4)
        n_recs = struct.unpack("<I", fh.read(4))[0]
        hdr_sz = struct.unpack("<H", fh.read(2))[0]
        rec_sz = struct.unpack("<H", fh.read(2))[0]
        fh.read(20)
        fields = []
        while True:
            desc = fh.read(32)
            if not desc or desc[0] in (0x0D, 0x1A):
                break
            name = desc[:11].rstrip(b"\x00").decode("ascii", errors="replace")
            fields.append((name, desc[16]))
        fh.seek(hdr_sz)
        for _ in range(n_recs):
            raw = fh.read(rec_sz)
            if not raw:
                break
            if raw[0] == 0x2A:
                rows.append(None)
                continue
            d, off = {}, 1
            for name, length in fields:
                val = raw[off:off + length].decode("ascii", errors="replace").strip()
                if name in want:
                    d[name] = val
                off += length
            rows.append(d)
    return rows


def _ray_cast(px, py, ring):
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[j]
        if (yi > py) != (yj > py):
            if px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def _epsg_from_latlon(lat: float, lon: float, log):
    """Query the State Plane shapefile with no third-party dependencies."""
    shp_path = STATEPLANE_SHAPEFILE
    dbf_path = os.path.splitext(shp_path)[0] + ".dbf"
    if not os.path.isfile(shp_path) or not os.path.isfile(dbf_path):
        log(f"  Shapefile not found:\n    {shp_path}")
        return None
    try:
        polys = _shp_polygons(shp_path)
        attrs = _dbf_rows(dbf_path, {"EPSG", "ZONENAME"})
        log(f"  Shapefile loaded — {len(polys)} zone records")

        for rings, attr in zip(polys, attrs):
            if not attr or not rings:
                continue
            hits = sum(1 for ring in rings if _ray_cast(lon, lat, ring))
            if hits % 2 == 1:
                epsg = str(int(float(attr["EPSG"])))
                name = attr["ZONENAME"]
                log(f"  → Zone: {name}  EPSG: {epsg}")
                return epsg, name
        log(f"  No zone found for lat={lat:.5f} lon={lon:.5f}")
    except Exception as e:
        import traceback
        log(f"  ERROR: {e}")
        log(traceback.format_exc())
    return None


# ── Test UI ───────────────────────────────────────────────────────────────────

def run_test():
    root = tk.Tk()
    root.title("GPS / EPSG Test Tool")
    root.geometry("780x560")
    root.resizable(True, True)

    # ── paths row ──
    frame_paths = tk.Frame(root, padx=10, pady=6)
    frame_paths.pack(fill=tk.X)

    img_var = tk.StringVar()
    t02_var = tk.StringVar()

    def browse_image():
        p = filedialog.askopenfilename(
            title="Select drone image",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.JPG *.JPEG"), ("All", "*.*")],
        )
        if p:
            img_var.set(os.path.normpath(p))

    def browse_t02():
        p = filedialog.askopenfilename(
            title="Select T02/T04 base file",
            filetypes=[("Trimble Raw", "*.T02 *.T04 *.t02 *.t04"), ("All", "*.*")],
        )
        if p:
            t02_var.set(os.path.normpath(p))

    tk.Label(frame_paths, text="Image:", width=7, anchor="w").grid(row=0, column=0, sticky="w")
    tk.Entry(frame_paths, textvariable=img_var, width=72).grid(row=0, column=1, padx=4)
    tk.Button(frame_paths, text="Browse", command=browse_image).grid(row=0, column=2)

    tk.Label(frame_paths, text="T02:", width=7, anchor="w").grid(row=1, column=0, sticky="w")
    tk.Entry(frame_paths, textvariable=t02_var, width=72).grid(row=1, column=1, padx=4)
    tk.Button(frame_paths, text="Browse", command=browse_t02).grid(row=1, column=2)

    # ── output area ──
    txt = scrolledtext.ScrolledText(root, font=("Consolas", 9), wrap=tk.WORD)
    txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

    def log(msg=""):
        txt.insert(tk.END, msg + "\n")
        txt.see(tk.END)
        root.update_idletasks()

    # ── run button ──
    def run():
        txt.delete("1.0", tk.END)

        img_path = img_var.get().strip()
        t02_path = t02_var.get().strip()

        if not img_path and not t02_path:
            log("Select at least one file to test.")
            return

        # ── Image test ──
        if img_path:
            log("=" * 60)
            log("IMAGE GPS TEST")
            log("=" * 60)
            if not os.path.isfile(img_path):
                log(f"File not found: {img_path}")
            else:
                coords = _gps_from_image(img_path, log)
                if coords:
                    lat, lon = coords
                    log()
                    log("STATE PLANE LOOKUP (from image)")
                    result = _epsg_from_latlon(lat, lon, log)
                    if result:
                        log(f"  Result: {result[1]}  (EPSG {result[0]})")
            log()

        # ── T02 test ──
        if t02_path:
            log("=" * 60)
            log("T02 BASE FILE GPS TEST")
            log("=" * 60)
            if not os.path.isfile(t02_path):
                log(f"File not found: {t02_path}")
            else:
                coords = _gps_from_t02(t02_path, log)
                if coords:
                    lat, lon = coords
                    log()
                    log("STATE PLANE LOOKUP (from T02)")
                    result = _epsg_from_latlon(lat, lon, log)
                    if result:
                        log(f"  Result: {result[1]}  (EPSG {result[0]})")
            log()

        log("Done.")

    btn_frame = tk.Frame(root, padx=10, pady=6)
    btn_frame.pack(fill=tk.X)
    tk.Button(btn_frame, text="Run Test", command=run, width=14,
              font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
    tk.Button(btn_frame, text="Clear", command=lambda: txt.delete("1.0", tk.END),
              width=8).pack(side=tk.LEFT, padx=6)
    tk.Button(btn_frame, text="Quit", command=root.destroy, width=8).pack(side=tk.RIGHT)

    root.mainloop()


if __name__ == "__main__":
    run_test()
