"""
Stage 1: Rainfall Monte Carlo — Generate PWP Fields
=====================================================
Runs the full GeoStudio chain (IC -> SEEP/W -> SLOPE/W) for each
rainfall scenario. SAVES the solved GSZ archives so Stage 2 can
re-use the SEEP/W results with different strength parameters.

This is the expensive stage (~5 min per iteration).
Only 50-100 iterations needed.

Output:
  - solved_gsz/rain_NNNN.gsz   : solved archives (SEEP/W results inside)
  - stage1_rainfall_log.csv     : rainfall params + PWP summary per iteration
  - stage1_samples.csv          : raw LHS sample values for reproducibility

Usage:
    python stage1_rainfall_mc.py                     # run all 75
    python stage1_rainfall_mc.py --start 1  --end 37  # terminal 1
    python stage1_rainfall_mc.py --start 38 --end 75  # terminal 2
"""

import sys, os, re, glob, shutil, zipfile, csv, io, warnings, subprocess, time
import argparse
import numpy as np
from datetime import datetime
from scipy.stats import qmc

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

FILE            = r"E:\Github\MCP_Geostudio\calibration\Metro-Center-slope-final.gsz"
N_RAIN          = 100                     # rainfall scenarios (50-100 recommended)
SEED            = 42
ANALYSIS_FS     = "FS"
ANALYSIS_SEEP   = "Rainfall Simulation"
ANALYSIS_INIT   = "Initial Condition"

OUT_DIR         = r"E:\Github\MCP_Geostudio\training"
SOLVED_DIR      = os.path.join(OUT_DIR, "solved_gsz")
STAGE1_LOG      = os.path.join(OUT_DIR, "stage1_rainfall_log.csv")
STAGE1_SAMPLES  = os.path.join(OUT_DIR, "stage1_samples.csv")

SOLVER_TIMEOUT  = 1800
SOLVER_OVERRIDE = None

# Time stepping (days)
ANTECEDENT_STEP_DAYS = 3
STORM_STEP_DAYS      = 1
RECESSION_STEP_DAYS  = 2

# NOAA Atlas 14 - Jackson MS
NOAA_RETURN_PERIODS = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]
NOAA_IDF = {
    1:  [3.77, 4.36, 5.34, 6.18, 7.38, 8.32, 9.28, 10.3,  11.7, 12.7],
    2:  [4.38, 5.04, 6.12, 7.03, 8.29, 9.28, 10.3, 11.3,  12.6, 13.7],
    3:  [4.82, 5.50, 6.61, 7.54, 8.83, 9.83, 10.8, 11.9,  13.2, 14.3],
    4:  [5.21, 5.89, 7.01, 7.95, 9.25, 10.3, 11.3, 12.3,  13.7, 14.7],
    7:  [6.20, 6.90, 8.05, 9.01, 10.3, 11.4, 12.4, 13.4,  14.8, 15.9],
    10: [7.04, 7.78, 8.99, 10.0, 11.4, 12.5, 13.5, 14.6,  16.1, 17.2],
}

API_BOUNDS = {
    "dry":    (0,   4,    0,  15,   0,  30,   0,  88),
    "normal": (4,  50,   10,  80,  20, 120,  88, 200),
    "wet":    (50, 150,  70, 200, 100, 280, 200, 350),
}

sys.path.insert(0, r"C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages")


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

def find_solver():
    if SOLVER_OVERRIDE and os.path.exists(SOLVER_OVERRIDE):
        return SOLVER_OVERRIDE
    for pattern in [
        r"C:\Program Files\Seequent\GeoStudio 2025*\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio 2024*\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio*\Bin\GeoCmd.exe",
    ]:
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
# NOAA IDF interpolation
# ---------------------------------------------------------------------------

def interpolate_depth(return_period_yr, duration_days):
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
            np.log10(np.array(depths, dtype=float))))

    if d_lo == d_hi:
        return interp_rp(NOAA_IDF[int(d_lo)])

    depth_lo = interp_rp(NOAA_IDF[int(d_lo)])
    depth_hi = interp_rp(NOAA_IDF[int(d_hi)])
    t = ((np.log10(dur) - np.log10(d_lo)) /
         (np.log10(d_hi) - np.log10(d_lo)))
    return float(10 ** (np.log10(depth_lo) +
                        t * (np.log10(depth_hi) - np.log10(depth_lo))))


