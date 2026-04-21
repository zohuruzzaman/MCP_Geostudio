"""
Monte Carlo Training Data Generator - Site-Specific Surrogate
==============================================================
Generates training database for ML slope stability prediction.
Site: Metro Center, Jackson MS (Yazoo Clay)

Each iteration runs the full GeoStudio chain:
  Initial Condition (SEEP/W) -> Rainfall Simulation (SEEP/W) -> FS (SLOPE/W)

Inputs varied per iteration (Latin Hypercube Sampling, 4D):
  - Return period   : log-uniform 1-1000 years -> depth via NOAA Atlas 14
  - Storm duration  : 1-10 days (integer)
  - Temporal shape  : 0=front-loaded, 0.5=uniform, 1=back-loaded
  - Antecedent state: dry / normal / wet (from S2 sensor percentiles)

Fixed per iteration (read from calibrated GSZ, never modified):
  - All soil strength parameters (c', phi')
  - Ksat, KYXRatio, SWCC
  - Geometry, mesh, boundary conditions

Extracted per iteration:
  - Minimum FS across all time steps (from coupled "FS" SLOPE/W analysis)
  - Full mesh PWP at the min-FS time step (all nodes from SEEP/W)

Time stepping:
  - Antecedent (30 days): every 3 days
  - Storm (1-10 days):    every 1 day
  - Recession (5 days):   every 2 days

Output CSV: one row per iteration with rainfall params + 201 PWP values + FS

Fixes incorporated from calibration diagnostic:
  - Rainfall tag: ">rainfall<" (case-insensitive)
  - TimeIncrements: fallback regex for attributed tags
  - FS extraction: handles both folder-per-step and flat layouts
  - PWP reading: folder-per-step pattern ({NNN}/node.csv)
  - No folder stripping (breaks material assignments)

Usage:
    python monte_carlo_training.py
"""

import sys, os, re, glob, shutil, zipfile, csv, io, warnings, subprocess, time
import numpy as np
from datetime import datetime
from scipy.stats import qmc

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

FILE            = r"E:\Github\MCP_Geostudio\calibration\Metro-Center-slope-final.gsz"
N_ITER          = 10     # set to 2000 for production run
SEED            = 99
ANALYSIS_FS     = "FS"                    # coupled SLOPE/W (parent = Rainfall Simulation)
ANALYSIS_SEEP   = "Rainfall Simulation"   # transient SEEP/W
ANALYSIS_INIT   = "Initial Condition"     # steady-state SEEP/W

OUT_DIR         = r"E:\Github\MCP_Geostudio\training"
OUT_CSV         = os.path.join(OUT_DIR, "training_data.csv")

SOLVER_TIMEOUT  = 900   # 15 min max per iteration
SOLVER_OVERRIDE = None

# Time stepping (days)
ANTECEDENT_STEP_DAYS = 3
STORM_STEP_DAYS      = 1
RECESSION_STEP_DAYS  = 2

# NOAA Atlas 14 - Jackson MS - Partial Duration Series
# Depths in inches. Return periods in years.
NOAA_RETURN_PERIODS = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]
NOAA_IDF = {
    1:  [3.77, 4.36, 5.34, 6.18, 7.38, 8.32, 9.28, 10.3,  11.7, 12.7],
    2:  [4.38, 5.04, 6.12, 7.03, 8.29, 9.28, 10.3, 11.3,  12.6, 13.7],
    3:  [4.82, 5.50, 6.61, 7.54, 8.83, 9.83, 10.8, 11.9,  13.2, 14.3],
    4:  [5.21, 5.89, 7.01, 7.95, 9.25, 10.3, 11.3, 12.3,  13.7, 14.7],
    7:  [6.20, 6.90, 8.05, 9.01, 10.3, 11.4, 12.4, 13.4,  14.8, 15.9],
    10: [7.04, 7.78, 8.99, 10.0, 11.4, 12.5, 13.5, 14.6,  16.1, 17.2],
}

# Antecedent state thresholds (7-day API in mm, from S2 sensor)
API_BOUNDS = {
    "dry":    (0,   4,    0,  15,   0,  30,   0,  88),
    "normal": (4,  50,   10,  80,  20, 120,  88, 200),
    "wet":    (50, 150,  70, 200, 100, 280, 200, 350),
}

