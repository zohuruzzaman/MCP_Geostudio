"""
SLOPE/W Strength Calibration - XML + GeoCmd Pattern
=====================================================
Back-analyses AWYC and WYC strength parameters (c', phi') against
the November 2020 failure event using a dual objective:

  cost = W_FS * (FS - 1.0)^2 + W_INCL * ((slip_depth - obs_depth) / obs_depth)^2

Pipeline per trial:
  1. Copy base GSZ to isolated temp folder
  2. Patch CohesionPrime / PhiPrime in root XML (element content, not attributes)
  3. Re-zip with correct entry names
  4. Solve with GeoCmd.exe (all 4 analyses: IC -> Rainfall Sim -> Slope Stability -> FS)
  5. Extract critical FS from FS/001/lambdafos_*.csv
  6. Extract slip circle geometry from FS/001/slip_surface.csv
  7. Compute slip surface depth at borehole X for inclinometer validation
  8. Evaluate dual cost function

Inclinometer data
-----------------
  CSV files with '@' delimiter separating A-direction (downslope) from B-direction.
  Columns: depth (ft) at 2-ft intervals, cumulative displacement (in) at each reading date.
  Shear zone depth = deepest depth where displacement > DISP_THRESHOLD at Nov 3 2020 reading.

Prerequisites
-------------
  - Base GSZ with calibrated SEEP/W parameters and Oct-Nov 2020 rainfall configured
  - GeoCmd.exe (GeoStudio 2024.2 or 2025.x)
  - Python 3.x with numpy, scipy

Usage
-----
    python calibrate_slope.py
"""

import sys, os, re, glob, shutil, zipfile, csv, io, subprocess, time, warnings
import numpy as np
from datetime import datetime
from scipy.optimize import minimize

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

CALIB_DIR   = r"E:\Github\MCP_Geostudio\calibration"

# Input: must have calibrated SEEP/W params + Oct-Nov 2020 rainfall already configured.
# The SEEP/W calibration (calibrate_seep.py) produces this file.
BASE_GSZ    = os.path.join(CALIB_DIR, "Metro-Center-seep-final.gsz")

# Output
OUT_GSZ     = os.path.join(CALIB_DIR, "Metro-Center-slope-final.gsz")
LOG_CSV     = os.path.join(CALIB_DIR, "slope_trial_log.csv")
TEMP_ROOT   = os.path.join(CALIB_DIR, "_slope_cal_temp")

# Inclinometer CSVs
INCL_Y1     = os.path.join(CALIB_DIR, "S2_Inclinometer_Y1.csv")
INCL_Y2     = os.path.join(CALIB_DIR, "S2_Inclinometer_Y2.csv")

# Analysis chain - GeoCmd solves all dependencies
FS_ANALYSIS = "FS"
ALL_ANALYSES = ["Slope Stability", "FS",
                "Initial Condition", "Rainfall Simulation"]

# Solver timeout per trial (seconds) - ~7 min for all 4 analyses
SOLVER_TIMEOUT = 600

# ---------------------------------------------------------------------------
# Model geometry
# ---------------------------------------------------------------------------

BOREHOLE_X  = 195.0    # ft - S2 sensor at crest platform
CREST_Y     = 83.0     # ft - crest surface elevation

# Inclinometer
FAILURE_DATE_STR = "11/3/2020"   # closest reading to Nov 2020 failure
DISP_THRESHOLD   = 0.005         # in - below this = no movement

# ---------------------------------------------------------------------------
# Baseline strength parameters (forensic study values)
# ---------------------------------------------------------------------------

C_AWYC_BASE   = 79.3     # psf
PHI_AWYC_BASE = 19.0     # degrees
C_WYC_BASE    = 248.5    # psf
PHI_WYC_BASE  = 19.0     # degrees

# Bounds for optimiser
C_AWYC_BOUNDS   = (0.0,  250.0)
PHI_AWYC_BOUNDS = (10.0,  25.0)
C_WYC_BOUNDS    = (50.0, 500.0)
PHI_WYC_BOUNDS  = (12.0,  25.0)

