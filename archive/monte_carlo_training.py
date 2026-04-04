"""
Monte Carlo Training Data Generator
=====================================
Generates a training database for ML slope stability prediction.

Each iteration runs the full GeoStudio chain:
  Initial Condition (SEEP/W) -> Rainfall Simulation (SEEP/W) -> FS (SLOPE/W)

Inputs varied per iteration (Latin Hypercube Sampling):
  - Return period   : log-uniform 1-1000 years -> depth via NOAA Atlas 14
  - Storm duration  : 1-10 days (integer)
  - Temporal shape  : 0=front-loaded, 0.5=uniform, 1=back-loaded
  - Antecedent state: dry / normal / wet (from S2 sensor percentiles)

Antecedent state generates:
  - API_7d, API_14d, API_21d, API_30d (mm) as features AND
  - A 30-day pre-storm wetting sequence prepended to the rainfall function
    so GeoStudio actually sees different starting moisture conditions

Fixed per iteration:
  - All soil strength parameters (calibrated values)
  - Ksat and hydraulic functions (calibrated values)

Output CSV columns:
  iter, return_period_yr, storm_duration_days, shape_param, antecedent_state,
  total_depth_in, API_7d_mm, API_14d_mm, API_21d_mm, API_30d_mm,
  rain_day_01 .. rain_day_10 (in/day, storm days only),
  FS_Slope_Stability, FS_FS, converged

Checkpoint/resume:
  - Writes each row immediately on completion
  - On restart reads existing CSV and skips completed iterations

Usage:
    python monte_carlo_training.py
"""

import sys, os, glob, shutil, zipfile, csv, warnings, subprocess, time
import numpy as np
from datetime import datetime
from scipy.stats import qmc

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

FILE            = r"E:\Github\MCP_Geostudio\Metro-Center-calibrated.gsz"
N_ITER          = 2000
SEED            = 99
SLOPE_ANALYSES  = ["Slope Stability", "FS"]
ANALYSIS_SEEP   = "Rainfall Simulation"
ANALYSIS_INIT   = "Initial Condition"

OUT_DIR         = os.path.dirname(os.path.abspath(FILE))
OUT_CSV         = os.path.join(OUT_DIR, "training_data.csv")

SOLVER_TIMEOUT  = 900   # 15 min max per iteration
SOLVER_OVERRIDE = None  # set to full path if auto-detect fails

sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')

# ---------------------------------------------------------------------------
# NOAA Atlas 14 - Jackson MS - Partial Duration Series
# Depths in inches. Return periods in years.
# ---------------------------------------------------------------------------

NOAA_RETURN_PERIODS = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]

# Duration in days -> depth array matching NOAA_RETURN_PERIODS
NOAA_IDF = {
    1:  [3.77, 4.36, 5.34, 6.18, 7.38, 8.32, 9.28, 10.3,  11.7, 12.7],
    2:  [4.38, 5.04, 6.12, 7.03, 8.29, 9.28, 10.3, 11.3,  12.6, 13.7],
    3:  [4.82, 5.50, 6.61, 7.54, 8.83, 9.83, 10.8, 11.9,  13.2, 14.3],
    4:  [5.21, 5.89, 7.01, 7.95, 9.25, 10.3, 11.3, 12.3,  13.7, 14.7],
    7:  [6.20, 6.90, 8.05, 9.01, 10.3, 11.4, 12.4, 13.4,  14.8, 15.9],
    10: [7.04, 7.78, 8.99, 10.0, 11.4, 12.5, 13.5, 14.6,  16.1, 17.2],
}

# Antecedent state thresholds from S2 sensor (7-day API in mm, P25/P75)
# Dry < 4mm | Normal 4-50mm | Wet > 50mm
API_BOUNDS = {
    "dry":    (0,   4,    0,  15,   0,  30,   0,  88),
    "normal": (4,  50,   10,  80,  20, 120,  88, 200),
    "wet":    (50, 150,  70, 200, 100, 280, 200, 350),
}

# Calibrated soil parameters are read directly from the GSZ at startup.
# See read_calibrated_params() - do not hardcode here.

# ---------------------------------------------------------------------------
# Solver detection
# ---------------------------------------------------------------------------

def find_solver():
    if SOLVER_OVERRIDE and os.path.exists(SOLVER_OVERRIDE):
        return SOLVER_OVERRIDE
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
# Read calibrated soil parameters from GSZ
# ---------------------------------------------------------------------------

