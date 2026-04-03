"""
SEEP/W Hydraulic Calibration - Direct Suction Match (v4)
=========================================================
All-XML + GeoCmd.exe approach. No GSI in the calibration loop.

  - XML patching: Ksat, KYXRatio, rainfall, time increments
  - GeoCmd.exe: solver via subprocess (~7 min/trial)
  - ZIP/CSV: read mesh, pore pressures, time steps from solved archive

Parameters calibrated:
  - KSat_WYC  : Weathered Yazoo Clay Ksat (ft/s)      [Seep WYC material]
  - KYX_AWYC  : Active Zone anisotropy ratio            [Seep AWYC material]
  - KSat_UYC  : Unweathered Yazoo Clay Ksat (ft/s)     [Seep UYC material]

Fixes from diagnostic (v3 -> v4):
  - FIX 1: _patch_material_ksat/kyxratio search to </Material> not +500 chars
  - FIX 2: list_saved_steps/read_pore_pressure use folder-per-step pattern
  - FIX 3: _RESULT_FOLDERS = [] (don't strip - breaks material assignments)
  - FIX 4: _patch_time_increments fallback regex for attributed tags

Usage:
    python calibrate_seepw.py
"""

import sys, os, shutil, csv, warnings, zipfile, io, re, glob, struct, subprocess, time
import numpy as np
import pandas as pd
from scipy.optimize import minimize

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
CALIB_DIR       = r"E:\Github\MCP_Geostudio\calibration"
BASE_GSZ        = os.path.join(CALIB_DIR, "Metro-Center_cal.gsz")
BASE_GSZ_NAME   = os.path.basename(BASE_GSZ)
OUT_GSZ         = os.path.join(CALIB_DIR, "Metro-Center-seep-final.gsz")
SENSOR_CSV      = os.path.join(CALIB_DIR, "S2.csv")
OUT_CSV         = os.path.join(CALIB_DIR, "seep_suction_calibration_log.csv")

CALIB_START     = "2018-08-22"
CALIB_END       = "2018-12-16"

CREST_X         = 195.0
SURFACE_Y       = 83.0
SENSOR_Y = {
    "1.5m": SURFACE_Y - 4.92,    # ~78.08 ft
    "3.0m": SURFACE_Y - 9.84,    # ~73.16 ft
    "5.0m": SURFACE_Y - 16.40,   # ~66.60 ft
}

KPA_TO_PSF         = 20.8854
SEEP_FOLDER        = "Rainfall Simulation"
SOLVER_TIMEOUT     = 900   # 15 min max (solves all 4 analyses)
SOLVER_OVERRIDE    = None  # set full path to GeoCmd.exe if auto-detect fails

# Original Ksat in the base GSZ (used for scaling K-function points)
ORIG_KSAT_WYC      = 1.004e-07   # ft/s
ORIG_KSAT_UYC      = 1.004e-07   # ft/s

# Search bounds (log10 space)
KSAT_WYC_BASE      = 1.004e-07
KYX_RATIO_BASE     = 11155.0
KSAT_UYC_BASE      = 1.004e-07

LOG_KSAT_WYC_MIN   = np.log10(KSAT_WYC_BASE) - 2.0
LOG_KSAT_WYC_MAX   = np.log10(KSAT_WYC_BASE) + 2.0
LOG_KYX_MIN        = np.log10(1000)
LOG_KYX_MAX        = np.log10(500000)
LOG_KSAT_UYC_MIN   = np.log10(KSAT_UYC_BASE) - 2.0
LOG_KSAT_UYC_MAX   = np.log10(KSAT_UYC_BASE) + 2.0

MAX_OPT_ITER       = 100