# PyGeoStudio path (only used for reading calibrated params at startup)
sys.path.insert(0, r"C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages")


# ---------------------------------------------------------------------------
# Solver detection
# ---------------------------------------------------------------------------

def find_solver():
    if SOLVER_OVERRIDE and os.path.exists(SOLVER_OVERRIDE):
        return SOLVER_OVERRIDE
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


def run_solver(solver_exe, gsz_path):
    result = subprocess.run(
        [solver_exe, "/solve", gsz_path],
        capture_output=True, text=True, timeout=SOLVER_TIMEOUT
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Solver exit {result.returncode}: "
            f"{(result.stderr or result.stdout)[:300]}")


# ---------------------------------------------------------------------------
# Read calibrated params (metadata only - never modified)
# ---------------------------------------------------------------------------

def read_calibrated_params():
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
    """Log-log interpolation on both axes."""
    rp  = np.clip(return_period_yr, 1.0, 1000.0)
    dur = np.clip(duration_days,    1.0,   10.0)

    rp_arr  = np.array(NOAA_RETURN_PERIODS, dtype=float)
    dur_arr = np.array(sorted(NOAA_IDF.keys()), dtype=float)

    if dur <= dur_arr[0]:
        d_lo = d_hi = dur_arr[0]
    elif dur >= dur_arr[-1]:
        d_lo = d_hi = dur_arr[-1]
    else:
        d_lo = dur_arr[dur_arr <= dur][-1]
        d_hi = dur_arr[dur_arr >= dur][0]

    def interp_rp(depths):
        return float(10 ** np.interp(
            np.log10(rp), np.log10(rp_arr),
            np.log10(np.array(depths, dtype=float))
        ))

    if d_lo == d_hi:
        return interp_rp(NOAA_IDF[int(d_lo)])

    depth_lo = interp_rp(NOAA_IDF[int(d_lo)])
    depth_hi = interp_rp(NOAA_IDF[int(d_hi)])
    t = ((np.log10(dur) - np.log10(d_lo)) /
         (np.log10(d_hi) - np.log10(d_lo)))
    return float(10 ** (np.log10(depth_lo) +
                        t * (np.log10(depth_hi) - np.log10(depth_lo))))


# ---------------------------------------------------------------------------
# Antecedent API sampling
# ---------------------------------------------------------------------------