def read_calibrated_params():
    """
    Read c' and phi' for all SLOPE/W materials directly from the
    calibrated GSZ. These values are never modified - just read once
    at startup for metadata logging and verification.
    """
    import PyGeoStudio as pgs
    study = pgs.GeoStudioFile(FILE)
    mat_names = [
        "Silty Clay",
        "Weathered Yazoo Clay in Active Zone",
        "Weathered Yazoo Clay",
        "Unweathered Yazoo Clay",
    ]
    params = {}
    for mat in study.materials:
        name = mat.data.get("Name")
        if name not in mat_names:
            continue
        ss = mat.data.get("StressStrain")
        if ss is None or not ss.data:
            continue
        c   = ss.data.get("CohesionPrime")
        phi = ss.data.get("PhiPrime")
        if c is not None and phi is not None:
            params[name] = {"cohesion": float(c), "phi": float(phi)}
    return params


# ---------------------------------------------------------------------------
# NOAA IDF interpolation
# ---------------------------------------------------------------------------

def interpolate_depth(return_period_yr, duration_days):
    """
    Interpolate rainfall depth (inches) from NOAA Atlas 14.
    Uses log-log interpolation on both axes.
    """
    rp  = np.clip(return_period_yr, 1.0, 1000.0)
    dur = np.clip(duration_days,    1.0,   10.0)

    rp_arr  = np.array(NOAA_RETURN_PERIODS, dtype=float)
    dur_arr = np.array(sorted(NOAA_IDF.keys()), dtype=float)

    # Bounding durations
    if dur <= dur_arr[0]:
        d_lo = d_hi = dur_arr[0]
    elif dur >= dur_arr[-1]:
        d_lo = d_hi = dur_arr[-1]
    else:
        d_lo = dur_arr[dur_arr <= dur][-1]
        d_hi = dur_arr[dur_arr >= dur][0]

    def interp_rp(depths):
        return float(10 ** np.interp(
            np.log10(rp),
            np.log10(rp_arr),
            np.log10(np.array(depths, dtype=float))
        ))

    if d_lo == d_hi:
        return interp_rp(NOAA_IDF[int(d_lo)])

    depth_lo = interp_rp(NOAA_IDF[int(d_lo)])
    depth_hi = interp_rp(NOAA_IDF[int(d_hi)])
    t = ((np.log10(dur) - np.log10(d_lo)) /
         (np.log10(d_hi) - np.log10(d_lo)))
    return float(10 ** (np.log10(depth_lo) + t * (np.log10(depth_hi) - np.log10(depth_lo))))


# ---------------------------------------------------------------------------
# Antecedent API sampling
# ---------------------------------------------------------------------------

def sample_antecedent_apis(state, iter_idx):
    """
    Sample API_7d, API_14d, API_21d, API_30d (mm) for given antecedent state.
    Enforces monotonically increasing constraint.
    """
    rng = np.random.default_rng(SEED + iter_idx)
    b   = API_BOUNDS[state]
    a7  = rng.uniform(b[0], b[1])
    a14 = rng.uniform(max(b[2], a7),  b[3])
    a21 = rng.uniform(max(b[4], a14), b[5])
    a30 = rng.uniform(max(b[6], a21), b[7])
    return {
        "API_7d":  round(a7,  1),
        "API_14d": round(a14, 1),
        "API_21d": round(a21, 1),
        "API_30d": round(a30, 1),
    }


# ---------------------------------------------------------------------------
# Rainfall construction
# ---------------------------------------------------------------------------

def distribute_storm(total_depth_in, n_days, shape):
    """
    Distribute total storm depth across n_days.
    shape=0 front-loaded, shape=0.5 uniform, shape=1 back-loaded.
    Returns array of length n_days (in/day).
    """
    if n_days == 1:
        return np.array([total_depth_in])
    days = np.linspace(0, 1, n_days)
    if abs(shape - 0.5) < 0.01:
        weights = np.ones(n_days)
    elif shape < 0.5:
        alpha   = 4.0 * (0.5 - shape)
        weights = np.exp(-alpha * days)
    else:
        alpha   = 4.0 * (shape - 0.5)
        weights = np.exp(alpha * days)
    weights /= weights.sum()
    return total_depth_in * weights


