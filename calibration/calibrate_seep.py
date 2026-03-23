"""
SEEP/W Hydraulic Calibration
=============================
Calibrates Ksat and anisotropy ratio against observed volumetric water
content from the S2 moisture sensors during the Oct-Nov 2020 calibration event.

Parameters being calibrated:
  - KSat_WYC  : saturated hydraulic conductivity of Seep WYC (ft/s)
  - KSat_AWYC : scale factor applied to Seep AWYC KYXRatio
                (anisotropy ratio, currently 11155)

Calibration target:
  - Observed VWC at 1.5m and 3m depth, Crest sensor, Oct 1 - Nov 30 2020
  - Minimise RMSE between simulated and observed at each survey timestep

Approach:
  1. Extract rainfall and VWC observations from S2.csv for calibration window
  2. For each parameter trial: copy GSZ, patch XML, run solver, read nodal VWC
  3. scipy.optimize (Nelder-Mead) minimises RMSE
  4. Save calibrated parameters and comparison plot data to CSV

Usage:
    python calibrate_seep.py
"""

import sys, os, glob, shutil, zipfile, csv, re, warnings, subprocess, time
import numpy as np
import pandas as pd
from datetime import datetime
from scipy.optimize import minimize

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIG - adjust these paths / values if needed
# ---------------------------------------------------------------------------

CALIB_DIR       = r"E:\Github\MCP_Geostudio\calibration"
BASE_GSZ        = os.path.join(CALIB_DIR, "Metro-Center_cal.gsz")
SENSOR_CSV      = os.path.join(CALIB_DIR, "S2.csv")
INCL_Y1         = os.path.join(CALIB_DIR, "S2_Inclinometer_Y1.xlsx")
INCL_Y2         = os.path.join(CALIB_DIR, "S2_Inclinometer_Y2.xlsx")

OUT_DIR         = CALIB_DIR
OUT_CSV         = os.path.join(OUT_DIR, "calibration_results.csv")

# Calibration event window
CALIB_START     = "2020-10-01"
CALIB_END       = "2020-11-30"

# Sensor location in model coordinates (ft)
# Crest flat area centre - adjust if you know exact x from the model
CREST_X         = 195.0
SURFACE_Y       = 83.0      # surface elevation at crest (ft)
DEPTH_1_5M      = 4.92      # 1.5m in feet
DEPTH_3M        = 9.84      # 3m in feet

SENSOR_Y_SHALLOW = SURFACE_Y - DEPTH_1_5M   # ~78 ft
SENSOR_Y_DEEP    = SURFACE_Y - DEPTH_3M     # ~73 ft

# SEEP/W analysis names
SEEP_INITIAL    = "Initial Condition"
SEEP_TRANSIENT  = "Rainfall Simulation"

# Current baseline hydraulic parameter values (from XML probe)
KSAT_WYC_BASE        = 1.004e-07   # ft/s - Seep WYC saturated Ksat
KYX_RATIO_AWYC_BASE  = 11155.0     # Seep AWYC anisotropy ratio
KSAT_UYC_BASE        = 1.004e-07   # ft/s - Seep UYC saturated Ksat (same prior as WYC)
# NOTE: Seep SC uses a K-function (KFnNum=1) not a scalar KSat — calibration of SC
# requires scaling all K-fn points (more complex); deferred to a later step.

# Optimiser bounds (log10 space for Ksat, linear for ratio)
# Ksat WYC: allow 2 orders of magnitude either side
LOG_KSAT_WYC_MIN     = np.log10(KSAT_WYC_BASE) - 2.0
LOG_KSAT_WYC_MAX     = np.log10(KSAT_WYC_BASE) + 2.0
# KYX ratio: allow 10x down or 10x up
LOG_KYX_MIN          = np.log10(1000)
LOG_KYX_MAX          = np.log10(100000)
# Ksat UYC: allow 2 orders of magnitude either side (deep confining layer)
LOG_KSAT_UYC_MIN     = np.log10(KSAT_UYC_BASE) - 2.0
LOG_KSAT_UYC_MAX     = np.log10(KSAT_UYC_BASE) + 2.0