def sample_antecedent_apis(state, iter_idx):
    """Sample API_7d..30d (mm). Monotonically increasing."""
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
    """Distribute storm depth. shape: 0=front, 0.5=uniform, 1=back."""
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
      Days  0-29 : antecedent pre-wetting from API values
      Days 30 to 30+n_storm-1 : main storm
      Days 30+n_storm to +4   : recession (0.01 in/day)
    Returns (list of {X, Y} dicts, total_days).
    """
    mm_to_in = 1.0 / 25.4
    n_storm  = len(storm_depths_in)

    a7  = apis_mm["API_7d"]  * mm_to_in
    a14 = apis_mm["API_14d"] * mm_to_in
    a21 = apis_mm["API_21d"] * mm_to_in
    a30 = apis_mm["API_30d"] * mm_to_in

    block_23_29 = a7
    block_16_22 = max(0.0, a14 - a7)
    block_9_15  = max(0.0, a21 - a14)
    block_0_8   = max(0.0, a30 - a21)

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
# Time increment builder (variable step size)
# ---------------------------------------------------------------------------

def build_time_steps(total_days, n_storm):
    """
    Build list of (elapsed_seconds, save_flag) for the TimeIncrements block.
    Antecedent (day 0-29): every ANTECEDENT_STEP_DAYS
    Storm (day 30 to 30+n_storm-1): every STORM_STEP_DAYS
    Recession (after storm to total_days): every RECESSION_STEP_DAYS
    """
    steps = []
    day = 0

    # Antecedent phase: days 0-29
    while day < 30:
        day += ANTECEDENT_STEP_DAYS
        if day > 30:
            day = 30
        steps.append(day * 86400)

    # Storm phase: days 30 to 30+n_storm
    storm_end = 30 + n_storm
    d = 30
    while d < storm_end:
        d += STORM_STEP_DAYS
        if d > storm_end:
            d = storm_end
        steps.append(d * 86400)

    # Recession phase
    d = storm_end
    while d < total_days:
        d += RECESSION_STEP_DAYS
        if d > total_days:
            d = total_days
        steps.append(d * 86400)

    # Deduplicate and sort
    steps = sorted(set(steps))
    return steps


# ---------------------------------------------------------------------------
# XML patching (all fixes from calibration diagnostic)
# ---------------------------------------------------------------------------

def _patch_rainfall(xml_text, rain_points):
    """Patch rainfall climate function points.
    Uses case-insensitive search for the rainfall tag (proven in calibration)."""
    # Case-insensitive find - position is same in original string
    idx = xml_text.lower().find(">rainfall<")
    if idx == -1:
        raise RuntimeError("'>rainfall<' not found in XML")
    chunk_end = xml_text.find("</ClimateFn>", idx)
    if chunk_end == -1:
        raise RuntimeError("</ClimateFn> not found after rainfall tag")
    chunk = xml_text[idx:chunk_end]
    new_pts = f'<Points Len="{len(rain_points)}">\n'
    for pt in rain_points:
        new_pts += (f'            <Point X="{pt["X"]}" '
                    f'Y="{pt["Y"]:.8f}" />\n')
    new_pts += "          </Points>"
    new_chunk = re.sub(r'<Points.*?</Points>', new_pts, chunk,
                       count=1, flags=re.DOTALL)
    if new_chunk == chunk:
        raise RuntimeError("Rainfall <Points> regex did not match")
    return xml_text[:idx] + new_chunk + xml_text[chunk_end:]


def _patch_time_increments(xml_text, analysis_name, time_steps_s,
                           total_duration_s):
    """
    Replace TimeIncrements for a named analysis.
    time_steps_s: list of elapsed seconds for each save point.
    Fallback regex for tags with attributes.
    """
    idx = xml_text.find(f">{analysis_name}<")
    if idx == -1:
        return xml_text  # analysis not found - skip silently

    chunk_end = xml_text.find("</Analysis>", idx)
    chunk = xml_text[idx:chunk_end]

    # Check if this analysis even has TimeIncrements
    if "<TimeIncrements" not in chunk:
        return xml_text

    n_steps = len(time_steps_s)
    new_ti = (f"<TimeIncrements>\n"
              f"        <Start>0</Start>\n"
              f"        <Duration>{total_duration_s}</Duration>\n"
              f"        <IncrementOption>Exponential</IncrementOption>\n"
              f"        <IncrementCount>{n_steps}</IncrementCount>\n"
              f'        <TimeSteps Len="{n_steps}">\n')

    prev = 0
    for elapsed in time_steps_s:
        step = elapsed - prev
        new_ti += (f'          <TimeStep Step="{step}" '
                   f'ElapsedTime="{elapsed}" Save="true" />\n')
        prev = elapsed
    new_ti += "        </TimeSteps>\n      </TimeIncrements>"

    old_chunk = chunk

    # Try bare tag first
    chunk = re.sub(r'<TimeIncrements>.*?</TimeIncrements>',
                   new_ti, chunk, count=1, flags=re.DOTALL)

    if chunk == old_chunk:
        # Fallback: tag with attributes
        chunk = re.sub(r'<TimeIncrements[^>]*>.*?</TimeIncrements>',
                       new_ti, old_chunk, count=1, flags=re.DOTALL)
        if chunk == old_chunk:
            return xml_text  # couldn't patch - skip

    return xml_text[:idx] + chunk + xml_text[chunk_end:]


# ---------------------------------------------------------------------------
# Temp GSZ preparation
# ---------------------------------------------------------------------------

def prepare_temp_gsz(iter_idx, rain_points, total_days, n_storm):
    """Copy calibrated GSZ, patch rainfall + time increments, strip old results."""
    temp_dir = os.path.join(OUT_DIR, f"_train_{iter_idx:05d}")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    gsz_name = os.path.basename(FILE)
    temp_gsz = os.path.join(temp_dir, gsz_name)

    # Read base archive
    with zipfile.ZipFile(FILE, "r") as zin:
        all_items = zin.infolist()
        all_data  = {item.filename: zin.read(item.filename)
                     for item in all_items}

    # Find root XML - the one NOT in an analysis subfolder
    analysis_folders = [ANALYSIS_FS, ANALYSIS_SEEP, ANALYSIS_INIT,
                        "Slope Stability"]
    root_xml_key = next(
        (k for k in all_data
         if k.endswith(".xml") and "/" not in k),
        None
    )
    if root_xml_key is None:
        raise RuntimeError(f"Root XML not found in {gsz_name}")

    xml_str = all_data[root_xml_key].decode("utf-8")

    # Build time steps
    time_steps_s = build_time_steps(total_days, n_storm)
    total_duration_s = total_days * 86400

    # Patch rainfall
    xml_str = _patch_rainfall(xml_str, rain_points)

    # Patch time increments for BOTH transient analyses
    xml_str = _patch_time_increments(xml_str, ANALYSIS_SEEP,
                                     time_steps_s, total_duration_s)
    xml_str = _patch_time_increments(xml_str, ANALYSIS_FS,
                                     time_steps_s, total_duration_s)

    # Prefixes for stale result folders - strip so solver regenerates
    result_prefixes = tuple(af + "/" for af in analysis_folders)

    # Write patched archive
    skipped = 0
    with zipfile.ZipFile(temp_gsz, "w",
                         compression=zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data  = all_data[fname]

            # Skip stale result folders
            if fname.startswith(result_prefixes):
                skipped += 1
                continue

            # Replace root XML with patched version
            if fname == root_xml_key:
                data = xml_str.encode("utf-8")

            zout.writestr(item, data)

    return temp_gsz, temp_dir


# ---------------------------------------------------------------------------
# Result extraction
# ---------------------------------------------------------------------------

def extract_fs_all_steps(gsz_path):
    """
    Extract FS at each saved time step from the coupled SLOPE/W analysis.
    Handles both folder-per-step (GeoStudio 2025) and flat layout.
    Returns dict: {step_idx: fs_value}
    """
    prefix = ANALYSIS_FS + "/"
    fs_by_step = {}

    with zipfile.ZipFile(gsz_path, "r") as z:
        all_files = z.namelist()

        # Strategy 1: folder-per-step (FS/000/, FS/001/, ...)
        step_folders = set()
        for f in all_files:
            if (f.startswith(prefix) and "lambdafos_" in f
                    and f.endswith(".csv")):
                parts = f[len(prefix):].split("/")
                if len(parts) == 2:
                    # FS/NNN/lambdafos_xxx.csv
                    try:
                        step_folders.add(int(parts[0]))
                    except ValueError:
                        pass
                elif len(parts) == 1:
                    # FS/lambdafos_xxx.csv (flat - single step)
                    step_folders.add(-1)  # sentinel for flat

        for step_idx in sorted(step_folders):
            if step_idx == -1:
                # Flat layout
                lf_files = [f for f in all_files
                            if f.startswith(prefix)
                            and "lambdafos_" in f
                            and f.endswith(".csv")
                            and f.count("/") == 1]
            else:
                step_prefix = f"{prefix}{step_idx:03d}/"
                lf_files = [f for f in all_files
                            if f.startswith(step_prefix)
                            and "lambdafos_" in f
                            and f.endswith(".csv")]

            if not lf_files:
                continue

            # Read first lambdafos file, find converged FS
            content = z.read(lf_files[0]).decode("utf-8")
            reader = csv.DictReader(content.splitlines())
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

            if best is not None:
                actual_step = max(step_idx, 0)
                fs_by_step[actual_step] = best

    return fs_by_step


def read_all_node_pwp(gsz_path, step_idx):
    """
    Read PWP for ALL nodes at a given time step.
    Returns dict: {node_id: pwp_value}
    """
    # Try folder-per-step first
    target = f"{ANALYSIS_SEEP}/{step_idx:03d}/node.csv"
    with zipfile.ZipFile(gsz_path, "r") as z:
        if target not in z.namelist():
            # Fallback: old pattern
            target = f"{ANALYSIS_SEEP}/001/node-{step_idx}s.csv"
            if target not in z.namelist():
                return {}
        content = z.read(target).decode("utf-8", errors="replace")

    pwp = {}
    for row in csv.DictReader(io.StringIO(content)):
        try:
            pwp[int(row["Node"])] = float(row["PoreWaterPressure"])
        except (ValueError, KeyError):
            pass
    return pwp


def get_node_count(gsz_path):
    """Read the number of mesh nodes from a solved archive."""
    # Try reading any node.csv to get the count
    with zipfile.ZipFile(gsz_path, "r") as z:
        for f in z.namelist():
            if (f.startswith(ANALYSIS_SEEP + "/")
                    and f.endswith("/node.csv")):
                content = z.read(f).decode("utf-8", errors="replace")
                reader = csv.DictReader(io.StringIO(content))
                return max(int(row["Node"]) for row in reader
                           if "Node" in row)
    return 0


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

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
                    idx = int(parts[0])
                    completed.add(idx)
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    return completed


# ---------------------------------------------------------------------------
# LHS sampling
# ---------------------------------------------------------------------------

def generate_lhs_samples():
    sampler = qmc.LatinHypercube(d=4, seed=SEED)
    return sampler.random(n=N_ITER)


def transform_sample(s):
    """Transform [0,1]^4 LHS sample to physical parameters."""
    rp       = float(10 ** (s[0] * 3.0))                     # 1-1000 yr
    duration = int(np.clip(round(1 + s[1] * 9), 1, 10))      # 1-10 days
    shape    = float(s[2])                                    # 0-1
    state    = ("dry" if s[3] < 0.25 else
                ("wet" if s[3] >= 0.75 else "normal"))
    return rp, duration, shape, state


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def build_header(n_nodes):
    cols = [
        "iter", "return_period_yr", "storm_duration_days",
        "shape_param", "antecedent_state", "total_depth_in",
        "API_7d_mm", "API_14d_mm", "API_21d_mm", "API_30d_mm",
    ]
    for d in range(1, 11):
        cols.append(f"rain_day_{d:02d}_in")
    for n in range(1, n_nodes + 1):
        cols.append(f"PWP_N{n:03d}")
    cols += ["min_FS", "min_FS_step", "n_fs_steps", "converged"]
    return cols


def build_row(i, rp, duration, shape, state, total_depth,
              apis, storm_daily, pwp_dict, n_nodes,
              min_fs, min_fs_step, n_fs_steps):
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
        row[key] = (round(float(storm_daily[d-1]), 6)
                    if d <= len(storm_daily) else 0.0)
    for n in range(1, n_nodes + 1):
        row[f"PWP_N{n:03d}"] = round(pwp_dict.get(n, float('nan')), 4)
    row["min_FS"]      = round(min_fs, 6) if min_fs is not None else "N/A"
    row["min_FS_step"] = min_fs_step if min_fs_step is not None else "N/A"
    row["n_fs_steps"]  = n_fs_steps
    row["converged"]   = 1 if min_fs is not None else 0
    return row


def write_metadata(f, solver, calibrated_params, n_nodes):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Monte Carlo Training Database - Site-Specific Surrogate",
        "# Metro Center Slope, Jackson MS (Yazoo Clay)",
        f"# Generated   : {now}",
        f"# Input file  : {FILE}",
        f"# Iterations  : {N_ITER}",
        f"# Seed        : {SEED}",
        "# Sampling    : Latin Hypercube 4D (rainfall loading only)",
        "# NOAA Atlas 14 : Jackson MS, Partial Duration Series",
        f"# Mesh nodes  : {n_nodes}",
        "#",
        "# Varied: return period, storm duration, temporal shape, antecedent state",
        "# Fixed:  all soil parameters, geometry, mesh, boundary conditions",
        "#",
        "# Fixed soil parameters (from calibrated GSZ):",
    ]
    for name, p in calibrated_params.items():
        lines.append(f"#   {name}: c'={p['cohesion']} psf, "
                     f"phi'={p['phi']} deg")
    lines += [
        "#",
        "# Time stepping:",
        f"#   Antecedent: every {ANTECEDENT_STEP_DAYS} days",
        f"#   Storm:      every {STORM_STEP_DAYS} day(s)",
        f"#   Recession:  every {RECESSION_STEP_DAYS} days",
        "#",
        "# Output per row:",
        "#   Rainfall params + API values + daily storm depths",
        f"#   PWP at all {n_nodes} mesh nodes at the min-FS time step (psf)",
        "#   Minimum FS from coupled SLOPE/W analysis",
        "#",
        "# Units: depths=inches, API=mm, PWP=psf, FS=dimensionless",
        "#",
    ]
    for line in lines:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def run_preflight(solver_exe):
    """Quick single-iteration test to verify the full pipeline."""
    print("\n" + "=" * 65)
    print("PRE-FLIGHT - single iteration pipeline test")
    print("=" * 65)

    # Use a 2-year, 3-day, uniform, normal-antecedent storm
    rp, duration, shape, state = 2.0, 3, 0.5, "normal"
    total_depth = interpolate_depth(rp, duration)
    apis = sample_antecedent_apis(state, 0)
    storm_daily = distribute_storm(total_depth, duration, shape)
    rain_points, total_days = build_full_rainfall(storm_daily, apis)

    print(f"  Storm: RP={rp}yr, {duration}d, depth={total_depth:.2f}in, "
          f"{state}")
    print(f"  Total days: {total_days} ({len(rain_points)} rainfall points)")

    time_steps_s = build_time_steps(total_days, duration)
    print(f"  Time steps: {len(time_steps_s)} save points")

    try:
        temp_gsz, temp_dir = prepare_temp_gsz(0, rain_points,
                                              total_days, duration)
        print(f"  Work GSZ: {temp_gsz}")
        print(f"  Solving ...", end="", flush=True)
        t0 = time.time()
        run_solver(solver_exe, temp_gsz)
        dt = time.time() - t0
        print(f"  done ({dt:.1f}s)")

        # Extract FS
        fs_all = extract_fs_all_steps(temp_gsz)
        print(f"  FS steps extracted: {len(fs_all)}")
        if fs_all:
            min_step = min(fs_all, key=fs_all.get)
            min_fs   = fs_all[min_step]
            print(f"  Min FS = {min_fs:.4f} at step {min_step}")
            for s in sorted(fs_all.keys())[:8]:
                print(f"    Step {s:3d}: FS={fs_all[s]:.4f}")
            if len(fs_all) > 8:
                print(f"    ... ({len(fs_all)} total steps)")
        else:
            print("  ERROR: no FS extracted!")
            cleanup_temp(temp_dir)
            return False, 0

        # Extract PWP
        pwp = read_all_node_pwp(temp_gsz, min_step)
        print(f"  PWP nodes at min-FS step: {len(pwp)}")
        if pwp:
            vals = list(pwp.values())
            print(f"  PWP range: {min(vals):.1f} to {max(vals):.1f} psf")
        else:
            print("  WARNING: no PWP data at min-FS step")

        cleanup_temp(temp_dir)
        print(f"\n  Pre-flight PASSED ({dt:.1f}s, {len(fs_all)} FS steps, "
              f"{len(pwp)} nodes)")
        return True, max(pwp.keys()) if pwp else 0

    except Exception as e:
        print(f"\n  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False, 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 65)
    print("Monte Carlo Training Data Generator")
    print(f"  Input    : {FILE}")
    print(f"  Output   : {OUT_CSV}")
    print(f"  Iters    : {N_ITER}")
    print(f"  Sampling : Latin Hypercube (4D rainfall loading)")
    print(f"  Varied   : return period, duration, shape, antecedent")
    print(f"  Fixed    : all soil parameters")
    print("=" * 65)

    if not os.path.exists(FILE):
        print(f"\nERROR: Calibrated GSZ not found: {FILE}")
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)

    solver = find_solver()
    if solver is None:
        print("\nERROR: GeoStudio solver not found.")
        sys.exit(1)
    print(f"\n  Solver : {solver}")

    print("\nReading calibrated soil parameters...")
    calibrated_params = read_calibrated_params()
    for name, p in calibrated_params.items():
        print(f"  {name}: c'={p['cohesion']} psf, phi'={p['phi']} deg")
    if not calibrated_params:
        print("  WARNING: no soil parameters found - check GSZ")

    # Pre-flight
    ok, n_nodes = run_preflight(solver)
    if not ok:
        resp = input("\nPre-flight FAILED. Continue? (y/N): ").strip().lower()
        if resp != 'y':
            sys.exit(1)
    else:
        resp = input(f"\nProceed with {N_ITER} iterations? (Y/n): "
                     ).strip().lower()
        if resp == 'n':
            return

    if n_nodes == 0:
        print("ERROR: could not determine node count")
        sys.exit(1)

    # LHS samples
    print("\nGenerating LHS samples...")
    lhs       = generate_lhs_samples()
    completed = get_completed_iters()
    remaining = N_ITER - len(completed)

    if completed:
        print(f"  Resuming - {len(completed)} done, {remaining} remaining")
    else:
        print(f"  Fresh start - {N_ITER} iterations")

    header      = build_header(n_nodes)
    file_exists = os.path.exists(OUT_CSV) and len(completed) > 0
    failed      = 0
    success     = 0

    with open(OUT_CSV, "a" if file_exists else "w",
              newline="", encoding="utf-8") as csvfile:

        writer = csv.DictWriter(csvfile, fieldnames=header)
        if not file_exists:
            write_metadata(csvfile, solver, calibrated_params, n_nodes)
            writer.writeheader()

        for i in range(1, N_ITER + 1):
            if i in completed:
                continue

            rp, duration, shape, state = transform_sample(lhs[i - 1])
            total_depth  = interpolate_depth(rp, duration)
            apis         = sample_antecedent_apis(state, i)
            storm_daily  = distribute_storm(total_depth, duration, shape)
            rain_points, total_days = build_full_rainfall(storm_daily, apis)

            temp_dir = None
            min_fs   = None
            min_step = None
            pwp_dict = {}
            n_fs     = 0

            try:
                temp_gsz, temp_dir = prepare_temp_gsz(
                    i, rain_points, total_days, duration)
                run_solver(solver, temp_gsz)

                # Extract FS at all steps
                fs_all = extract_fs_all_steps(temp_gsz)
                n_fs   = len(fs_all)

                if fs_all:
                    min_step = min(fs_all, key=fs_all.get)
                    min_fs   = fs_all[min_step]

                    # Read full mesh PWP at min-FS step
                    pwp_dict = read_all_node_pwp(temp_gsz, min_step)

                row = build_row(i, rp, duration, shape, state,
                                total_depth, apis, storm_daily,
                                pwp_dict, n_nodes,
                                min_fs, min_step, n_fs)
                writer.writerow(row)
                csvfile.flush()
                success += 1

                fs_str = f"{min_fs:.4f}" if min_fs else "N/A"
                print(f"  [{i:>5}/{N_ITER}]  "
                      f"RP={rp:7.1f}yr  dur={duration:2d}d  "
                      f"{state:<6}  depth={total_depth:.2f}in  "
                      f"FS={fs_str}  ({n_fs} steps, "
                      f"{len(pwp_dict)} nodes)")

            except Exception as e:
                failed += 1
                row = build_row(i, rp, duration, shape, state,
                                total_depth, apis, storm_daily,
                                {}, n_nodes, None, None, 0)
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

    # Summary
    print(f"\n{'=' * 65}")
    print(f"Completed : {success} succeeded | {failed} failed")
    print(f"Output    : {OUT_CSV}")

    # Quick stats from output
    fs_vals = []
    try:
        with open(OUT_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(
                (line for line in f if not line.startswith("#")))
            for row in reader:
                try:
                    v = float(row["min_FS"])
                    if 0 < v < 10:
                        fs_vals.append(v)
                except (ValueError, KeyError):
                    continue
    except Exception:
        pass

    if fs_vals:
        arr = np.array(fs_vals)
        print(f"\n--- FS summary ({len(arr)} valid samples) ---")
        print(f"  Mean        : {arr.mean():.4f}")
        print(f"  Std         : {arr.std():.4f}")
        print(f"  Min / Max   : {arr.min():.4f} / {arr.max():.4f}")
        print(f"  P(FS < 1.0) : {(arr < 1.0).mean()*100:.2f}%")
        print(f"  P(FS < 1.5) : {(arr < 1.5).mean()*100:.2f}%")

    print(f"\n{'=' * 65}")

if __name__ == "__main__":
    main()