def build_full_rainfall(storm_depths_in, apis_mm):
    """
    Build complete rainfall time series:
      Days  0-29 : antecedent pre-wetting derived from API values
      Days 30 to 30+storm_duration-1 : main storm
      Days 30+storm_duration to +4   : recession

    All time values in seconds from t=0.
    Y values in in/day.
    Returns (list of {X, Y} dicts, total_days int).
    """
    mm_to_in = 1.0 / 25.4
    n_storm  = len(storm_depths_in)

    # Convert API cumulative totals to incremental block totals (inches)
    a7  = apis_mm["API_7d"]  * mm_to_in
    a14 = apis_mm["API_14d"] * mm_to_in
    a21 = apis_mm["API_21d"] * mm_to_in
    a30 = apis_mm["API_30d"] * mm_to_in

    block_23_29 = a7                   # 7 days
    block_16_22 = max(0.0, a14 - a7)   # 7 days
    block_9_15  = max(0.0, a21 - a14)  # 7 days
    block_0_8   = max(0.0, a30 - a21)  # 9 days

    antecedent = np.zeros(30)
    antecedent[23:30] = block_23_29 / 7.0
    antecedent[16:23] = block_16_22 / 7.0
    antecedent[9:16]  = block_9_15  / 7.0
    antecedent[0:9]   = block_0_8   / 9.0

    recession  = np.full(5, 0.01)
    all_depths = np.concatenate([antecedent, storm_depths_in, recession])
    total_days = len(all_depths)

    points = []
    for day_idx in range(total_days):
        points.append({
            "X": str(day_idx * 86400),
            "Y": max(0.0, float(all_depths[day_idx]))
        })
    return points, total_days


# ---------------------------------------------------------------------------
# XML patching
# ---------------------------------------------------------------------------

def patch_rainfall_xml(xml_str, rain_points):
    """Replace rainfall function points."""
    start_tag = "<n>rainfall</n>"
    pos = xml_str.find(start_tag)
    if pos == -1:
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


def patch_analysis_duration(xml_str, analysis_name, total_days):
    """
    Update the Duration of the named SEEP/W transient analysis.
    Finds the analysis block by name and patches its TimeIncrement Duration.
    GeoStudio uses <Name>...</Name> for analysis names, not <n>.
    """
    total_seconds = total_days * 86400
    # Analyses use <Name>...</Name> tag in GeoStudio XML
    pos = xml_str.find(f"<Name>{analysis_name}</Name>")
    if pos == -1:
        print(f"  WARNING: '{analysis_name}' not found - duration not patched")
        return xml_str
    ti_pos = xml_str.find("<TimeIncrements", pos)
    if ti_pos == -1:
        return xml_str
    dur_start = xml_str.find("<Duration>", ti_pos)
    dur_end   = xml_str.find("</Duration>", dur_start) + len("</Duration>")
    if dur_start == -1 or dur_end == -1:
        return xml_str
    return (xml_str[:dur_start]
            + f"<Duration>{total_seconds}</Duration>"
            + xml_str[dur_end:])

# patch_soil_params removed - calibrated GSZ already has correct values.


# ---------------------------------------------------------------------------
# Temp GSZ preparation
# ---------------------------------------------------------------------------