# ---------------------------------------------------------------------------
# Objective weights
# ---------------------------------------------------------------------------

FS_TARGET = 1.0
W_FS      = 1.0     # weight on (FS - 1.0)^2
W_INCL    = 0.5     # weight on normalised depth mismatch

MAX_OPT_ITER = 60

# Manual solver override - leave None for auto-detection
SOLVER_EXE_OVERRIDE = None


# ===========================================================================
# SOLVER DETECTION
# ===========================================================================

def find_solver():
    if SOLVER_EXE_OVERRIDE and os.path.exists(SOLVER_EXE_OVERRIDE):
        return SOLVER_EXE_OVERRIDE
    patterns = [
        r"C:\Program Files\Seequent\GeoStudio 2025*\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio 2024*\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio*\Bin\GeoCmd.exe",
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


# ===========================================================================
# INCLINOMETER PARSING
# ===========================================================================

def _parse_incl_a_direction(filepath):
    """
    Parse A-direction block from inclinometer CSV.
    CSV uses '@' delimiter between A and B measurement directions.

    Returns:
      profiles: {depth_ft: {date_str: displacement_in, ...}, ...}
      dates:    {date_str: column_index, ...}
    """
    with open(filepath, "r", encoding="utf-8-sig") as f:
        raw = f.read()

    lines = raw.splitlines()
    # A-direction is everything before '@' on each row
    a_lines = [line.split("@")[0].rstrip(",") for line in lines]

    # Row 3 (index 2) contains dates
    date_row = a_lines[2].split(",")
    dates = {}
    for i, d in enumerate(date_row):
        d = d.strip()
        if d and "/" in d:
            dates[d] = i

    # Data rows start at index 4 (row 5)
    # Index 3 is a duplicate "1" header row, index 4 onward is depth=1,3,5,...
    profiles = {}
    for line in a_lines[4:]:
        parts = line.split(",")
        if not parts or not parts[0].strip():
            break
        try:
            depth = float(parts[0])
        except ValueError:
            break
        profiles[depth] = {}
        for date_str, col_idx in dates.items():
            try:
                if col_idx < len(parts) and parts[col_idx].strip():
                    profiles[depth][date_str] = float(parts[col_idx])
            except (ValueError, IndexError):
                pass

    return profiles, dates


def _shear_zone_depth(profiles, date_str, threshold):
    """
    Find deepest depth where cumulative displacement > threshold.
    Scans from bottom up; first depth exceeding threshold = shear zone.
    """
    depths_sorted = sorted(profiles.keys(), reverse=True)   # deep -> shallow
    for d in depths_sorted:
        disp = profiles[d].get(date_str)
        if disp is not None and abs(disp) > threshold:
            return d
    return None


def load_inclinometer():
    """
    Parse both inclinometer CSV files and return observed shear zone
    depth (ft below surface) at the failure date.

    Takes the deeper (more conservative) estimate from Y1/Y2.
    """
    shear_depths = []

    for fpath, label in [(INCL_Y1, "Y1"), (INCL_Y2, "Y2")]:
        if not os.path.exists(fpath):
            print(f"  WARNING: inclinometer file not found: {fpath}")
            continue

        profiles, dates = _parse_incl_a_direction(fpath)
        if FAILURE_DATE_STR not in dates:
            print(f"  WARNING: {label} has no reading for {FAILURE_DATE_STR}")
            continue

        sd = _shear_zone_depth(profiles, FAILURE_DATE_STR, DISP_THRESHOLD)
        if sd is None:
            print(f"  WARNING: {label} - no shear zone detected")
            continue

        max_depth = max(profiles.keys())
        print(f"  {label}: shear zone depth = {sd:.0f} ft  "
              f"(max instrumented depth = {max_depth:.0f} ft)")

        # Print displacement profile at failure date
        for depth in sorted(profiles.keys()):
            disp = profiles[depth].get(FAILURE_DATE_STR)
            if disp is not None:
                marker = " <-- shear zone" if depth == sd else ""
                below = " *" if abs(disp) <= DISP_THRESHOLD else ""
                print(f"    {depth:4.0f} ft  {disp:.6f} in{marker}{below}")

        shear_depths.append(sd)

    if not shear_depths:
        return None

    obs_depth = max(shear_depths)   # use deeper / more conservative
    print(f"\n  Observed shear zone depth : {obs_depth:.1f} ft  "
          f"(SHEAR_Y = {CREST_Y - obs_depth:.1f} ft in model coords)")
    return obs_depth


# ===========================================================================
# XML PATCHING - STRENGTH PARAMETERS
# ===========================================================================

def _patch_strength_param(xml_text, material_name, param_tag, new_value):
    """
    Patch element content like <CohesionPrime>79.3</CohesionPrime>
    within the <Material> block identified by material_name.

    material_name: exact string appearing as >Name< in XML
    param_tag:     "CohesionPrime" or "PhiPrime"
    new_value:     float
    """
    marker = f">{material_name}<"
    start = xml_text.find(marker)
    if start == -1:
        print(f"  x FAILED: material '{material_name}' not found in XML")
        return xml_text

    mat_end = xml_text.find("</Material>", start)
    if mat_end == -1:
        mat_end = start + 3000   # fallback search window

    # Find the tag within this material block
    open_tag  = f"<{param_tag}>"
    close_tag = f"</{param_tag}>"

    tag_start = xml_text.find(open_tag, start)
    if tag_start == -1 or tag_start > mat_end:
        print(f"  x FAILED: <{param_tag}> not found within '{material_name}'")
        return xml_text

    tag_end = xml_text.find(close_tag, tag_start)
    if tag_end == -1 or tag_end > mat_end:
        print(f"  x FAILED: </{param_tag}> not found within '{material_name}'")
        return xml_text

    # Extract old value for logging
    old_value = xml_text[tag_start + len(open_tag):tag_end]

    # Replace
    replacement = f"{open_tag}{new_value:.6g}{close_tag}"
    xml_text = (xml_text[:tag_start] + replacement
                + xml_text[tag_end + len(close_tag):])

    print(f"  OK {material_name}: {param_tag} {old_value} -> {new_value:.6g}")
    return xml_text


def patch_all_strength(xml_text, c_awyc, phi_awyc, c_wyc, phi_wyc):
    """Patch c' and phi' for both AWYC and WYC in the root XML."""
    xml_text = _patch_strength_param(
        xml_text, "Weathered Yazoo Clay in Active Zone",
        "CohesionPrime", c_awyc)
    xml_text = _patch_strength_param(
        xml_text, "Weathered Yazoo Clay in Active Zone",
        "PhiPrime", phi_awyc)
    xml_text = _patch_strength_param(
        xml_text, "Weathered Yazoo Clay",
        "CohesionPrime", c_wyc)
    xml_text = _patch_strength_param(
        xml_text, "Weathered Yazoo Clay",
        "PhiPrime", phi_wyc)
    return xml_text


# ===========================================================================
# TEMP GSZ CREATION + SOLVE
# ===========================================================================

def create_work_gsz(trial_idx, c_awyc, phi_awyc, c_wyc, phi_wyc):
    """
    Copy base GSZ to temp folder, patch strength params, re-zip.
    Returns (work_gsz_path, temp_dir).
    """
    temp_dir = os.path.join(TEMP_ROOT, f"trial_{trial_idx:03d}")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    gsz_name = os.path.basename(BASE_GSZ)
    gsz_stem = os.path.splitext(gsz_name)[0]
    work_gsz = os.path.join(temp_dir, gsz_name)

    # Read original archive
    with zipfile.ZipFile(BASE_GSZ, 'r') as zin:
        all_items = zin.infolist()
        all_data  = {item.filename: zin.read(item.filename) for item in all_items}

    # Find root XML (not under analysis subfolders)
    root_xml_key = None
    for key in all_data:
        if (key.endswith(gsz_stem + ".xml")
                and not any(key.startswith(af + "/")
                            for af in ALL_ANALYSES)):
            root_xml_key = key
            break

    if root_xml_key is None:
        # Try any XML not in a subfolder
        for key in all_data:
            if key.endswith(".xml") and "/" not in key:
                root_xml_key = key
                break

    if root_xml_key is None:
        raise RuntimeError(
            f"No root XML found in {BASE_GSZ}. "
            f"Entries: {list(all_data.keys())[:15]}")

    # Patch strength parameters
    xml_text = all_data[root_xml_key].decode("utf-8", errors="replace")
    xml_text = patch_all_strength(xml_text, c_awyc, phi_awyc, c_wyc, phi_wyc)

    # Re-zip with correct entry names
    # PyGeoStudio or prior scripts may have stored XML with full absolute paths.
    # GeoCmd expects short names: "Stem.xml", "FS/Stem.xml", etc.
    with zipfile.ZipFile(work_gsz, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data  = all_data[fname]

            # Fix XML entry names (strip absolute paths)
            if gsz_stem + ".xml" in fname:
                fixed_name = gsz_stem + ".xml"
                for af in ALL_ANALYSES:
                    if fname.startswith(af + "/") or ("/" + af + "/") in fname:
                        fixed_name = af + "/" + gsz_stem + ".xml"
                        break
                item.filename = fixed_name
                if fixed_name == gsz_stem + ".xml":
                    data = xml_text.encode("utf-8")

            zout.writestr(item, data)

    return work_gsz, temp_dir


def run_solver(solver_exe, work_gsz):
    """Run GeoCmd.exe /solve and return (success, elapsed_seconds, stderr)."""
    t0 = time.time()
    try:
        result = subprocess.run(
            [solver_exe, "/solve", work_gsz],
            capture_output=True,
            text=True,
            timeout=SOLVER_TIMEOUT,
        )
        dt = time.time() - t0
        if result.returncode != 0:
            return False, dt, (result.stderr or result.stdout)[:300]
        return True, dt, ""
    except subprocess.TimeoutExpired:
        return False, SOLVER_TIMEOUT, "TIMEOUT"
    except Exception as e:
        return False, time.time() - t0, str(e)


# ===========================================================================
# FS EXTRACTION
# ===========================================================================

def extract_fs(gsz_path, analysis_name="FS"):
    """
    Read critical FS from lambdafos_*.csv inside the solved archive.
    Converged FS = row where |FOSByForce - FOSByMoment| is minimised.
    Returns average of force and moment FS at convergence.
    """
    with zipfile.ZipFile(gsz_path, 'r') as z:
        files  = z.namelist()
        prefix = analysis_name + "/"
        # Find lambdafos CSV(s) - could be in 001/ subfolder
        lf_files = [f for f in files
                    if f.startswith(prefix) and "lambdafos_" in f
                    and f.endswith(".csv")]
        if not lf_files:
            return None

        with z.open(lf_files[0]) as f:
            lines = f.read().decode("utf-8", errors="replace").splitlines()

    reader = csv.DictReader(lines)
    best, best_diff = None, float("inf")
    for row in reader:
        try:
            ff = float(row["FOSByForce"])
            fm = float(row["FOSByMoment"])
            d  = abs(ff - fm)
            if d < best_diff:
                best_diff = d
                best = (ff + fm) / 2.0
        except (ValueError, KeyError):
            continue
    return best


# ===========================================================================
# SLIP GEOMETRY EXTRACTION
# ===========================================================================

def extract_slip_geometry(gsz_path, analysis_name="FS"):
    """
    Read slip circle geometry from slip_surface.csv.
    Columns: SlipNum, SlipFOS, SlipCenterX, SlipCenterY, SlipRadiusX, ...

    Returns dict for the critical (minimum FOS) slip surface:
      {fos, center_x, center_y, radius}
    or None if not found.
    """
    with zipfile.ZipFile(gsz_path, 'r') as z:
        files  = z.namelist()
        prefix = analysis_name + "/"
        ss_files = [f for f in files
                    if f.startswith(prefix) and "slip_surface" in f
                    and f.endswith(".csv")]
        if not ss_files:
            return None

        with z.open(ss_files[0]) as f:
            lines = f.read().decode("utf-8", errors="replace").splitlines()

    reader = csv.DictReader(lines)
    best = None
    best_fos = float("inf")

    for row in reader:
        try:
            fos = float(row["SlipFOS"])
            cx  = float(row["SlipCenterX"])
            cy  = float(row["SlipCenterY"])
            # SlipRadiusX is the radius (distance from center to slip arc)
            r   = float(row["SlipRadiusX"])
        except (ValueError, KeyError):
            continue

        if 0.01 < fos < 100 and fos < best_fos:
            best_fos = fos
            best = {"fos": fos, "center_x": cx, "center_y": cy, "radius": r}

    return best


def compute_slip_depth_at_borehole(slip_geom):
    """
    Given a circular slip surface (center_x, center_y, radius),
    compute the depth of intersection at BOREHOLE_X.

    Returns (depth_ft, info_dict).
    depth_ft: depth below CREST_Y where slip circle intersects borehole vertical.
              None if borehole is outside the slip circle.
    """
    if slip_geom is None:
        return None, {}

    cx = slip_geom["center_x"]
    cy = slip_geom["center_y"]
    r  = slip_geom["radius"]

    info = {
        "cx": round(cx, 2),
        "cy": round(cy, 2),
        "radius": round(r, 2),
    }

    dx = BOREHOLE_X - cx

    if abs(dx) > r:
        # Borehole is outside the slip circle
        gap = abs(dx) - r
        info["outside_circle"] = True
        info["lateral_gap_ft"] = round(gap, 2)
        return None, info

    # Borehole is inside: slip surface Y at borehole X
    # For a circle centered at (cx, cy) with radius r:
    #   (x - cx)^2 + (y - cy)^2 = r^2
    #   y = cy - sqrt(r^2 - dx^2)   (lower intersection = failure surface)
    slip_y = cy - np.sqrt(r**2 - dx**2)
    depth  = CREST_Y - slip_y

    info["slip_y_at_borehole"] = round(slip_y, 2)
    info["depth_at_borehole"]  = round(depth, 2)

    return max(0.0, depth), info


# ===========================================================================
# CLEANUP
# ===========================================================================

def cleanup_temp(temp_dir):
    """Remove temp folder with retry for Windows file locks."""
    for attempt in range(10):
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            return
        except Exception:
            time.sleep(1)


# ===========================================================================
# OBJECTIVE FUNCTION
# ===========================================================================

_iter_count = [0]
_trial_log  = []
_solver_exe = [None]   # set in main()
_obs_depth  = [None]   # set in main()


def objective(params):
    """Dual-objective cost function for Nelder-Mead."""
    c_awyc  = float(np.clip(params[0], *C_AWYC_BOUNDS))
    phi_awyc = float(np.clip(params[1], *PHI_AWYC_BOUNDS))
    c_wyc   = float(np.clip(params[2], *C_WYC_BOUNDS))
    phi_wyc  = float(np.clip(params[3], *PHI_WYC_BOUNDS))

    _iter_count[0] += 1
    idx = _iter_count[0]

    print(f"  Trial {idx:3d}: "
          f"c_AWYC={c_awyc:7.1f}  phi_AWYC={phi_awyc:5.2f}  "
          f"c_WYC={c_wyc:7.1f}  phi_WYC={phi_wyc:5.2f}",
          end="", flush=True)

    temp_dir = None
    try:
        # 1. Build patched GSZ
        work_gsz, temp_dir = create_work_gsz(
            idx, c_awyc, phi_awyc, c_wyc, phi_wyc)

        # 2. Solve
        ok, dt, err_msg = run_solver(_solver_exe[0], work_gsz)
        if not ok:
            print(f"  -> SOLVER FAILED ({dt:.0f}s): {err_msg[:80]}")
            _log_trial(idx, c_awyc, phi_awyc, c_wyc, phi_wyc,
                       None, None, None, 9999.0, dt, "SOLVER_FAIL")
            return 9999.0

        # 3. Extract FS
        fs = extract_fs(work_gsz)

        # 4. Extract slip geometry + depth at borehole
        slip_geom  = extract_slip_geometry(work_gsz)
        slip_depth, slip_info = compute_slip_depth_at_borehole(slip_geom)

        # 5. Compute cost
        # FS term
        if fs is None:
            fs_cost = 9999.0
        else:
            fs_cost = W_FS * (fs - FS_TARGET) ** 2

        # Inclinometer depth term
        incl_cost = 0.0
        obs = _obs_depth[0]
        if obs is not None:
            if slip_depth is not None:
                incl_cost = W_INCL * ((slip_depth - obs) / max(obs, 1.0)) ** 2
            elif slip_info.get("lateral_gap_ft") is not None:
                # Borehole outside circle: penalise lateral gap
                gap_norm = slip_info["lateral_gap_ft"] / max(BOREHOLE_X, 1.0)
                incl_cost = W_INCL * gap_norm ** 2

        cost = fs_cost + incl_cost

        # Log
        fs_str = f"{fs:.4f}" if fs else "N/A"
        depth_str = (f"{slip_depth:.1f}ft" if slip_depth is not None
                     else f"outside(gap={slip_info.get('lateral_gap_ft', '?')}ft)")
        print(f"  -> FS={fs_str}  depth@BH={depth_str}  "
              f"cost={cost:.5f}  ({dt:.0f}s)")

        _log_trial(idx, c_awyc, phi_awyc, c_wyc, phi_wyc,
                   fs, slip_depth, slip_info, cost, dt, "OK")
        return cost

    except Exception as e:
        print(f"  -> FAILED: {e}")
        _log_trial(idx, c_awyc, phi_awyc, c_wyc, phi_wyc,
                   None, None, {}, 9999.0, 0, str(e)[:80])
        return 9999.0

    finally:
        if temp_dir:
            cleanup_temp(temp_dir)


def _log_trial(idx, c_awyc, phi_awyc, c_wyc, phi_wyc,
               fs, slip_depth, slip_info, cost, dt, status):
    _trial_log.append({
        "trial":      idx,
        "c_awyc":     round(c_awyc, 2),
        "phi_awyc":   round(phi_awyc, 3),
        "c_wyc":      round(c_wyc, 2),
        "phi_wyc":    round(phi_wyc, 3),
        "fs":         round(fs, 5) if fs else None,
        "slip_depth_ft":  round(slip_depth, 2) if slip_depth else None,
        "obs_depth_ft":   round(_obs_depth[0], 1) if _obs_depth[0] else None,
        "slip_cx":    slip_info.get("cx") if slip_info else None,
        "slip_cy":    slip_info.get("cy") if slip_info else None,
        "slip_r":     slip_info.get("radius") if slip_info else None,
        "cost":       round(cost, 6),
        "solve_s":    round(dt, 1),
        "status":     status,
    })


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("=" * 70)
    print("SLOPE/W Strength Calibration - XML + GeoCmd")
    print(f"  Base GSZ      : {os.path.basename(BASE_GSZ)}")
    print(f"  Failure date  : {FAILURE_DATE_STR}")
    print(f"  Borehole X    : {BOREHOLE_X} ft")
    print(f"  FS target     : {FS_TARGET}")
    print(f"  Weights       : W_FS={W_FS}  W_INCL={W_INCL}")
    print(f"  Max trials    : {MAX_OPT_ITER}")
    print("=" * 70)

    # ---- Check files ----
    if not os.path.exists(BASE_GSZ):
        print(f"\nERROR: Base GSZ not found: {BASE_GSZ}")
        print("Run SEEP/W calibration first to produce this file.")
        sys.exit(1)

    # ---- Find solver ----
    solver = find_solver()
    if solver is None:
        print("\nERROR: GeoCmd.exe not found.")
        print("Set SOLVER_EXE_OVERRIDE at the top of this script.")
        sys.exit(1)
    _solver_exe[0] = solver
    print(f"\n  Solver: {solver}")

    # ---- Verify base GSZ structure ----
    print("\nVerifying base GSZ structure...")
    with zipfile.ZipFile(BASE_GSZ, 'r') as z:
        all_files = z.namelist()
        has_fs_results = any("FS/" in f and "lambdafos_" in f for f in all_files)
        has_slip_csv   = any("FS/" in f and "slip_surface" in f for f in all_files)
        xml_files = [f for f in all_files if f.endswith(".xml") and "/" not in f]
        print(f"  Root XML entries: {xml_files}")
        print(f"  FS/lambdafos_*: {'found' if has_fs_results else 'NOT found'}")
        print(f"  FS/slip_surface: {'found' if has_slip_csv else 'NOT found'}")

    # Quick baseline FS check from existing results
    base_fs = extract_fs(BASE_GSZ)
    if base_fs:
        print(f"  Baseline FS (from existing results): {base_fs:.4f}")
    base_slip = extract_slip_geometry(BASE_GSZ)
    if base_slip:
        print(f"  Baseline slip: center=({base_slip['center_x']:.1f}, "
              f"{base_slip['center_y']:.1f})  R={base_slip['radius']:.1f}")
        depth0, info0 = compute_slip_depth_at_borehole(base_slip)
        if depth0:
            print(f"  Baseline slip depth at BH: {depth0:.1f} ft")

    # ---- Parse inclinometer ----
    print("\nLoading inclinometer data...")
    obs_depth = load_inclinometer()
    _obs_depth[0] = obs_depth

    if obs_depth is None:
        print("\nWARNING: could not determine shear zone depth.")
        print("Running with FS-only objective (W_INCL overridden to 0).")
        # Will still work - incl_cost stays 0 in objective()

    # ---- Verify XML strength tags exist ----
    print("\nVerifying XML structure for strength patching...")
    gsz_stem = os.path.splitext(os.path.basename(BASE_GSZ))[0]
    with zipfile.ZipFile(BASE_GSZ, 'r') as z:
        root_xml = None
        for name in z.namelist():
            if name.endswith(".xml") and "/" not in name:
                root_xml = name
                break
        if root_xml:
            xml_text = z.read(root_xml).decode("utf-8", errors="replace")
            for mat_name in ["Weathered Yazoo Clay in Active Zone",
                             "Weathered Yazoo Clay"]:
                marker = f">{mat_name}<"
                if marker in xml_text:
                    # Find CohesionPrime and PhiPrime
                    pos = xml_text.find(marker)
                    mat_end = xml_text.find("</Material>", pos)
                    block = xml_text[pos:mat_end] if mat_end > pos else ""
                    c_match = re.search(
                        r"<CohesionPrime>([^<]*)</CohesionPrime>", block)
                    p_match = re.search(
                        r"<PhiPrime>([^<]*)</PhiPrime>", block)
                    print(f"  {mat_name}:")
                    print(f"    CohesionPrime = "
                          f"{c_match.group(1) if c_match else 'NOT FOUND'}")
                    print(f"    PhiPrime      = "
                          f"{p_match.group(1) if p_match else 'NOT FOUND'}")
                else:
                    print(f"  WARNING: '{mat_name}' not found in XML!")

    # ---- Prepare temp root ----
    if os.path.exists(TEMP_ROOT):
        shutil.rmtree(TEMP_ROOT)

    # ---- Starting parameters ----
    x0 = np.array([C_AWYC_BASE, PHI_AWYC_BASE, C_WYC_BASE, PHI_WYC_BASE])
    print(f"\nStarting parameters (forensic study baseline):")
    print(f"  c_AWYC  = {x0[0]:>8.1f} psf   bounds [{C_AWYC_BOUNDS[0]}, {C_AWYC_BOUNDS[1]}]")
    print(f"  phi_AWYC = {x0[1]:>7.2f} deg   bounds [{PHI_AWYC_BOUNDS[0]}, {PHI_AWYC_BOUNDS[1]}]")
    print(f"  c_WYC   = {x0[2]:>8.1f} psf   bounds [{C_WYC_BOUNDS[0]}, {C_WYC_BOUNDS[1]}]")
    print(f"  phi_WYC  = {x0[3]:>7.2f} deg   bounds [{PHI_WYC_BOUNDS[0]}, {PHI_WYC_BOUNDS[1]}]")
    if obs_depth:
        print(f"\n  Inclinometer target: slip depth = {obs_depth:.1f} ft at "
              f"X = {BOREHOLE_X}")

    # ---- Optimise ----
    print(f"\nRunning optimisation (max {MAX_OPT_ITER} trials)...\n")
    t_start = time.time()

    result = minimize(
        objective,
        x0,
        method="Nelder-Mead",
        options={
            "maxiter": MAX_OPT_ITER,
            "xatol":   0.5,       # 0.5 psf or 0.5 degrees
            "fatol":   1e-4,
            "disp":    True,
        },
    )

    t_total = time.time() - t_start

    # ---- Pick best from log ----
    valid = [t for t in _trial_log
             if t["fs"] is not None and t["status"] == "OK"]
    if valid:
        best = min(valid, key=lambda t: t["cost"])
    else:
        print("\nERROR: No valid trials completed!")
        sys.exit(1)

    # ---- Summary ----
    print(f"\n{'=' * 70}")
    print("Optimisation complete")
    print(f"  Status     : {result.message}")
    print(f"  Trials     : {_iter_count[0]}   (valid: {len(valid)})")
    print(f"  Total time : {t_total/60:.1f} min")

    if best["fs"]:
        print(f"  Best FS    : {best['fs']:.4f}   |FS-1.0| = "
              f"{abs(best['fs'] - FS_TARGET):.4f}")
    if best["slip_depth_ft"] and obs_depth:
        print(f"  Slip depth : {best['slip_depth_ft']:.1f} ft  "
              f"(observed {obs_depth:.1f} ft, "
              f"error {best['slip_depth_ft'] - obs_depth:+.1f} ft)")

    print(f"\n  {'Parameter':<22} {'Baseline':>12}  {'Calibrated':>12}  {'Change':>10}")
    print(f"  {'-'*62}")
    for lbl, base, cal in [
        ("c_AWYC (psf)",   C_AWYC_BASE,   best["c_awyc"]),
        ("phi_AWYC (deg)", PHI_AWYC_BASE,  best["phi_awyc"]),
        ("c_WYC (psf)",    C_WYC_BASE,     best["c_wyc"]),
        ("phi_WYC (deg)",  PHI_WYC_BASE,   best["phi_wyc"]),
    ]:
        pct = ((cal - base) / base * 100) if base != 0 else 0
        print(f"  {lbl:<22} {base:>12.2f}  {cal:>12.2f}  "
              f"{cal - base:>+10.2f} ({pct:+.1f}%)")

    # ---- Save trial log ----
    if _trial_log:
        with open(LOG_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_trial_log[0].keys())
            writer.writeheader()
            writer.writerows(_trial_log)
        print(f"\n  Trial log  -> {LOG_CSV}")

    # ---- Final solve with best params -> OUT_GSZ ----
    print(f"\nRunning final solve with best parameters...")
    work_gsz, temp_dir = create_work_gsz(
        0, best["c_awyc"], best["phi_awyc"],
        best["c_wyc"], best["phi_wyc"])

    ok, dt, err = run_solver(solver, work_gsz)
    if ok:
        shutil.copy2(work_gsz, OUT_GSZ)
        final_fs = extract_fs(OUT_GSZ)
        final_slip = extract_slip_geometry(OUT_GSZ)
        final_depth, _ = compute_slip_depth_at_borehole(final_slip)
        print(f"  Final FS         = {final_fs:.4f}" if final_fs else
              "  Final FS = unreadable")
        if final_depth:
            print(f"  Final slip depth = {final_depth:.1f} ft at borehole")
        print(f"  Saved -> {OUT_GSZ}")
    else:
        print(f"  Final solve FAILED: {err}")
        print("  Saving patched (unsolved) GSZ anyway...")
        shutil.copy2(work_gsz, OUT_GSZ)

    cleanup_temp(temp_dir)

    # ---- Cleanup temp root ----
    if os.path.exists(TEMP_ROOT):
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)

    print(f"\nDone.")


if __name__ == "__main__":
    main()