# FIX 3: Don't strip result folders - breaks material assignments in GeoCmd
_RESULT_FOLDERS    = []


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------
def find_solver():
    if SOLVER_OVERRIDE and os.path.exists(SOLVER_OVERRIDE):
        return SOLVER_OVERRIDE
    patterns = [
        r"C:\Program Files\Seequent\GeoStudio 2025*\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio 2024*\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio*\Bin\GeoCmd.exe",
        r"C:\Program Files\GeoSlope\GeoStudio*\GeoCmd.exe",
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def run_solver(solver_exe, gsz_path):
    """Run GeoCmd.exe to solve a GSZ file. Raises on failure."""
    result = subprocess.run(
        [solver_exe, "/solve", gsz_path],
        capture_output=True, text=True, timeout=SOLVER_TIMEOUT
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Solver exit {result.returncode}: "
            f"{(result.stderr or result.stdout)[:200]}")


# ---------------------------------------------------------------------------
# Sensor Data
# ---------------------------------------------------------------------------
def load_sensor_data():
    df = pd.read_csv(SENSOR_CSV, low_memory=False)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    for col in ["Suction_1.5m", "Suction_3m", "Suction_5m", "Precipitation"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    crest = df[df["position"] == "Crest"].copy().set_index("timestamp")
    daily = crest[["Suction_1.5m", "Suction_3m", "Suction_5m",
                    "Precipitation"]].resample("D").agg({
        "Suction_1.5m": "mean", "Suction_3m": "mean",
        "Suction_5m": "mean", "Precipitation": "sum",
    })
    daily = daily.loc[CALIB_START:CALIB_END]
    daily = daily.dropna(subset=["Suction_1.5m", "Suction_3m",
                                  "Suction_5m"], how="all")
    if daily.empty:
        raise ValueError(
            f"No sensor data between {CALIB_START} and {CALIB_END}!")

    print(f"  Sensor window : {daily.index[0].date()} to "
          f"{daily.index[-1].date()}")
    print(f"  Days with data: {len(daily)}")
    for col, label in [("Suction_1.5m", "1.5m"), ("Suction_3m", "3.0m"),
                        ("Suction_5m", "5.0m")]:
        s = daily[col].dropna()
        print(f"  {label} suction: {s.min():.1f} to {s.max():.1f} kPa  "
              f"({s.min()*KPA_TO_PSF:.0f} to {s.max()*KPA_TO_PSF:.0f} psf)")
    return daily


def build_rainfall_time_series(daily):
    mm_to_in = 1.0 / 25.4
    weekly = daily["Precipitation"].resample("W").sum()
    points = []
    t0 = daily.index[0]
    for ts, total_mm in weekly.items():
        elapsed_s = max(int((ts - t0).total_seconds()), 86400)
        points.append({"X": float(elapsed_s),
                       "Y": (total_mm * mm_to_in) / 7.0})
    return points


# ---------------------------------------------------------------------------
# ZIP/CSV Result Extraction
# ---------------------------------------------------------------------------
def read_mesh_from_ply(gsz_path):
    ply_path = f"{SEEP_FOLDER}/Mesh.ply"
    with zipfile.ZipFile(gsz_path, 'r') as z:
        if ply_path not in z.namelist():
            raise FileNotFoundError(f"'{ply_path}' not in archive")
        content = z.read(ply_path)

    header_end = content.find(b"end_header\n")
    if header_end == -1:
        header_end = content.find(b"end_header\r\n")
        header_len = header_end + 12
    else:
        header_len = header_end + 11

    header = content[:header_len].decode('utf-8', errors='replace')
    n_nodes = 0
    for line in header.splitlines():
        parts = line.split()
        if (len(parts) >= 3 and parts[0] == "element"
                and parts[1] in ("vertex", "node")):
            n_nodes = int(parts[2])

    offset = header_len
    if "element version" in header:
        offset += 4

    xs, ys = [], []
    if "binary" in header:
        for _ in range(n_nodes):
            x, y, z = struct.unpack('<ddd', content[offset:offset+24])
            xs.append(x)
            ys.append(y)
            offset += 24
    else:
        text_lines = content[header_len:].decode(
            'utf-8', errors='replace').splitlines()
        for i in range(n_nodes):
            parts = text_lines[i].split()
            xs.append(float(parts[0]))
            ys.append(float(parts[1]))

    return np.array(xs), np.array(ys)


def find_sensor_nodes(gsz_path):
    try:
        arr_x, arr_y = read_mesh_from_ply(gsz_path)
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

    print(f"  Mesh: {len(arr_x)} nodes from Mesh.ply")
    print(f"  X range: {arr_x.min():.2f} to {arr_x.max():.2f} ft")
    print(f"  Y range: {arr_y.min():.2f} to {arr_y.max():.2f} ft")

    result = {}
    for label, target_y in SENSOR_Y.items():
        dist = np.sqrt((arr_x - CREST_X)**2 + (arr_y - target_y)**2)
        idx = int(np.argmin(dist))
        d = dist[idx]
        result[label] = idx
        warn = "  WARNING >5ft!" if d > 5.0 else ""
        print(f"  Sensor {label}: node_idx={idx}  "
              f"x={arr_x[idx]:.2f}  y={arr_y[idx]:.2f}  "
              f"dist={d:.2f} ft{warn}")

    vals = list(result.values())
    if len(vals) != len(set(vals)):
        print("  WARNING: Two sensors share the same mesh node!")
        print("  -> Refine mesh near crest in GeoStudio for better calibration")

    return result


def read_time_steps(gsz_path):
    time_path = f"{SEEP_FOLDER}/time.csv"
    with zipfile.ZipFile(gsz_path, 'r') as z:
        if time_path not in z.namelist():
            return []
        content = z.read(time_path).decode('utf-8', errors='replace')
    steps = []
    for row in csv.DictReader(io.StringIO(content)):
        try:
            steps.append({"step": int(row["Step"]),
                          "time_s": float(row["Time"])})
        except (ValueError, KeyError):
            pass
    return steps


def list_saved_steps(gsz_path):
    """
    FIX 2: GeoStudio 2025 stores each time step in its own folder:
      Rainfall Simulation/000/node.csv
      Rainfall Simulation/001/node.csv
      ...
      Rainfall Simulation/016/node.csv
    Not the old pattern of 001/node-{N}s.csv.
    """
    with zipfile.ZipFile(gsz_path, 'r') as z:
        all_files = z.namelist()
    steps = set()
    for f in all_files:
        if (f.startswith(SEEP_FOLDER + "/") and f.endswith("/node.csv")
                and f.count("/") == 2):
            folder = f.split("/")[1]
            try:
                steps.add(int(folder))
            except ValueError:
                pass
    return sorted(steps)


def read_pore_pressure(gsz_path, step_idx):
    """
    FIX 2: Read from Rainfall Simulation/{step:03d}/node.csv
    instead of the old 001/node-{N}s.csv pattern.
    """
    target = f"{SEEP_FOLDER}/{step_idx:03d}/node.csv"
    with zipfile.ZipFile(gsz_path, 'r') as z:
        if target not in z.namelist():
            return None
        content = z.read(target).decode('utf-8', errors='replace')

    pwp_dict = {}
    for row in csv.DictReader(io.StringIO(content)):
        try:
            pwp_dict[int(row["Node"])] = float(row["PoreWaterPressure"])
        except (ValueError, KeyError):
            pass
    if not pwp_dict:
        return None
    max_id = max(pwp_dict.keys())
    arr = np.full(max_id + 1, np.nan)
    for nid, val in pwp_dict.items():
        arr[nid] = val
    return arr


# ---------------------------------------------------------------------------
# XML Patching
# ---------------------------------------------------------------------------
def _patch_ksat_in_kfn(xml_text, kfn_name, new_ksat, orig_ksat):
    """
    Patch a K-function block:
      1. Scale all <Point> Y values by (new_ksat / orig_ksat)
      2. Update HydKSat= in the <Estimate> tag
    Only matters for materials that reference a KFn by number
    (e.g. Seep AWYC -> HC AWYC via KFnNum="2").
    Seep WYC/UYC have no KFnNum - solver computes K from KSat + SWCC.
    """
    ratio = new_ksat / orig_ksat
    marker = f">{kfn_name}<"
    start = xml_text.find(marker)
    if start == -1:
        return xml_text

    end = xml_text.find("</KFn>", start)
    if end == -1:
        return xml_text
    end += len("</KFn>")

    block = xml_text[start:end]

    def scale_point(m):
        x_val = m.group(1)
        y_val = float(m.group(2))
        return f'<Point X="{x_val}" Y="{y_val * ratio:.15g}" />'

    block = re.sub(r'<Point X="([^"]*)" Y="([^"]*)" />',
                   scale_point, block)
    block = re.sub(r'HydKSat=[0-9eE.+\-]+',
                   f'HydKSat={new_ksat:.6g}', block)

    return xml_text[:start] + block + xml_text[end:]


def _patch_material_ksat(xml_text, material_name, new_ksat):
    """
    Patch KSat="..." attribute on the <Hydraulic> tag for a material.
    FIX 1: Searches to </Material> boundary instead of +500 chars.
    The StressStrain block between <Name> and <Hydraulic> can be >800 chars.
    """
    marker = f">{material_name}<"
    start = xml_text.find(marker)
    if start == -1:
        return xml_text

    # Search to end of material block, not fixed 500 chars
    mat_end = xml_text.find("</Material>", start)
    if mat_end == -1:
        mat_end = start + 2000  # fallback

    hyd_start = xml_text.find("<Hydraulic", start)
    if hyd_start == -1 or hyd_start > mat_end:
        return xml_text

    hyd_end = xml_text.find("/>", hyd_start) + 2
    hyd_tag = xml_text[hyd_start:hyd_end]

    if 'KSat=' not in hyd_tag:
        return xml_text

    new_tag = re.sub(r'KSat="[^"]*"', f'KSat="{new_ksat:.6g}"', hyd_tag)
    return xml_text[:hyd_start] + new_tag + xml_text[hyd_end:]


def _patch_material_kyxratio(xml_text, material_name, new_kyx):
    """
    Patch KYXRatio="..." attribute on the <Hydraulic> tag.
    FIX 1: Searches to </Material> boundary instead of +500 chars.
    """
    marker = f">{material_name}<"
    start = xml_text.find(marker)
    if start == -1:
        return xml_text

    mat_end = xml_text.find("</Material>", start)
    if mat_end == -1:
        mat_end = start + 2000

    hyd_start = xml_text.find("<Hydraulic", start)
    if hyd_start == -1 or hyd_start > mat_end:
        return xml_text

    hyd_end = xml_text.find("/>", hyd_start) + 2
    hyd_tag = xml_text[hyd_start:hyd_end]

    if 'KYXRatio=' not in hyd_tag:
        return xml_text

    new_tag = re.sub(r'KYXRatio="[^"]*"',
                     f'KYXRatio="{new_kyx:.6g}"', hyd_tag)
    return xml_text[:hyd_start] + new_tag + xml_text[hyd_end:]


def _patch_rainfall(xml_text, rain_points):
    """Patch rainfall function points."""
    idx = xml_text.find(">rainfall<")
    if idx == -1:
        return xml_text
    chunk_end = xml_text.find("</ClimateFn>", idx)
    chunk = xml_text[idx:chunk_end]
    new_pts = f'<Points Len="{len(rain_points)}">\n'
    for pt in rain_points:
        new_pts += (f'            <Point X="{pt["X"]}" '
                    f'Y="{pt["Y"]:.8f}" />\n')
    new_pts += "          </Points>"
    chunk = re.sub(r'<Points.*?</Points>', new_pts, chunk,
                   count=1, flags=re.DOTALL)
    return xml_text[:idx] + chunk + xml_text[chunk_end:]


def _patch_time_increments(xml_text, rain_points):
    """
    Patch TimeIncrements for the Rainfall Simulation analysis.
    FIX 4: Two-stage regex - try bare tag, then tag with attributes.
    """
    idx = xml_text.find(">Rainfall Simulation<")
    if idx == -1:
        return xml_text
    chunk_end = xml_text.find("</Analysis>", idx)
    chunk = xml_text[idx:chunk_end]

    n_weeks = len(rain_points)
    start_s = 432000
    week_s = 7 * 86400

    new_ti = (f"<TimeIncrements>\n"
              f"        <Start>{start_s}</Start>\n"
              f"        <Duration>{n_weeks * week_s}</Duration>\n"
              f"        <IncrementOption>Exponential</IncrementOption>\n"
              f"        <IncrementCount>{n_weeks}</IncrementCount>\n"
              f'        <TimeSteps Len="{n_weeks}">\n')
    for w in range(1, n_weeks + 1):
        new_ti += (f'          <TimeStep Step="{week_s}" '
                   f'ElapsedTime="{start_s + w * week_s}" '
                   f'Save="true" />\n')
    new_ti += "        </TimeSteps>\n      </TimeIncrements>"

    old_chunk = chunk

    # Try 1: bare <TimeIncrements>
    chunk = re.sub(r'<TimeIncrements>.*?</TimeIncrements>',
                   new_ti, chunk, count=1, flags=re.DOTALL)

    if chunk == old_chunk:
        # Try 2: <TimeIncrements SomeAttr="...">
        chunk = re.sub(r'<TimeIncrements[^>]*>.*?</TimeIncrements>',
                       new_ti, old_chunk, count=1, flags=re.DOTALL)

    return xml_text[:idx] + chunk + xml_text[chunk_end:]


# ---------------------------------------------------------------------------
# Work GSZ Creation
# ---------------------------------------------------------------------------
def _cleanup_old_iters():
    for item in os.listdir(CALIB_DIR):
        if item.startswith("_seep_iter_"):
            try:
                shutil.rmtree(os.path.join(CALIB_DIR, item))
            except Exception:
                pass


def create_work_gsz(rain_points, trial_idx, ksat_wyc, kyx_awyc, ksat_uyc):
    """
    Build a clean work GSZ with ALL parameters patched via XML:
      - Ksat for WYC and UYC (material attribute + K-function points)
      - KYXRatio for AWYC (material attribute)
      - Rainfall function points
      - TimeIncrements for Rainfall Simulation (all steps saved)
    """
    trial_dir = os.path.join(CALIB_DIR, f"_seep_iter_{trial_idx:04d}")
    if os.path.exists(trial_dir):
        try:
            shutil.rmtree(trial_dir)
        except Exception:
            pass
    os.makedirs(trial_dir, exist_ok=True)
    work_gsz = os.path.join(trial_dir, BASE_GSZ_NAME)

    with zipfile.ZipFile(BASE_GSZ, 'r') as zin, \
         zipfile.ZipFile(work_gsz, 'w', zipfile.ZIP_DEFLATED) as zout:

        xml_name = [f for f in zin.namelist()
                    if f.endswith('.xml') and '/' not in f][0]

        # Copy everything except root XML (and any result folders if set)
        for item in zin.infolist():
            if item.filename == xml_name:
                continue
            if _RESULT_FOLDERS and any(
                    item.filename.startswith(pf) for pf in _RESULT_FOLDERS):
                continue
            zout.writestr(item, zin.read(item.filename))

        xml_text = zin.read(xml_name).decode('utf-8', errors='replace')

        # 1. Patch Ksat for Weathered Yazoo Clay (material + K-function)
        xml_text = _patch_ksat_in_kfn(xml_text, "HC WYC",
                                       ksat_wyc, ORIG_KSAT_WYC)
        xml_text = _patch_material_ksat(xml_text, "Seep WYC", ksat_wyc)

        # 2. Patch Ksat for Unweathered Yazoo Clay
        xml_text = _patch_ksat_in_kfn(xml_text, "HC UYC",
                                       ksat_uyc, ORIG_KSAT_UYC)
        xml_text = _patch_material_ksat(xml_text, "Seep UYC", ksat_uyc)

        # 3. Patch KYXRatio for Active Zone
        xml_text = _patch_material_kyxratio(xml_text, "Seep AWYC",
                                            kyx_awyc)

        # 4. Patch rainfall
        xml_text = _patch_rainfall(xml_text, rain_points)

        # 5. Patch time increments (all steps Save="true")
        xml_text = _patch_time_increments(xml_text, rain_points)

        info = zin.getinfo(xml_name)
        zout.writestr(info, xml_text.encode('utf-8'))

    return work_gsz, trial_dir


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
_sensor_nodes = {}


def run_preflight(solver_exe, rain_points, obs_daily):
    """
    Run a single solve with baseline params to verify the full pipeline:
    XML patch -> GeoCmd solve -> ZIP read -> suction comparison.
    """
    print("\n" + "=" * 65)
    print("PRE-FLIGHT - full pipeline test")
    print("=" * 65)

    try:
        work_gsz, trial_dir = create_work_gsz(
            rain_points, 0,
            KSAT_WYC_BASE, KYX_RATIO_BASE, KSAT_UYC_BASE)

        print(f"  Work GSZ: {work_gsz}")
        print(f"  Solving with GeoCmd.exe ...", end="", flush=True)
        t0 = time.time()
        run_solver(solver_exe, work_gsz)
        dt = time.time() - t0
        print(f"  done ({dt:.1f}s)")

        # Check results
        time_steps = read_time_steps(work_gsz)
        saved = list_saved_steps(work_gsz)
        print(f"  Time steps: {len(time_steps)}")
        print(f"  Saved PWP steps: {saved}")

        if not saved:
            print("  ERROR: No results after solve.")
            return False

        max_node = max(_sensor_nodes.values())

        # Check +1 offset (Mesh.ply 0-based vs node.csv 1-based)
        test_pwp = read_pore_pressure(work_gsz, saved[-1])
        if test_pwp is not None and max_node < len(test_pwp):
            val_at_idx = (test_pwp[max_node]
                          if max_node < len(test_pwp) else np.nan)
            val_at_idx1 = (test_pwp[max_node + 1]
                           if (max_node + 1) < len(test_pwp) else np.nan)
            if np.isnan(val_at_idx) and not np.isnan(val_at_idx1):
                print("  Applying +1 offset (Mesh.ply 0-based -> "
                      "node.csv 1-based)")
                for label in _sensor_nodes:
                    _sensor_nodes[label] += 1
                max_node = max(_sensor_nodes.values())

        # Print comparison table
        time_for_step = {ts["step"]: ts["time_s"] for ts in time_steps}
        obs_t0 = obs_daily.index[0]
        sensor_cols = {"1.5m": "Suction_1.5m", "3.0m": "Suction_3m",
                       "5.0m": "Suction_5m"}

        print(f"\n  {'Step':>4}  {'Days':>8}  "
              f"{'Sim1.5':>8} {'Obs1.5':>8}  "
              f"{'Sim3.0':>8} {'Obs3.0':>8}  "
              f"{'Sim5.0':>8} {'Obs5.0':>8}  (kPa)")
        print("  " + "-" * 76)

        for step_idx in saved:
            if step_idx == 0:
                continue
            pwp = read_pore_pressure(work_gsz, step_idx)
            if pwp is None or max_node >= len(pwp):
                continue

            time_s = time_for_step.get(step_idx, 0)
            days = time_s / 86400.0
            rel_s = max(0, time_s - 432000)

            obs_vals = {}
            target_ts = obs_t0 + pd.Timedelta(seconds=rel_s)
            diffs = abs(obs_daily.index - target_ts)
            if diffs.min() < pd.Timedelta(days=7):
                nearest = obs_daily.index[diffs.argmin()]
                for label, col in sensor_cols.items():
                    obs_vals[label] = obs_daily.loc[nearest, col]

            parts = [f"  {step_idx:>4}  {days:>8.1f}"]
            for label in ["1.5m", "3.0m", "5.0m"]:
                nidx = _sensor_nodes[label]
                sim_kpa = (pwp[nidx] / KPA_TO_PSF
                           if nidx < len(pwp) and np.isfinite(pwp[nidx])
                           else float('nan'))
                parts.append(f"  {sim_kpa:>8.2f} "
                             f"{obs_vals.get(label, float('nan')):>8.2f}")
            print("".join(parts))

        try:
            shutil.rmtree(trial_dir)
        except Exception:
            pass

        print(f"\n  Pre-flight PASSED (solve time: {dt:.1f}s)")
        return True

    except Exception as e:
        print(f"\n  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# Objective
# ---------------------------------------------------------------------------
iter_count = [0]
trial_log  = []


def objective(params, solver_exe, rain_points, obs_daily):
    log_ksat, log_kyx, log_ksat_uyc = params
    log_ksat     = np.clip(log_ksat,     LOG_KSAT_WYC_MIN, LOG_KSAT_WYC_MAX)
    log_kyx      = np.clip(log_kyx,      LOG_KYX_MIN,      LOG_KYX_MAX)
    log_ksat_uyc = np.clip(log_ksat_uyc, LOG_KSAT_UYC_MIN, LOG_KSAT_UYC_MAX)

    ksat_wyc = 10**log_ksat
    kyx_awyc = 10**log_kyx
    ksat_uyc = 10**log_ksat_uyc

    iter_count[0] += 1
    idx = iter_count[0]
    print(f"  Trial {idx:3d}: Ksat_WYC={ksat_wyc:.3e}  "
          f"KYX={kyx_awyc:.0f}  Ksat_UYC={ksat_uyc:.3e}",
          end="", flush=True)

    trial_dir = None
    try:
        t0 = time.time()
        work_gsz, trial_dir = create_work_gsz(
            rain_points, idx, ksat_wyc, kyx_awyc, ksat_uyc)

        run_solver(solver_exe, work_gsz)
        dt = time.time() - t0

        time_steps = read_time_steps(work_gsz)
        saved = list_saved_steps(work_gsz)
        if not time_steps or not saved:
            print(f"  ->  No results ({dt:.0f}s)")
            return 9999.0

        time_for_step = {ts["step"]: ts["time_s"] for ts in time_steps}
        max_node = max(_sensor_nodes.values())
        sensor_cols = {"1.5m": "Suction_1.5m", "3.0m": "Suction_3m",
                       "5.0m": "Suction_5m"}

        sim_results = {}
        for step_idx in saved:
            if step_idx == 0:
                continue
            pwp = read_pore_pressure(work_gsz, step_idx)
            if pwp is None or max_node >= len(pwp):
                continue
            time_s = time_for_step.get(step_idx, 0)
            rel_s = max(0, time_s - 432000)
            data = {}
            for label, nidx in _sensor_nodes.items():
                if nidx < len(pwp) and np.isfinite(pwp[nidx]):
                    data[label] = pwp[nidx]
            if data:
                sim_results[rel_s] = data

        if not sim_results:
            print(f"  ->  No PWP ({dt:.0f}s)")
            return 9999.0

        # Compare to observations
        sim_keys = sorted(sim_results.keys())
        errors = []
        for ts, row in obs_daily.iterrows():
            elapsed_s = int((ts - obs_daily.index[0]).total_seconds())
            closest_t = min(sim_keys, key=lambda t: abs(t - elapsed_s))
            if abs(closest_t - elapsed_s) > 7 * 86400:
                continue
            sim = sim_results[closest_t]
            for label, col in sensor_cols.items():
                obs_kpa = row[col]
                if label not in sim or not np.isfinite(obs_kpa):
                    continue
                obs_psf = obs_kpa * KPA_TO_PSF
                sim_psf = sim[label]
                if np.isfinite(sim_psf):
                    errors.append((sim_psf - obs_psf)**2)

        rmse = np.sqrt(np.mean(errors)) if errors else 9999.0
        rmse_kpa = rmse / KPA_TO_PSF
        print(f"  ->  RMSE={rmse_kpa:.2f} kPa "
              f"[{len(errors)} pts] ({dt:.0f}s)")

        trial_log.append({
            "trial": idx,
            "log_ksat_wyc": round(log_ksat, 4),
            "log_kyx": round(log_kyx, 4),
            "log_ksat_uyc": round(log_ksat_uyc, 4),
            "ksat_wyc": ksat_wyc, "kyx_awyc": kyx_awyc,
            "ksat_uyc": ksat_uyc,
            "rmse_psf": round(rmse, 2),
            "rmse_kpa": round(rmse_kpa, 4),
            "n_pts": len(errors), "solve_s": round(dt, 1),
        })
        return rmse

    except Exception as e:
        print(f"  ->  ERROR: {e}")
        trial_log.append({
            "trial": idx,
            "log_ksat_wyc": round(log_ksat, 4),
            "log_kyx": round(log_kyx, 4),
            "log_ksat_uyc": round(log_ksat_uyc, 4),
            "ksat_wyc": 10**log_ksat, "kyx_awyc": 10**log_kyx,
            "ksat_uyc": 10**log_ksat_uyc,
            "rmse_psf": 9999.0, "rmse_kpa": 9999.0,
            "n_pts": 0, "solve_s": 0,
        })
        return 9999.0

    finally:
        if trial_dir:
            try:
                shutil.rmtree(trial_dir)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 65)
    print("SEEP/W Hydraulic Calibration - Direct Suction Match (v4)")
    print("  Architecture: XML patch + GeoCmd.exe (no GSI in loop)")
    print(f"  Cal window : {CALIB_START} to {CALIB_END}")
    print(f"  Sensors    : 1.5m, 3.0m, 5.0m (suction kPa)")
    print(f"  Model      : ft / psf  (1 kPa = {KPA_TO_PSF:.4f} psf)")
    print(f"  Output     : {os.path.basename(OUT_GSZ)}")
    print("=" * 65)

    for p, lbl in [(BASE_GSZ, "Base GSZ"), (SENSOR_CSV, "Sensor CSV")]:
        if not os.path.exists(p):
            print(f"\nERROR: {lbl} not found: {p}")
            sys.exit(1)

    solver_exe = find_solver()
    if solver_exe is None:
        print("\nERROR: GeoCmd.exe not found. Set SOLVER_OVERRIDE in CONFIG.")
        sys.exit(1)
    print(f"\n  Solver: {solver_exe}")

    print("\nLoading sensor data...")
    obs_daily = load_sensor_data()

    rain_points = build_rainfall_time_series(obs_daily)
    print(f"  Rainfall: {len(rain_points)} weekly points")

    global _sensor_nodes
    print("\nLocating mesh nodes from Mesh.ply...")
    found = find_sensor_nodes(BASE_GSZ)
    if found is None or len(found) < 3:
        print("\nERROR: Could not find sensor nodes.")
        sys.exit(1)
    _sensor_nodes = found

    preflight_ok = run_preflight(solver_exe, rain_points, obs_daily)
    if not preflight_ok:
        resp = input("\nPre-flight FAILED. Continue anyway? (y/N): "
                     ).strip().lower()
        if resp != 'y':
            sys.exit(1)
    else:
        resp = input("\nProceed to calibration? (Y/n): ").strip().lower()
        if resp == 'n':
            return

    _cleanup_old_iters()

    x0 = np.array([
        np.log10(5.640e-06),
        np.log10(80827.0),
        np.log10(KSAT_UYC_BASE),
    ])
    print(f"\nStarting: Ksat_WYC={10**x0[0]:.3e}  "
          f"KYX={10**x0[1]:.0f}  Ksat_UYC={10**x0[2]:.3e}")
    print(f"Running optimisation (max {MAX_OPT_ITER} trials)...\n")

    result = minimize(
        objective, x0, args=(solver_exe, rain_points, obs_daily),
        method="Nelder-Mead",
        options={"maxiter": MAX_OPT_ITER, "xatol": 0.05, "fatol": 0.5}
    )

    print(f"\n{'=' * 65}")
    print("Optimisation complete")
    best_rmse_psf = result.fun
    print(f"  Status : {result.message}")
    print(f"  RMSE   : {best_rmse_psf/KPA_TO_PSF:.3f} kPa  "
          f"({best_rmse_psf:.1f} psf)")

    bk, bx, bu = 10**result.x[0], 10**result.x[1], 10**result.x[2]
    print(f"  Ksat_WYC = {bk:.3e} ft/s")
    print(f"  KYX_AWYC = {bx:.0f}")
    print(f"  Ksat_UYC = {bu:.3e} ft/s")

    if trial_log:
        pd.DataFrame(trial_log).to_csv(OUT_CSV, index=False)
        print(f"\nLog -> {OUT_CSV}")

    # Save final calibrated GSZ
    print(f"\nSaving -> {os.path.basename(OUT_GSZ)}")
    try:
        work_gsz, trial_dir = create_work_gsz(
            rain_points, 9999, bk, bx, bu)
        run_solver(solver_exe, work_gsz)
        shutil.copy2(work_gsz, OUT_GSZ)
        shutil.rmtree(trial_dir, ignore_errors=True)
        print(f"  Done -> {OUT_GSZ}")
    except Exception as e:
        print(f"  ERROR: {e}")

    _cleanup_old_iters()

    print(f"\n{'=' * 65}")
    print(f"NEXT: python calibrate_slope.py  "
          f"(BASE_GSZ = {os.path.basename(OUT_GSZ)})")
    print("=" * 65)


if __name__ == "__main__":
    main()