def prepare_temp_gsz(iter_idx, rain_points, total_days):
    """Copy calibrated GSZ and apply all patches."""
    temp_dir = os.path.join(OUT_DIR, f"_train_{iter_idx:05d}")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    gsz_name = os.path.basename(FILE)
    gsz_stem = os.path.splitext(gsz_name)[0]
    temp_gsz = os.path.join(temp_dir, gsz_name)
    shutil.copy2(FILE, temp_gsz)

    analysis_folders = SLOPE_ANALYSES + [ANALYSIS_INIT, ANALYSIS_SEEP]

    with zipfile.ZipFile(temp_gsz, "r") as zin:
        all_items = zin.infolist()
        all_data  = {item.filename: zin.read(item.filename) for item in all_items}

    root_xml_key = next(
        (k for k in all_data
         if k.endswith(gsz_stem + ".xml")
         and not any(k.startswith(af + "/") for af in analysis_folders)),
        None
    )
    if root_xml_key is None:
        raise RuntimeError("Root XML not found")

    xml_str = all_data[root_xml_key].decode("utf-8")
    xml_str = patch_rainfall_xml(xml_str, rain_points)
    xml_str = patch_analysis_duration(xml_str, ANALYSIS_SEEP, total_days)

    with zipfile.ZipFile(temp_gsz, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data  = all_data[fname]
            if gsz_stem + ".xml" in fname:
                fixed_name = gsz_stem + ".xml"
                for af in analysis_folders:
                    if fname.startswith(af + "/") or ("/" + af + "/") in fname:
                        fixed_name = af + "/" + gsz_stem + ".xml"
                        break
                item.filename = fixed_name
                if fixed_name == gsz_stem + ".xml":
                    data = xml_str.encode("utf-8")
            zout.writestr(item, data)

    return temp_gsz, temp_dir


# ---------------------------------------------------------------------------
# Solver and FS extraction
# ---------------------------------------------------------------------------

def run_solver(solver_exe, temp_gsz):
    result = subprocess.run(
        [solver_exe, "/solve", temp_gsz],
        capture_output=True, text=True, timeout=SOLVER_TIMEOUT
    )
    if result.returncode != 0:
        raise RuntimeError(f"Solver {result.returncode}: {result.stderr or result.stdout}")


def extract_fs(temp_gsz, analysis_name):
    """Extract critical FS from lambdafos CSV in solved archive."""
    with zipfile.ZipFile(temp_gsz, "r") as z:
        files    = z.namelist()
        prefix   = analysis_name + "/"
        lf_files = [f for f in files
                    if f.startswith(prefix) and "lambdafos_" in f and f.endswith(".csv")]
        if not lf_files:
            return None
        with z.open(lf_files[0]) as f:
            lines = f.read().decode("utf-8").splitlines()

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


def cleanup_temp(temp_dir):
    for _ in range(10):
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            return
        except Exception:
            time.sleep(2)


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def get_completed_iters():
    completed = set()
    if not os.path.exists(OUT_CSV):
        return completed
    try:
        with open(OUT_CSV, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.strip().split(",")
                try:
                    completed.add(int(parts[0]))
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    return completed


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def build_header():
    cols = [
        "iter", "return_period_yr", "storm_duration_days",
        "shape_param", "antecedent_state", "total_depth_in",
        "API_7d_mm", "API_14d_mm", "API_21d_mm", "API_30d_mm",
    ]
    for d in range(1, 11):
        cols.append(f"rain_day_{d:02d}_in")
    cols += ["FS_Slope_Stability", "FS_FS", "converged"]
    return cols


def write_metadata(f, writer, header, calibrated_params):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Monte Carlo Training Database - GeoStudio SEEP/W + SLOPE/W",
        f"# Generated   : {now}",
        f"# Input file  : {FILE}",
        f"# Iterations  : {N_ITER}",
        f"# Seed        : {SEED}",
        "# Sampling    : Latin Hypercube 4D",
        "# NOAA Atlas 14 : Jackson MS | Partial Duration Series",
        "#",
        "# Fixed soil parameters (read from calibrated GSZ):",
    ]
    for name, p in calibrated_params.items():
        lines.append(f"#   {name}: c={p['cohesion']} psf, phi={p['phi']} deg")
    lines += [
        "#",
        "# Antecedent states (7-day API thresholds from S2 sensor):",
        "#   Dry:    API_7d < 4 mm",
        "#   Normal: 4 <= API_7d <= 50 mm",
        "#   Wet:    API_7d > 50 mm",
        "#",
        "# Units: depths=inches, API=mm, FS=dimensionless",
        "#",
    ]
    for line in lines:
        f.write(line + "\n")
    writer.writeheader()


def build_row(i, rp, duration, shape, state, total_depth,
              apis, storm_daily, fs_slope, fs_fs):
    row = {
        "iter":                i,
        "return_period_yr":    round(rp, 3),
        "storm_duration_days": duration,
        "shape_param":         round(shape, 4),
        "antecedent_state":    state,
        "total_depth_in":      round(total_depth, 4),
        "API_7d_mm":           apis["API_7d"],
        "API_14d_mm":          apis["API_14d"],
        "API_21d_mm":          apis["API_21d"],
        "API_30d_mm":          apis["API_30d"],
    }
    for d in range(1, 11):
        key = f"rain_day_{d:02d}_in"
        row[key] = round(float(storm_daily[d-1]), 6) if d <= len(storm_daily) else 0.0
    row["FS_Slope_Stability"] = round(fs_slope, 6) if fs_slope is not None else "N/A"
    row["FS_FS"]               = round(fs_fs,    6) if fs_fs    is not None else "N/A"
    row["converged"]           = 1 if (fs_slope is not None and fs_fs is not None) else 0
    return row


# ---------------------------------------------------------------------------
# LHS sampling
# ---------------------------------------------------------------------------

def generate_lhs_samples():
    sampler = qmc.LatinHypercube(d=4, seed=SEED)
    return sampler.random(n=N_ITER)


def transform_sample(s):
    """Transform [0,1]^4 LHS sample to physical parameters."""
    rp       = float(10 ** (s[0] * 3.0))        # 1 to 1000 yr
    duration = int(np.clip(round(1 + s[1] * 9), 1, 10))  # 1 to 10 days
    shape    = float(s[2])                       # 0 to 1
    state    = "dry" if s[3] < 0.25 else ("wet" if s[3] >= 0.75 else "normal")
    return rp, duration, shape, state


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 65)
    print("Monte Carlo Training Data Generator")
    print(f"  File       : {FILE}")
    print(f"  Iterations : {N_ITER}")
    print(f"  Output     : {OUT_CSV}")
    print(f"  Sampling   : Latin Hypercube (4D)")
    print("=" * 65)

    if not os.path.exists(FILE):
        print(f"\nERROR: Calibrated GSZ not found: {FILE}")
        sys.exit(1)

    solver = find_solver()
    if solver is None:
        print("\nERROR: GeoStudio solver not found.")
        sys.exit(1)
    print(f"\n  Solver : {solver}")

    print("\nReading calibrated soil parameters from GSZ...")
    calibrated_params = read_calibrated_params()
    for name, p in calibrated_params.items():
        print(f"  {name}: c={p['cohesion']} psf, phi={p['phi']} deg")
    if not calibrated_params:
        print("  WARNING: no soil parameters found - check GSZ file")

    print("\nGenerating LHS samples...")
    lhs       = generate_lhs_samples()
    completed = get_completed_iters()
    remaining = N_ITER - len(completed)

    if completed:
        print(f"  Resuming - {len(completed)} done, {remaining} remaining")
    else:
        print(f"  Fresh start - {N_ITER} iterations to run")

    header      = build_header()
    file_exists = os.path.exists(OUT_CSV) and len(completed) > 0
    failed      = 0
    success     = 0

    with open(OUT_CSV, "a" if file_exists else "w",
              newline="", encoding="utf-8") as csvfile:

        writer = csv.DictWriter(csvfile, fieldnames=header)
        if not file_exists:
            write_metadata(csvfile, writer, header, calibrated_params)

        for i in range(1, N_ITER + 1):
            if i in completed:
                continue

            rp, duration, shape, state = transform_sample(lhs[i - 1])
            total_depth  = interpolate_depth(rp, duration)
            apis         = sample_antecedent_apis(state, i)
            storm_daily  = distribute_storm(total_depth, duration, shape)
            rain_points, total_days = build_full_rainfall(storm_daily, apis)

            temp_dir = None
            fs_slope = None
            fs_fs    = None

            try:
                temp_gsz, temp_dir = prepare_temp_gsz(i, rain_points, total_days)
                run_solver(solver, temp_gsz)
                fs_slope = extract_fs(temp_gsz, "Slope Stability")
                fs_fs    = extract_fs(temp_gsz, "FS")

                row = build_row(i, rp, duration, shape, state,
                                total_depth, apis, storm_daily, fs_slope, fs_fs)
                writer.writerow(row)
                csvfile.flush()
                success += 1

                fs_s = f"{fs_slope:.4f}" if fs_slope else "N/A"
                fs_f = f"{fs_fs:.4f}"    if fs_fs    else "N/A"
                print(f"  [{i:>5}/{N_ITER}]  "
                      f"RP={rp:7.1f}yr  dur={duration:2d}d  "
                      f"{state:<6}  depth={total_depth:.2f}in  "
                      f"FS_SS={fs_s}  FS_FS={fs_f}")

            except Exception as e:
                failed += 1
                row = build_row(i, rp, duration, shape, state,
                                total_depth, apis, storm_daily, None, None)
                writer.writerow(row)
                csvfile.flush()
                print(f"  [{i:>5}/{N_ITER}]  FAILED - {e}")

            finally:
                if temp_dir:
                    cleanup_temp(temp_dir)

    # Stray folder cleanup
    for item in os.listdir(OUT_DIR):
        if item.startswith("_train_"):
            cleanup_temp(os.path.join(OUT_DIR, item))

    print(f"\n{'=' * 65}")
    print(f"Completed : {success} succeeded | {failed} failed")
    print(f"Output    : {OUT_CSV}")

    # Summary stats from output file
    fs_vals = []
    try:
        with open(OUT_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(
                (l for l in f if not l.startswith("#"))
            )
            for row in reader:
                try:
                    v = float(row["FS_FS"])
                    if 0 < v < 10:
                        fs_vals.append(v)
                except (ValueError, KeyError):
                    continue
    except Exception:
        pass

    if fs_vals:
        arr = np.array(fs_vals)
        print(f"\n--- FS (coupled SEEP+SLOPE) summary across {len(arr)} samples ---")
        print(f"  Mean        : {arr.mean():.4f}")
        print(f"  Std         : {arr.std():.4f}")
        print(f"  Min / Max   : {arr.min():.4f} / {arr.max():.4f}")
        print(f"  P(FS < 1.0) : {(arr < 1.0).mean()*100:.2f}%")
        print(f"  P(FS < 1.5) : {(arr < 1.5).mean()*100:.2f}%")


if __name__ == "__main__":
    main()