# ---------------------------------------------------------------------------
# Antecedent / Rainfall / Time stepping
# ---------------------------------------------------------------------------

def sample_antecedent_apis(state, iter_idx):
    rng = np.random.default_rng(SEED + iter_idx)
    b   = API_BOUNDS[state]
    a7  = rng.uniform(b[0], b[1])
    a14 = rng.uniform(max(b[2], a7),  b[3])
    a21 = rng.uniform(max(b[4], a14), b[5])
    a30 = rng.uniform(max(b[6], a21), b[7])
    return {"API_7d": round(a7, 1), "API_14d": round(a14, 1),
            "API_21d": round(a21, 1), "API_30d": round(a30, 1)}


def distribute_storm(total_depth_in, n_days, shape):
    if n_days == 1:
        return np.array([total_depth_in])
    days = np.linspace(0, 1, n_days)
    if abs(shape - 0.5) < 0.01:
        weights = np.ones(n_days)
    elif shape < 0.5:
        weights = np.exp(-4.0 * (0.5 - shape) * days)
    else:
        weights = np.exp(4.0 * (shape - 0.5) * days)
    weights /= weights.sum()
    return total_depth_in * weights


def build_full_rainfall(storm_depths_in, apis_mm):
    mm_to_in = 1.0 / 25.4
    n_storm  = len(storm_depths_in)
    a7  = apis_mm["API_7d"]  * mm_to_in
    a14 = apis_mm["API_14d"] * mm_to_in
    a21 = apis_mm["API_21d"] * mm_to_in
    a30 = apis_mm["API_30d"] * mm_to_in

    antecedent = np.zeros(30)
    antecedent[23:30] = a7 / 7.0
    antecedent[16:23] = max(0.0, a14 - a7) / 7.0
    antecedent[9:16]  = max(0.0, a21 - a14) / 7.0
    antecedent[0:9]   = max(0.0, a30 - a21) / 9.0

    all_depths = np.concatenate([antecedent, storm_depths_in, np.full(5, 0.01)])
    total_days = len(all_depths)
    points = [{"X": str(d * 86400), "Y": max(0.0, float(all_depths[d]))}
              for d in range(total_days)]
    return points, total_days


def build_time_steps(total_days, n_storm):
    steps = []
    day = 0
    while day < 30:
        day += ANTECEDENT_STEP_DAYS
        if day > 30: day = 30
        steps.append(day * 86400)
    storm_end = 30 + n_storm
    d = 30
    while d < storm_end:
        d += STORM_STEP_DAYS
        if d > storm_end: d = storm_end
        steps.append(d * 86400)
    d = storm_end
    while d < total_days:
        d += RECESSION_STEP_DAYS
        if d > total_days: d = total_days
        steps.append(d * 86400)
    return sorted(set(steps))


# ---------------------------------------------------------------------------
# XML patching (rainfall + time increments only)
# ---------------------------------------------------------------------------

def _patch_rainfall(xml_text, rain_points):
    idx = xml_text.lower().find(">rainfall<")
    if idx == -1:
        raise RuntimeError("'>rainfall<' not found in XML")
    chunk_end = xml_text.find("</ClimateFn>", idx)
    if chunk_end == -1:
        raise RuntimeError("</ClimateFn> not found")
    chunk = xml_text[idx:chunk_end]
    new_pts = f'<Points Len="{len(rain_points)}">\n'
    for pt in rain_points:
        new_pts += f'            <Point X="{pt["X"]}" Y="{pt["Y"]:.8f}" />\n'
    new_pts += "          </Points>"
    new_chunk = re.sub(r'<Points.*?</Points>', new_pts, chunk,
                       count=1, flags=re.DOTALL)
    if new_chunk == chunk:
        raise RuntimeError("Rainfall <Points> regex did not match")
    return xml_text[:idx] + new_chunk + xml_text[chunk_end:]