# Max solver iterations for optimisation
# Nelder-Mead with 3 params needs (n+1)=4 simplex vertices → min ~12 trials just
# to build the simplex; 30 gives ~6-8 real improvement cycles.
MAX_OPT_ITER    = 30
SOLVER_TIMEOUT  = 1800  # seconds per GeoStudio solve

sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')

# ---------------------------------------------------------------------------
# Solver detection
# ---------------------------------------------------------------------------

def find_solver():
    patterns = [
        r"C:\Program Files\Seequent\GeoStudio 2024.2\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio*\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio*\GeoCmd.exe",
        r"C:\Program Files\GeoSlope\GeoStudio*\GeoCmd.exe",
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


# ---------------------------------------------------------------------------
# Load and prepare sensor observations
# ---------------------------------------------------------------------------

def load_sensor_data():
    """Load S2.csv and extract Crest suction + moisture + rainfall for calibration window."""
    df = pd.read_csv(SENSOR_CSV, low_memory=False)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    for col in ["Moisture_1.5m", "Moisture_3m", "Suction_1.5m", "Suction_3m", "Precipitation"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    crest = df[df["position"] == "Crest"].copy()
    crest = crest.set_index("timestamp")

    # Resample to daily
    daily = crest[["Moisture_1.5m", "Moisture_3m", "Suction_1.5m", "Suction_3m", "Precipitation"]].resample("D").agg({
        "Moisture_1.5m": "mean",
        "Moisture_3m":   "mean",
        "Suction_1.5m":  "mean",
        "Suction_3m":    "mean",
        "Precipitation": "sum",
    })

    # Clip to calibration window - require moisture data; suction optional
    daily = daily.loc[CALIB_START:CALIB_END].dropna(subset=["Moisture_1.5m", "Moisture_3m"])

    print(f"  Sensor calibration window: {daily.index[0].date()} to {daily.index[-1].date()}")
    print(f"  Days with data: {len(daily)}")
    print(f"  Mean VWC 1.5m:     {daily['Moisture_1.5m'].mean():.3f}")
    print(f"  Mean VWC 3m:       {daily['Moisture_3m'].mean():.3f}")
    print(f"  Mean suction 1.5m: {daily['Suction_1.5m'].mean():.3f} kPa")
    print(f"  Mean suction 3m:   {daily['Suction_3m'].mean():.3f} kPa")
    print(f"  Total precip:      {daily['Precipitation'].sum():.0f} mm")

    return daily


def build_rainfall_time_series(daily):
    """
    Aggregate daily precipitation to weekly totals and convert to GeoStudio
    rainfall function format (in/day equivalent, Step function).
    GeoStudio uses: X = elapsed seconds from simulation start, Y = in/day.
    Weekly aggregation reduces the number of solver timesteps from 61 → ~9,
    which keeps each trial solve under ~5 minutes for a 61-day window.
    """
    mm_to_in = 1.0 / 25.4
    weekly = daily["Precipitation"].resample("W").sum()
    points = []
    t0 = daily.index[0]
    for ts, total_mm in weekly.items():
        # Use end-of-week timestamp; X = elapsed seconds from calib start
        elapsed_s = int((ts - t0).total_seconds())
        # Convert weekly total mm → average in/day for the 7-day window
        rain_in_per_day = (total_mm * mm_to_in) / 7.0
        points.append({"X": str(elapsed_s), "Y": rain_in_per_day})
    return points


# ---------------------------------------------------------------------------
# XML modification helpers
# ---------------------------------------------------------------------------

def patch_ksat_wyc(xml_str, new_ksat):
    """Replace KSat value for Seep WYC (SatOnly scalar form)."""
    # Pattern: <Hydraulic KSat="..." VolWC="0.5" Beta="0.00104" />
    pattern = r'(<Hydraulic KSat=")[^"]*(" VolWC="0\.5" Beta="0\.00104" />)'
    replacement = rf'\g<1>{new_ksat:.6e}\g<2>'
    new_xml, count = re.subn(pattern, replacement, xml_str)
    if count == 0:
        print("  WARNING: KSat WYC pattern not found in XML")
    return new_xml


def patch_kyx_ratio_awyc(xml_str, new_ratio):
    """Replace KYXRatio for Seep AWYC."""
    pattern = r'(<Hydraulic KYXRatio=")[^"]*(" KFnNum="2" VolWCFnNum="2" />)'
    replacement = rf'\g<1>{new_ratio:.1f}\g<2>'
    new_xml, count = re.subn(pattern, replacement, xml_str)
    if count == 0:
        print("  WARNING: KYXRatio AWYC pattern not found in XML")
    return new_xml


def patch_ksat_uyc(xml_str, new_ksat):
    """Replace KSat value for Seep UYC (SatOnly, VolWC=0.45)."""
    pattern = r'(<Hydraulic KSat=")[^"]*(" VolWC="0\.45" Beta="0\.00104" />)'
    replacement = rf'\g<1>{new_ksat:.6e}\g<2>'
    new_xml, count = re.subn(pattern, replacement, xml_str)
    if count == 0:
        print("  WARNING: KSat UYC pattern not found in XML")
    return new_xml


def patch_rainfall_xml(xml_str, rain_points):
    """Replace the rainfall function Y values with new calibration event data."""
    start_tag = "<Name>rainfall</Name>"
    pos = xml_str.find(start_tag)
    if pos == -1:
        print("  WARNING: rainfall function not found in XML")
        return xml_str
    pts_open  = xml_str.find("<Points", pos)
    pts_close = xml_str.find("</Points>", pts_open) + len("</Points>")
    if pts_open == -1 or pts_close <= len("</Points>"):
        return xml_str
    new_pts = f'<Points Len="{len(rain_points)}">\n'
    for pt in rain_points:
        new_pts += f'            <Point X="{pt["X"]}" Y="{pt["Y"]:.8f}" />\n'
    new_pts += "          </Points>"
    return xml_str[:pts_open] + new_pts + xml_str[pts_close:]


def patch_duration_xml(xml_str, n_weeks, start_s=432000):
    """
    Extend the Rainfall Simulation to run for n_weeks with weekly save steps.
    Weekly timesteps (~9 steps for 61-day window) keep each trial solve fast.
    Replaces the <TimeIncrements> block inside the Rainfall Simulation analysis.
    """
    week_s     = 7 * 86400
    duration_s = n_weeks * week_s
    step_lines = []
    for w in range(1, n_weeks + 1):
        elapsed = start_s + w * week_s
        step_lines.append(
            f'          <TimeStep Step="{week_s}" ElapsedTime="{elapsed}" Save="true" />'
        )
    steps_xml = "\n".join(step_lines)
    new_block = (
        f"<TimeIncrements>\n"
        f"        <Start>{start_s}</Start>\n"
        f"        <Duration>{duration_s}</Duration>\n"
        f"        <IncrementOption>Exponential</IncrementOption>\n"
        f"        <IncrementCount>{n_weeks}</IncrementCount>\n"
        f"        <TimeSteps Len=\"{n_weeks}\">\n"
        f"{steps_xml}\n"
        f"        </TimeSteps>\n"
        f"      </TimeIncrements>"
    )
    rs_start = xml_str.find("<Name>Rainfall Simulation</Name>")
    if rs_start == -1:
        print("  WARNING: Rainfall Simulation analysis not found - duration not patched")
        return xml_str
    ti_start = xml_str.find("<TimeIncrements>", rs_start)
    ti_end   = xml_str.find("</TimeIncrements>", ti_start) + len("</TimeIncrements>")
    return xml_str[:ti_start] + new_block + xml_str[ti_end:]


# ---------------------------------------------------------------------------
# SWCC helpers — convert pore pressure (psf) → VWC
# ---------------------------------------------------------------------------

def load_swcc_points(xml_str, fn_id=2):
    """
    Extract SWCC (VolWCFn) spline points for the given function ID.
    X = matric suction in psf, Y = volumetric water content.
    """
    pattern = rf"<VolWCFn>\s*<ID>{fn_id}</ID>.*?</VolWCFn>"
    m = re.search(pattern, xml_str, re.DOTALL)
    if not m:
        return None
    pts = re.findall(r'<Point X="([^"]+)" Y="([^"]+)"', m.group())
    return [(float(x), float(y)) for x, y in pts]


def apply_swcc(pore_pressure_psf, swcc_pts, theta_sat):
    """
    Convert a node's pore water pressure (psf) to VWC using the SWCC.
    Positive pore pressure  → saturated (theta_sat).
    Negative pore pressure  → suction = -pwp; interpolate on SWCC curve.
    """
    if pore_pressure_psf >= 0:
        return theta_sat
    suction = -pore_pressure_psf
    if suction <= swcc_pts[0][0]:
        return theta_sat
    if suction >= swcc_pts[-1][0]:
        return swcc_pts[-1][1]
    for i in range(len(swcc_pts) - 1):
        x0, y0 = swcc_pts[i]
        x1, y1 = swcc_pts[i + 1]
        if x0 <= suction <= x1:
            t = (suction - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return swcc_pts[-1][1]


# ---------------------------------------------------------------------------
# Prepare temp GSZ for a given parameter set
# ---------------------------------------------------------------------------

def prepare_trial_gsz(trial_idx, ksat_wyc, kyx_awyc, ksat_uyc, rain_points):
    """
    Copy base GSZ to isolated temp folder, patch hydraulic parameters
    and rainfall, re-zip with correct entry names.
    Returns (temp_gsz_path, temp_dir).
    """
    temp_dir = os.path.join(OUT_DIR, f"_calib_{trial_idx:04d}")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    gsz_name = os.path.basename(BASE_GSZ)
    gsz_stem = os.path.splitext(gsz_name)[0]
    temp_gsz = os.path.join(temp_dir, gsz_name)
    shutil.copy2(BASE_GSZ, temp_gsz)

    analysis_folders = [SEEP_INITIAL, SEEP_TRANSIENT, "Slope Stability", "FS"]

    with zipfile.ZipFile(temp_gsz, "r") as zin:
        all_items = zin.infolist()
        all_data  = {item.filename: zin.read(item.filename) for item in all_items}

    # Find root XML - any .xml not inside an analysis subfolder
    root_xml_key = None
    for key in all_data:
        if key.endswith(".xml") and not any(
            key.startswith(af + "/") for af in analysis_folders
        ):
            root_xml_key = key
            break
    if root_xml_key is None:
        raise RuntimeError(f"Root XML not found in {temp_gsz}")

    # Derive the actual XML stem from the archive (may differ from gsz filename)
    xml_stem = os.path.splitext(os.path.basename(root_xml_key))[0]

    xml_str = all_data[root_xml_key].decode("utf-8")

    # Apply parameter patches
    xml_str = patch_ksat_wyc(xml_str, ksat_wyc)
    xml_str = patch_kyx_ratio_awyc(xml_str, kyx_awyc)
    xml_str = patch_ksat_uyc(xml_str, ksat_uyc)
    xml_str = patch_rainfall_xml(xml_str, rain_points)
    xml_str = patch_duration_xml(xml_str, len(rain_points))  # rain_points is weekly

    # Re-zip with corrected entry names
    with zipfile.ZipFile(temp_gsz, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data  = all_data[fname]

            if xml_stem + ".xml" in fname:
                fixed_name = xml_stem + ".xml"
                for af in analysis_folders:
                    if fname.startswith(af + "/") or ("/" + af + "/") in fname:
                        fixed_name = af + "/" + xml_stem + ".xml"
                        break
                item.filename = fixed_name
                if fixed_name == xml_stem + ".xml":
                    data = xml_str.encode("utf-8")

            zout.writestr(item, data)

    return temp_gsz, temp_dir


# ---------------------------------------------------------------------------
# Run solver
# ---------------------------------------------------------------------------

def run_solver(solver_exe, temp_gsz):
    result = subprocess.run(
        [solver_exe, "/solve", temp_gsz],
        capture_output=True, text=True, timeout=SOLVER_TIMEOUT
    )
    if result.returncode != 0:
        raise RuntimeError(f"Solver error {result.returncode}: {result.stderr or result.stdout}")


# ---------------------------------------------------------------------------
# Read SEEP/W nodal VWC from solved archive
# ---------------------------------------------------------------------------

def read_seep_nodal_vwc(temp_gsz, analysis_name, swcc_pts, theta_sat):
    """
    Read VWC at sensor nodes from the solved SEEP/W archive.

    Approach:
      1. Read time.csv to map step number → absolute elapsed time (s).
      2. For each step folder, read node.csv and extract PoreWaterPressure
         at the two sensor nodes (identified from the mesh PLY).
      3. Convert pore pressure to VWC via the material SWCC.

    Sensor nodes (from PLY analysis, x≈199.5 ft, crest area):
      Node 173  y=75 ft  ≈ 1.5 m depth
      Node 172  y=71 ft  ≈ 3.0 m depth

    Returns: dict {sim_relative_s: {"vwc_shallow": float, "vwc_deep": float}}
    where sim_relative_s = 0 at start of calibration window.
    """
    SENSOR_NODE_SHALLOW = 173
    SENSOR_NODE_DEEP    = 172
    SIMULATION_START_S  = 432000   # Initial Condition elapsed time

    results = {}
    try:
        with zipfile.ZipFile(temp_gsz, "r") as z:
            files   = z.namelist()
            prefix  = analysis_name + "/"
            time_key = prefix + "time.csv"

            if time_key not in files:
                print(f"  WARNING: {time_key} not found in archive")
                return None

            # Parse time.csv → {step_num: absolute_elapsed_s}
            time_content = z.read(time_key).decode("utf-8")
            step_times   = {}
            reader = csv.DictReader(time_content.splitlines())
            for row in reader:
                try:
                    step    = int(float(row.get("Step", -1)))
                    elapsed = float(row.get("Time", row.get("ElapsedTime", 0)))
                    if step >= 0:
                        step_times[step] = int(elapsed)
                except (ValueError, KeyError):
                    continue

            if not step_times:
                print("  WARNING: no timestep data in time.csv")
                return None

            # For each step, extract PoreWaterPressure at sensor nodes
            for step_num, abs_elapsed_s in sorted(step_times.items()):
                node_key = f"{prefix}{step_num:03d}/node.csv"
                if node_key not in files:
                    node_key = f"{prefix}{step_num}/node.csv"
                    if node_key not in files:
                        continue

                node_content = z.read(node_key).decode("utf-8")
                pwp_shallow  = None
                pwp_deep     = None

                reader = csv.DictReader(node_content.splitlines())
                for row in reader:
                    try:
                        node_id = int(row.get("Node", 0))
                        pwp     = float(row.get("PoreWaterPressure", "nan"))
                        if node_id == SENSOR_NODE_SHALLOW:
                            pwp_shallow = pwp
                        elif node_id == SENSOR_NODE_DEEP:
                            pwp_deep = pwp
                    except (ValueError, KeyError):
                        continue

                if pwp_shallow is not None and pwp_deep is not None:
                    rel_s = abs_elapsed_s - SIMULATION_START_S
                    results[rel_s] = {
                        "vwc_shallow": apply_swcc(pwp_shallow, swcc_pts, theta_sat),
                        "vwc_deep":    apply_swcc(pwp_deep,    swcc_pts, theta_sat),
                    }

    except Exception as e:
        print(f"  WARNING: could not read nodal VWC - {e}")
        return None

    return results if results else None


def probe_seep_archive(temp_gsz, analysis_name):
    """
    Print what files exist in the solved SEEP/W archive.
    Used to understand output structure before running full calibration.
    """
    print(f"\n=== SEEP/W archive contents for '{analysis_name}' ===")
    try:
        with zipfile.ZipFile(temp_gsz, "r") as z:
            prefix = analysis_name + "/"
            seep_files = [f for f in z.namelist() if f.startswith(prefix)]
            if not seep_files:
                print("  No files found under this analysis name.")
                print("  All archive entries:")
                for f in z.namelist():
                    info = z.getinfo(f)
                    print(f"    {f}  ({info.file_size} bytes)")
                return

            for f in seep_files:
                info = z.getinfo(f)
                print(f"  {f}  ({info.file_size} bytes)")
                # Print first few lines of CSV files
                if f.endswith(".csv"):
                    with z.open(f) as fh:
                        lines = fh.read(1000).decode("utf-8", errors="replace").splitlines()
                    for i, line in enumerate(lines[:5]):
                        print(f"    [{i}] {line[:120]}")
    except Exception as e:
        print(f"  Error: {e}")


# ---------------------------------------------------------------------------
# Objective function
# ---------------------------------------------------------------------------

iter_count   = [0]
trial_log    = []

def objective(params, solver, rain_points, obs_daily, swcc_pts, theta_sat):
    """
    params[0] = log10(KSat_WYC)
    params[1] = log10(KYX_ratio_AWYC)
    params[2] = log10(KSat_UYC)
    """
    log_ksat, log_kyx, log_ksat_uyc = params

    # Clip to bounds
    log_ksat     = np.clip(log_ksat,     LOG_KSAT_WYC_MIN, LOG_KSAT_WYC_MAX)
    log_kyx      = np.clip(log_kyx,      LOG_KYX_MIN,      LOG_KYX_MAX)
    log_ksat_uyc = np.clip(log_ksat_uyc, LOG_KSAT_UYC_MIN, LOG_KSAT_UYC_MAX)

    ksat_wyc  = 10 ** log_ksat
    kyx_awyc  = 10 ** log_kyx
    ksat_uyc  = 10 ** log_ksat_uyc

    iter_count[0] += 1
    idx = iter_count[0]
    print(f"  Trial {idx:3d}: KSat_WYC={ksat_wyc:.3e}  KYX_AWYC={kyx_awyc:.0f}  KSat_UYC={ksat_uyc:.3e}")

    temp_dir = None
    try:
        temp_gsz, temp_dir = prepare_trial_gsz(idx, ksat_wyc, kyx_awyc, ksat_uyc, rain_points)
        run_solver(solver, temp_gsz)

        # First iteration - probe the archive structure
        if idx == 1:
            probe_seep_archive(temp_gsz, SEEP_TRANSIENT)

        sim_results = read_seep_nodal_vwc(temp_gsz, SEEP_TRANSIENT, swcc_pts, theta_sat)

        if sim_results is None or len(sim_results) == 0:
            print("  WARNING: no VWC results readable - returning large penalty")
            rmse = 9999.0
        else:
            # Align simulated timesteps with observed daily VWC
            errors = []
            t0_s = 0  # calibration window start = time 0 in the sim
            for ts, row in obs_daily.iterrows():
                elapsed_s = int((ts - obs_daily.index[0]).total_seconds())
                # Find closest simulated timestep
                if not sim_results:
                    continue
                closest_t = min(sim_results.keys(), key=lambda t: abs(t - elapsed_s))
                sim = sim_results[closest_t]
                obs_s = row["Moisture_1.5m"]
                obs_d = row["Moisture_3m"]
                if np.isfinite(obs_s) and np.isfinite(sim["vwc_shallow"]):
                    errors.append((sim["vwc_shallow"] - obs_s) ** 2)
                if np.isfinite(obs_d) and np.isfinite(sim["vwc_deep"]):
                    errors.append((sim["vwc_deep"] - obs_d) ** 2)
            rmse = np.sqrt(np.mean(errors)) if errors else 9999.0

        print(f"    -> RMSE = {rmse:.5f}")
        trial_log.append({
            "trial":        idx,
            "log_ksat_wyc": round(log_ksat,     4),
            "log_kyx":      round(log_kyx,      4),
            "log_ksat_uyc": round(log_ksat_uyc, 4),
            "ksat_wyc":     ksat_wyc,
            "kyx_awyc":     kyx_awyc,
            "ksat_uyc":     ksat_uyc,
            "rmse":         rmse,
        })
        return rmse

    except Exception as e:
        print(f"    -> FAILED: {e}")
        trial_log.append({
            "trial":        idx,
            "log_ksat_wyc": round(log_ksat,     4),
            "log_kyx":      round(log_kyx,      4),
            "log_ksat_uyc": round(log_ksat_uyc, 4),
            "ksat_wyc":     ksat_wyc,
            "kyx_awyc":     kyx_awyc,
            "ksat_uyc":     ksat_uyc,
            "rmse":         9999.0,
        })
        return 9999.0

    finally:
        if temp_dir:
            _cleanup(temp_dir)


def _cleanup(temp_dir):
    for attempt in range(8):
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            return
        except Exception:
            time.sleep(2)


# ---------------------------------------------------------------------------
# Save calibrated GSZ
# ---------------------------------------------------------------------------

def save_calibrated_gsz(ksat_wyc, kyx_awyc, ksat_uyc, rain_points):
    """Save a permanent copy of the GSZ with calibrated parameters."""
    out_path = os.path.join(OUT_DIR, "Metro-Center-calibrated.gsz")
    shutil.copy2(BASE_GSZ, out_path)

    analysis_folders = [SEEP_INITIAL, SEEP_TRANSIENT, "Slope Stability", "FS"]

    with zipfile.ZipFile(out_path, "r") as zin:
        all_items = zin.infolist()
        all_data  = {item.filename: zin.read(item.filename) for item in all_items}

    root_xml_key = next(
        (k for k in all_data
         if k.endswith(".xml")
         and not any(k.startswith(af + "/") for af in analysis_folders)),
        None
    )
    if root_xml_key is None:
        print("WARNING: could not save calibrated GSZ - root XML not found")
        return

    xml_stem = os.path.splitext(os.path.basename(root_xml_key))[0]
    xml_str = all_data[root_xml_key].decode("utf-8")
    xml_str = patch_ksat_wyc(xml_str, ksat_wyc)
    xml_str = patch_kyx_ratio_awyc(xml_str, kyx_awyc)
    xml_str = patch_ksat_uyc(xml_str, ksat_uyc)

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data  = all_data[fname]
            if xml_stem + ".xml" in fname:
                fixed_name = xml_stem + ".xml"
                for af in analysis_folders:
                    if fname.startswith(af + "/") or ("/" + af + "/") in fname:
                        fixed_name = af + "/" + xml_stem + ".xml"
                        break
                item.filename = fixed_name
                if fixed_name == xml_stem + ".xml":
                    data = xml_str.encode("utf-8")
            zout.writestr(item, data)

    print(f"\nCalibrated GSZ saved to: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("SEEP/W Hydraulic Calibration")
    print(f"  Base GSZ     : {BASE_GSZ}")
    print(f"  Sensor data  : {SENSOR_CSV}")
    print(f"  Cal window   : {CALIB_START} to {CALIB_END}")
    print(f"  Sensor depths: {DEPTH_1_5M:.1f} ft ({DEPTH_1_5M*0.3048:.1f}m), "
          f"{DEPTH_3M:.1f} ft ({DEPTH_3M*0.3048:.1f}m)")
    print("=" * 60)

    # Validate inputs
    for path, label in [(BASE_GSZ, "Base GSZ"), (SENSOR_CSV, "Sensor CSV")]:
        if not os.path.exists(path):
            print(f"\nERROR: {label} not found at {path}")
            print("Make sure you copied Metro-Center.gsz to the calibration folder.")
            sys.exit(1)

    solver = find_solver()
    if solver is None:
        print("\nERROR: GeoStudio solver not found.")
        sys.exit(1)
    print(f"\n  Solver: {solver}")

    # Load sensor data
    print("\nLoading sensor data...")
    obs_daily = load_sensor_data()

    # Build calibration rainfall time series from sensor precip data
    rain_points = build_rainfall_time_series(obs_daily)
    print(f"  Rainfall time series: {len(rain_points)} weekly points")

    # Load SWCC from base GSZ for AWYC material (fn_id=2, theta_sat=0.55)
    # Used to convert model PoreWaterPressure (psf) → VWC for comparison with sensors
    print("\nLoading SWCC from base GSZ...")
    with zipfile.ZipFile(BASE_GSZ, "r") as zin:
        all_keys = zin.namelist()
        root_key = next(
            k for k in all_keys
            if k.endswith(".xml") and not any(
                k.startswith(af + "/") for af in [SEEP_INITIAL, SEEP_TRANSIENT, "Slope Stability", "FS"]
            )
        )
        base_xml = zin.read(root_key).decode("utf-8")
    SWCC_FN_ID  = 2       # VOL WC AWYC — material at crest sensor depths
    THETA_SAT   = 0.55
    swcc_pts = load_swcc_points(base_xml, fn_id=SWCC_FN_ID)
    if swcc_pts is None:
        print(f"  ERROR: SWCC fn_id={SWCC_FN_ID} not found in XML")
        sys.exit(1)
    print(f"  SWCC loaded: fn_id={SWCC_FN_ID}, {len(swcc_pts)} points, theta_sat={THETA_SAT}")

    # Initial parameter values (log10 space)
    # Warm-start WYC and KYX from previous calibration best; UYC at prior.
    x0 = np.array([
        np.log10(5.640e-06),       # KSat_WYC — previous best
        np.log10(80827.0),         # KYX_AWYC  — previous best
        np.log10(KSAT_UYC_BASE),   # KSat_UYC  — prior (new parameter)
    ])
    print(f"\nStarting parameters:")
    print(f"  KSat_WYC  = {10**x0[0]:.3e}  (log10 = {x0[0]:.3f})  [warm-start from prev calib]")
    print(f"  KYX_AWYC  = {10**x0[1]:.0f}  (log10 = {x0[1]:.3f})  [warm-start from prev calib]")
    print(f"  KSat_UYC  = {10**x0[2]:.3e}  (log10 = {x0[2]:.3f})  [prior - new parameter]")

    print(f"\nRunning optimisation (max {MAX_OPT_ITER} iterations)...\n")

    result = minimize(
        objective,
        x0,
        args=(solver, rain_points, obs_daily, swcc_pts, THETA_SAT),
        method="Nelder-Mead",
        options={
            "maxiter":  MAX_OPT_ITER,
            "xatol":    0.05,   # tolerance in log10 space
            "fatol":    0.001,  # tolerance in RMSE
            "disp":     True,
        }
    )

    # Extract best parameters
    best_log_ksat, best_log_kyx, best_log_ksat_uyc = result.x
    best_ksat     = 10 ** best_log_ksat
    best_kyx      = 10 ** best_log_kyx
    best_ksat_uyc = 10 ** best_log_ksat_uyc

    print(f"\n{'=' * 60}")
    print(f"Optimisation complete")
    print(f"  Status        : {result.message}")
    print(f"  Iterations    : {result.nit}")
    print(f"  Best RMSE     : {result.fun:.5f}")
    print(f"  KSat_WYC      : {best_ksat:.3e} ft/s  (baseline: {KSAT_WYC_BASE:.3e})  change: {best_ksat/KSAT_WYC_BASE:.1f}x")
    print(f"  KYX_AWYC      : {best_kyx:.1f}         (baseline: {KYX_RATIO_AWYC_BASE:.0f})  change: {best_kyx/KYX_RATIO_AWYC_BASE:.1f}x")
    print(f"  KSat_UYC      : {best_ksat_uyc:.3e} ft/s  (baseline: {KSAT_UYC_BASE:.3e})  change: {best_ksat_uyc/KSAT_UYC_BASE:.1f}x")

    # Save trial log
    log_path = os.path.join(OUT_DIR, "calibration_trial_log.csv")
    if trial_log:
        with open(log_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trial_log[0].keys())
            writer.writeheader()
            writer.writerows(trial_log)
        print(f"\nTrial log saved to: {log_path}")

    # Save calibrated GSZ
    save_calibrated_gsz(best_ksat, best_kyx, best_ksat_uyc, rain_points)

    # Final cleanup
    for item in os.listdir(OUT_DIR):
        if item.startswith("_calib_"):
            _cleanup(os.path.join(OUT_DIR, item))

    print("\nDone.")


if __name__ == "__main__":
    main()