def _patch_time_increments(xml_text, analysis_name, time_steps_s,
                           total_duration_s):
    idx = xml_text.find(f">{analysis_name}<")
    if idx == -1:
        return xml_text
    chunk_end = xml_text.find("</Analysis>", idx)
    chunk = xml_text[idx:chunk_end]
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
        new_ti += (f'          <TimeStep Step="{elapsed - prev}" '
                   f'ElapsedTime="{elapsed}" Save="true" />\n')
        prev = elapsed
    new_ti += "        </TimeSteps>\n      </TimeIncrements>"

    old_chunk = chunk
    chunk = re.sub(r'<TimeIncrements>.*?</TimeIncrements>',
                   new_ti, chunk, count=1, flags=re.DOTALL)
    if chunk == old_chunk:
        chunk = re.sub(r'<TimeIncrements[^>]*>.*?</TimeIncrements>',
                       new_ti, old_chunk, count=1, flags=re.DOTALL)
        if chunk == old_chunk:
            return xml_text
    return xml_text[:idx] + chunk + xml_text[chunk_end:]


# ---------------------------------------------------------------------------
# Temp GSZ — strip ALL results so solver regenerates everything
# ---------------------------------------------------------------------------

def prepare_temp_gsz(iter_idx, rain_points, total_days, n_storm):
    temp_dir = os.path.join(OUT_DIR, f"_stage1_{iter_idx:04d}")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    gsz_name = os.path.basename(FILE)
    gsz_stem = os.path.splitext(gsz_name)[0]
    temp_gsz = os.path.join(temp_dir, gsz_name)

    with zipfile.ZipFile(FILE, "r") as zin:
        all_items = zin.infolist()
        all_data  = {item.filename: zin.read(item.filename)
                     for item in all_items}

    analysis_folders = [ANALYSIS_FS, ANALYSIS_SEEP, ANALYSIS_INIT,
                        "Slope Stability"]
    root_xml_key = next(
        (k for k in all_data
         if k.endswith(gsz_stem + ".xml")
         and not any(k.startswith(af + "/") for af in analysis_folders)),
        None)
    if root_xml_key is None:
        root_xml_key = next(
            (k for k in all_data if k.endswith(".xml") and "/" not in k), None)
    if root_xml_key is None:
        raise RuntimeError(f"Root XML not found in {gsz_name}")

    xml_str = all_data[root_xml_key].decode("utf-8")

    time_steps_s     = build_time_steps(total_days, n_storm)
    total_duration_s = total_days * 86400

    xml_str = _patch_rainfall(xml_str, rain_points)
    xml_str = _patch_time_increments(xml_str, ANALYSIS_SEEP,
                                     time_steps_s, total_duration_s)
    xml_str = _patch_time_increments(xml_str, ANALYSIS_FS,
                                     time_steps_s, total_duration_s)

    # Strip ALL old results — solver regenerates everything fresh
    result_prefixes = (ANALYSIS_FS + "/", ANALYSIS_SEEP + "/",
                       ANALYSIS_INIT + "/")

    with zipfile.ZipFile(temp_gsz, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data  = all_data[fname]

            # Skip stale result folders
            if any(fname.startswith(p) for p in result_prefixes):
                continue

            # Replace root XML with patched version
            if fname == root_xml_key:
                data = xml_str.encode("utf-8")

            zout.writestr(item, data)

    return temp_gsz, temp_dir


# ---------------------------------------------------------------------------
# Result extraction (PWP + FS)
# ---------------------------------------------------------------------------

def extract_fs_all_steps(gsz_path):
    prefix = ANALYSIS_FS + "/"
    fs_by_step = {}
    with zipfile.ZipFile(gsz_path, "r") as z:
        all_files = z.namelist()
        step_folders = set()
        for f in all_files:
            if f.startswith(prefix) and "lambdafos_" in f and f.endswith(".csv"):
                parts = f[len(prefix):].split("/")
                if len(parts) == 2:
                    try: step_folders.add(int(parts[0]))
                    except ValueError: pass
                elif len(parts) == 1:
                    step_folders.add(-1)

        for step_idx in sorted(step_folders):
            if step_idx == -1:
                lf_files = [f for f in all_files if f.startswith(prefix)
                            and "lambdafos_" in f and f.endswith(".csv")
                            and f.count("/") == 1]
            else:
                step_prefix = f"{prefix}{step_idx:03d}/"
                lf_files = [f for f in all_files if f.startswith(step_prefix)
                            and "lambdafos_" in f and f.endswith(".csv")]
            if not lf_files:
                continue
            content = z.read(lf_files[0]).decode("utf-8")
            best, best_diff = None, float("inf")
            for row in csv.DictReader(content.splitlines()):
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
                fs_by_step[max(step_idx, 0)] = best
    return fs_by_step


def read_all_node_pwp(gsz_path, step_idx):
    target = f"{ANALYSIS_SEEP}/{step_idx:03d}/node.csv"
    with zipfile.ZipFile(gsz_path, "r") as z:
        if target not in z.namelist():
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


# ---------------------------------------------------------------------------
# LHS sampling
# ---------------------------------------------------------------------------

def generate_lhs_samples():
    sampler = qmc.LatinHypercube(d=4, seed=SEED)
    return sampler.random(n=N_RAIN)


def transform_sample(s):
    rp       = float(10 ** (s[0] * 3.0))
    duration = int(np.clip(round(1 + s[1] * 9), 1, 10))
    shape    = float(s[2])
    state    = "dry" if s[3] < 0.25 else ("wet" if s[3] >= 0.75 else "normal")
    return rp, duration, shape, state


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Stage 1: Rainfall Monte Carlo")
    parser.add_argument("--start", type=int, default=1,
                        help="First rain_id to process (default: 1)")
    parser.add_argument("--end", type=int, default=N_RAIN,
                        help=f"Last rain_id to process (default: {N_RAIN})")
    args = parser.parse_args()

    run_start = args.start
    run_end   = min(args.end, N_RAIN)

    print("=" * 65)
    print("STAGE 1: Rainfall Monte Carlo — Generate PWP Fields")
    print(f"  Input     : {FILE}")
    print(f"  Range     : {run_start} to {run_end} of {N_RAIN} total")
    print(f"  Saved to  : {SOLVED_DIR}")
    print(f"  Log       : {STAGE1_LOG}")
    print("=" * 65)

    if not os.path.exists(FILE):
        print(f"\nERROR: Calibrated GSZ not found: {FILE}")
        sys.exit(1)

    os.makedirs(SOLVED_DIR, exist_ok=True)

    solver = find_solver()
    if solver is None:
        print("\nERROR: GeoStudio solver not found.")
        sys.exit(1)
    print(f"\n  Solver: {solver}")

    # Check for completed runs
    completed = set()
    for f in os.listdir(SOLVED_DIR):
        if f.startswith("rain_") and f.endswith(".gsz"):
            try:
                completed.add(int(f[5:9]))
            except ValueError:
                pass

    lhs = generate_lhs_samples()

    # Save LHS samples for reproducibility (only if not already written)
    if not os.path.exists(STAGE1_SAMPLES):
        with open(STAGE1_SAMPLES, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["rain_id", "lhs_0", "lhs_1", "lhs_2", "lhs_3",
                         "return_period_yr", "duration_days", "shape", "state",
                         "total_depth_in"])
            for i in range(N_RAIN):
                rp, dur, shape, state = transform_sample(lhs[i])
                depth = interpolate_depth(rp, dur)
                w.writerow([i + 1, *lhs[i], round(rp, 3), dur,
                             round(shape, 4), state, round(depth, 4)])

    in_range   = set(range(run_start, run_end + 1))
    to_run     = in_range - completed
    print(f"\n  Range {run_start}-{run_end}: {len(to_run)} to run "
          f"({len(in_range - to_run)} already done)")

    resp = input(f"\nProceed? (Y/n): ").strip().lower()
    if resp == 'n':
        return

    # Open log file (append mode — safe for parallel terminals)
    log_is_new = not os.path.exists(STAGE1_LOG)
    log_file   = open(STAGE1_LOG, "a", newline="", encoding="utf-8")
    log_writer = csv.writer(log_file)
    if log_is_new:
        log_writer.writerow([
            "rain_id", "return_period_yr", "storm_duration_days",
            "shape_param", "antecedent_state", "total_depth_in",
            "API_7d_mm", "API_14d_mm", "API_21d_mm", "API_30d_mm",
            "n_fs_steps", "min_fs_calib_strength", "min_fs_step",
            "n_pwp_nodes", "pwp_min", "pwp_max",
            "solve_time_s", "status"
        ])

    success = 0
    failed  = 0

    for i in range(run_start, run_end + 1):
        if i in completed:
            continue

        rp, duration, shape, state = transform_sample(lhs[i - 1])
        total_depth = interpolate_depth(rp, duration)
        apis        = sample_antecedent_apis(state, i)
        storm_daily = distribute_storm(total_depth, duration, shape)
        rain_points, total_days = build_full_rainfall(storm_daily, apis)

        try:
            temp_gsz, temp_dir = prepare_temp_gsz(
                i, rain_points, total_days, duration)

            print(f"  [{i:>3}/{run_end}]  RP={rp:7.1f}yr  dur={duration:2d}d  "
                  f"{state:<6}  depth={total_depth:.2f}in  ", end="", flush=True)

            t0 = time.time()
            run_solver(solver, temp_gsz)
            dt = time.time() - t0
            print(f"solved ({dt:.0f}s)  ", end="", flush=True)

            # Extract FS (at calibrated strength — just for logging)
            fs_all = extract_fs_all_steps(temp_gsz)
            n_fs   = len(fs_all)
            min_fs = min(fs_all.values()) if fs_all else None
            min_step = min(fs_all, key=fs_all.get) if fs_all else None

            # Extract PWP at min-FS step
            pwp = read_all_node_pwp(temp_gsz, min_step) if min_step is not None else {}
            pwp_vals = list(pwp.values()) if pwp else []

            # Save solved GSZ to permanent location
            saved_gsz = os.path.join(SOLVED_DIR, f"rain_{i:04d}.gsz")
            shutil.copy2(temp_gsz, saved_gsz)

            fs_str = f"{min_fs:.4f}" if min_fs else "N/A"
            print(f"FS={fs_str}  steps={n_fs}  nodes={len(pwp)}")

            log_writer.writerow([
                i, round(rp, 3), duration, round(shape, 4), state,
                round(total_depth, 4),
                apis["API_7d"], apis["API_14d"], apis["API_21d"], apis["API_30d"],
                n_fs,
                round(min_fs, 6) if min_fs else "N/A",
                min_step if min_step is not None else "N/A",
                len(pwp),
                round(min(pwp_vals), 1) if pwp_vals else "N/A",
                round(max(pwp_vals), 1) if pwp_vals else "N/A",
                round(dt, 1), "OK"
            ])
            log_file.flush()
            success += 1

        except Exception as e:
            print(f"FAILED - {e}")
            log_writer.writerow([
                i, round(rp, 3), duration, round(shape, 4), state,
                round(total_depth, 4),
                apis["API_7d"], apis["API_14d"], apis["API_21d"], apis["API_30d"],
                0, "N/A", "N/A", 0, "N/A", "N/A", 0, f"FAILED: {e}"
            ])
            log_file.flush()
            failed += 1

        finally:
            # Clean up temp working directory (solved copy already saved)
            if 'temp_dir' in dir() and temp_dir and os.path.exists(temp_dir):
                for _ in range(5):
                    try:
                        shutil.rmtree(temp_dir)
                        break
                    except Exception:
                        time.sleep(2)

    log_file.close()

    print(f"\n{'=' * 65}")
    print(f"Stage 1 Complete (range {run_start}-{run_end}): "
          f"{success} OK | {failed} failed")
    print(f"  Solved archives : {SOLVED_DIR}")
    print(f"  Log             : {STAGE1_LOG}")
    print(f"  Samples         : {STAGE1_SAMPLES}")
    print(f"\nNext: run stage2_strength_mc.py to vary strength parameters")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    main()
