"""
Stage 2: Strength Parameter Monte Carlo — Reuse PWP Fields
============================================================
For each solved GSZ from Stage 1 (with SEEP/W results inside),
varies c' and phi' for AWYC and WYC via Latin Hypercube Sampling.

Key trick: keeps SEEP/W results (Initial Condition + Rainfall Simulation)
intact in the archive and strips ONLY the FS (SLOPE/W) results.
The solver sees valid SEEP/W and only recomputes SLOPE/W → much faster.

If the solver still re-runs SEEP/W (some GeoStudio versions), the
results will be identical since hydraulic inputs are unchanged, so
correctness is guaranteed either way.

Inputs:
  - solved_gsz/rain_NNNN.gsz from Stage 1
  - LHS on 4D strength space (c'_AWYC, phi'_AWYC, c'_WYC, phi'_WYC)

Output:
  - training_data_combined.csv : full training database
    Each row: rainfall params + strength params + 201 PWP values + FS

Usage:
    python stage2_strength_mc.py --height 20                      # all rainfall scenarios
    python stage2_strength_mc.py --height 20 --start 1  --end 50  # terminal 1
    python stage2_strength_mc.py --height 20 --start 51 --end 100 # terminal 2
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

OUT_DIR         = "training"
# These will become e.g. training/H20/solved_gsz/, training/H20/training_data_combined.csv
HEIGHT          = None  # set in main()
SOLVED_DIR      = None  # set in main()
STAGE1_LOG      = None  # set in main()
OUT_CSV         = None  # set in main()
CHECKPOINT_CSV  = None  # set in main()
TEMP_ROOT       = None  # set in main()

ANALYSIS_FS     = "FS"
ANALYSIS_SEEP   = "Rainfall Simulation"
ANALYSIS_INIT   = "Initial Condition"

N_STRENGTH      = 30          # strength combos PER rainfall scenario
STRENGTH_SEED   = 7777

SOLVER_TIMEOUT  = 3600        # increased to 20 minutes for parallel loads
SOLVER_OVERRIDE = None

# ---------------------------------------------------------------------------
# Strength parameter bounds
# ---------------------------------------------------------------------------
# Ranges span from below residual to peak strength estimates for Yazoo Clay

#                         (  min,    max  )
C_AWYC_BOUNDS   =        ( 30.0,  300.0)   # psf  (calibrated: 79.3)
PHI_AWYC_BOUNDS =        ( 12.0,   25.0)   # deg  (calibrated: 19.0)
C_WYC_BOUNDS    =        (100.0,  500.0)   # psf  (calibrated: 248.5)
PHI_WYC_BOUNDS  =        ( 12.0,   25.0)   # deg  (calibrated: 19.0)

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
	[solver_exe, gsz_path, "/solve", "FS"],
        #[solver_exe, "/solve", gsz_path],
        capture_output=True, text=True, timeout=SOLVER_TIMEOUT
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Solver exit {result.returncode}: "
            f"{(result.stderr or result.stdout)[:300]}")


# ---------------------------------------------------------------------------
# XML patching — strength parameters only
# ---------------------------------------------------------------------------

def _patch_strength_param(xml_text, material_name, param_tag, new_value):
    """Patch element content like <CohesionPrime>79.3</CohesionPrime>
    within the <Material> block identified by material_name."""
    marker = f">{material_name}<"
    start = xml_text.find(marker)
    if start == -1:
        raise RuntimeError(f"Material '{material_name}' not found in XML")

    mat_end = xml_text.find("</Material>", start)
    if mat_end == -1:
        mat_end = start + 3000

    open_tag  = f"<{param_tag}>"
    close_tag = f"</{param_tag}>"
    tag_start = xml_text.find(open_tag, start)
    if tag_start == -1 or tag_start > mat_end:
        raise RuntimeError(f"<{param_tag}> not found in '{material_name}'")
    tag_end = xml_text.find(close_tag, tag_start)
    if tag_end == -1 or tag_end > mat_end:
        raise RuntimeError(f"</{param_tag}> not found in '{material_name}'")

    replacement = f"{open_tag}{new_value:.6g}{close_tag}"
    return xml_text[:tag_start] + replacement + xml_text[tag_end + len(close_tag):]


def patch_all_strength(xml_text, c_awyc, phi_awyc, c_wyc, phi_wyc):
    xml_text = _patch_strength_param(
        xml_text, "Weathered Yazoo Clay in Active Zone", "CohesionPrime", c_awyc)
    xml_text = _patch_strength_param(
        xml_text, "Weathered Yazoo Clay in Active Zone", "PhiPrime", phi_awyc)
    xml_text = _patch_strength_param(
        xml_text, "Weathered Yazoo Clay", "CohesionPrime", c_wyc)
    xml_text = _patch_strength_param(
        xml_text, "Weathered Yazoo Clay", "PhiPrime", phi_wyc)
    return xml_text


# ---------------------------------------------------------------------------
# Prepare Stage 2 GSZ: patch strength, strip ONLY FS results
# ---------------------------------------------------------------------------

def prepare_strength_gsz(solved_gsz_path, c_awyc, phi_awyc, c_wyc, phi_wyc,
                         work_dir):
    """
    Copy a Stage 1 solved GSZ, patch strength params, strip ONLY FS results.
    SEEP/W results (Initial Condition + Rainfall Simulation) are KEPT.
    """
    gsz_name = os.path.basename(solved_gsz_path)
    gsz_stem = os.path.splitext(gsz_name)[0]
    work_gsz = os.path.join(work_dir, gsz_name)

    with zipfile.ZipFile(solved_gsz_path, "r") as zin:
        all_items = zin.infolist()
        all_data  = {item.filename: zin.read(item.filename)
                     for item in all_items}

    # Find root XML
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
    xml_str = patch_all_strength(xml_str, c_awyc, phi_awyc, c_wyc, phi_wyc)

    # Strip ONLY FS (SLOPE/W) results — keep SEEP/W results intact
    fs_prefix = ANALYSIS_FS + "/"
    skipped = 0

    with zipfile.ZipFile(work_gsz, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data  = all_data[fname]

            # Skip FS result files only
            if fname.startswith(fs_prefix) and fname != fs_prefix:
                # Keep the FS analysis XML (FS/Stem.xml) but skip result CSVs
                if not fname.endswith(".xml"):
                    skipped += 1
                    continue

            # Replace root XML with patched version
            if fname == root_xml_key:
                data = xml_str.encode("utf-8")

            zout.writestr(item, data)

    return work_gsz, skipped


# ---------------------------------------------------------------------------
# Result extraction
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
# LHS for strength parameters
# ---------------------------------------------------------------------------

def generate_strength_samples():
    sampler = qmc.LatinHypercube(d=4, seed=STRENGTH_SEED)
    raw = sampler.random(n=N_STRENGTH)
    samples = []
    for s in raw:
        c_awyc   = C_AWYC_BOUNDS[0]   + s[0] * (C_AWYC_BOUNDS[1]   - C_AWYC_BOUNDS[0])
        phi_awyc = PHI_AWYC_BOUNDS[0] + s[1] * (PHI_AWYC_BOUNDS[1] - PHI_AWYC_BOUNDS[0])
        c_wyc    = C_WYC_BOUNDS[0]    + s[2] * (C_WYC_BOUNDS[1]    - C_WYC_BOUNDS[0])
        phi_wyc  = PHI_WYC_BOUNDS[0]  + s[3] * (PHI_WYC_BOUNDS[1]  - PHI_WYC_BOUNDS[0])
        samples.append((round(c_awyc, 2), round(phi_awyc, 2),
                         round(c_wyc, 2), round(phi_wyc, 2)))
    return samples


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def load_checkpoint():
    """Returns set of (rain_id, strength_id) tuples already completed."""
    done = set()
    if not os.path.exists(CHECKPOINT_CSV):
        return done
    try:
        with open(CHECKPOINT_CSV, "r") as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            for row in reader:
                try:
                    done.add((int(row[0]), int(row[1])))
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass
    return done


# ---------------------------------------------------------------------------
# Load Stage 1 rainfall log
# ---------------------------------------------------------------------------

def load_stage1_log():
    """Read Stage 1 rainfall parameters for each rain_id."""
    rain_params = {}
    if not os.path.exists(STAGE1_LOG):
        return rain_params
    with open(STAGE1_LOG, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rid = int(row["rain_id"])
                if row["status"] != "OK":
                    continue
                rain_params[rid] = {
                    "return_period_yr":    float(row["return_period_yr"]),
                    "storm_duration_days": int(row["storm_duration_days"]),
                    "shape_param":         float(row["shape_param"]),
                    "antecedent_state":    row["antecedent_state"],
                    "total_depth_in":      float(row["total_depth_in"]),
                    "API_7d_mm":           float(row["API_7d_mm"]),
                    "API_14d_mm":          float(row["API_14d_mm"]),
                    "API_21d_mm":          float(row["API_21d_mm"]),
                    "API_30d_mm":          float(row["API_30d_mm"]),
                }
            except (ValueError, KeyError):
                continue
    return rain_params


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def build_header(n_nodes):
    cols = [
        "rain_id", "strength_id", "slope_height_ft",
        # Rainfall params
        "return_period_yr", "storm_duration_days",
        "shape_param", "antecedent_state", "total_depth_in",
        "API_7d_mm", "API_14d_mm", "API_21d_mm", "API_30d_mm",
        # Strength params
        "c_awyc_psf", "phi_awyc_deg", "c_wyc_psf", "phi_wyc_deg",
    ]
    # PWP columns
    for n in range(1, n_nodes + 1):
        cols.append(f"PWP_N{n:03d}")
    cols += ["min_FS", "min_FS_step", "n_fs_steps", "solve_time_s", "converged"]
    return cols


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global HEIGHT, SOLVED_DIR, STAGE1_LOG, OUT_CSV, CHECKPOINT_CSV, TEMP_ROOT

    parser = argparse.ArgumentParser(description="Stage 2: Strength Monte Carlo")
    parser.add_argument("--height", type=int, required=True,
                        help="Slope height in feet (15, 20, 25, or 30)")
    parser.add_argument("--start", type=int, default=None,
                        help="First rain_id to process (default: all)")
    parser.add_argument("--end", type=int, default=None,
                        help="Last rain_id to process (default: all)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip confirmation prompt (for batch/automated runs)")
    args = parser.parse_args()

    # Set paths based on height
    HEIGHT         = args.height
    HEIGHT_DIR     = os.path.join(OUT_DIR, f"H{HEIGHT}")
    SOLVED_DIR     = os.path.join(HEIGHT_DIR, "solved_gsz")
    STAGE1_LOG     = os.path.join(HEIGHT_DIR, "stage1_rainfall_log.csv")
    OUT_CSV        = os.path.join(HEIGHT_DIR, "training_data_combined.csv")
    CHECKPOINT_CSV = os.path.join(HEIGHT_DIR, "stage2_checkpoint.csv")
    TEMP_ROOT      = os.path.join(HEIGHT_DIR, "_stage2_temp")

    print("=" * 65)
    print("STAGE 2: Strength Parameter Monte Carlo")
    print(f"  Height       : {HEIGHT} ft")
    print(f"  Solved GSZs  : {SOLVED_DIR}")
    print(f"  Strength LHS : {N_STRENGTH} combos per rainfall scenario")
    print(f"  Output       : {OUT_CSV}")
    print("=" * 65)

    solver = find_solver()
    if solver is None:
        print("\nERROR: GeoStudio solver not found.")
        sys.exit(1)
    print(f"\n  Solver: {solver}")

    # Load Stage 1 data
    rain_params = load_stage1_log()
    if not rain_params:
        print("\nERROR: No Stage 1 data found. Run stage1_rainfall_mc.py first.")
        sys.exit(1)

    # Find solved GSZ files
    rain_ids = sorted(rain_params.keys())
    available = []
    for rid in rain_ids:
        gsz_path = os.path.join(SOLVED_DIR, f"rain_{rid:04d}.gsz")
        if os.path.exists(gsz_path):
            available.append(rid)

    # Filter to requested range
    if args.start is not None or args.end is not None:
        r_start = args.start if args.start is not None else min(available)
        r_end   = args.end   if args.end   is not None else max(available)
        available = [r for r in available if r_start <= r <= r_end]
        print(f"  Rain_id range: {r_start} to {r_end}")

    print(f"  Stage 1 scenarios: {len(available)} solved GSZs in range")

    # Generate strength samples (same for all rainfall scenarios)
    strength_samples = generate_strength_samples()
    print(f"  Strength samples : {N_STRENGTH}")
    print(f"  Total iterations : {len(available) * N_STRENGTH}")

    # Print strength parameter ranges
    print(f"\n  Strength bounds:")
    print(f"    c'_AWYC  : {C_AWYC_BOUNDS[0]:>6.1f} - {C_AWYC_BOUNDS[1]:>6.1f} psf")
    print(f"    phi'_AWYC: {PHI_AWYC_BOUNDS[0]:>6.1f} - {PHI_AWYC_BOUNDS[1]:>6.1f} deg")
    print(f"    c'_WYC   : {C_WYC_BOUNDS[0]:>6.1f} - {C_WYC_BOUNDS[1]:>6.1f} psf")
    print(f"    phi'_WYC : {PHI_WYC_BOUNDS[0]:>6.1f} - {PHI_WYC_BOUNDS[1]:>6.1f} deg")

    # Determine node count from first solved GSZ
    first_gsz = os.path.join(SOLVED_DIR, f"rain_{available[0]:04d}.gsz")
    n_nodes = 0
    with zipfile.ZipFile(first_gsz, "r") as z:
        for f in z.namelist():
            if f.startswith(ANALYSIS_SEEP + "/") and f.endswith("/node.csv"):
                content = z.read(f).decode("utf-8", errors="replace")
                reader = csv.DictReader(io.StringIO(content))
                n_nodes = max(int(row["Node"]) for row in reader
                              if "Node" in row)
                break
    if n_nodes == 0:
        print("\nERROR: Could not determine node count from solved GSZ")
        sys.exit(1)
    print(f"  Mesh nodes: {n_nodes}")

    # Load checkpoint
    completed = load_checkpoint()
    total_in_range = len(available) * N_STRENGTH
    done_in_range  = sum(1 for rid in available
                         for sid in range(1, N_STRENGTH + 1)
                         if (rid, sid) in completed)
    remaining = total_in_range - done_in_range
    print(f"  In this range: {done_in_range} done, {remaining} remaining")

    if not args.yes:
        resp = input(f"\nProceed with Stage 2? (Y/n): ").strip().lower()
        if resp == 'n':
            return

    # Prepare output (append mode — safe for parallel terminals)
    header = build_header(n_nodes)
    csv_is_new = not os.path.exists(OUT_CSV)

    os.makedirs(TEMP_ROOT, exist_ok=True)

    out_file = open(OUT_CSV, "a", newline="", encoding="utf-8")
    out_writer = csv.DictWriter(out_file, fieldnames=header)
    if csv_is_new:
        # Write metadata header
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for line in [
            "# Two-Stage Monte Carlo Training Database",
            "# Metro Center Slope, Jackson MS (Yazoo Clay)",
            f"# Generated: {now}",
            f"# Slope height: {HEIGHT} ft",
            f"# Rainfall scenarios: {len(available)} (from Stage 1)",
            f"# Strength combos: {N_STRENGTH} per scenario",
            f"# Total rows: {len(available) * N_STRENGTH}",
            f"# Strength bounds: c'_AWYC={C_AWYC_BOUNDS}, phi'_AWYC={PHI_AWYC_BOUNDS}",
            f"#                  c'_WYC={C_WYC_BOUNDS}, phi'_WYC={PHI_WYC_BOUNDS}",
            f"# Mesh nodes: {n_nodes}",
            "#",
        ]:
            out_file.write(line + "\n")
        out_writer.writeheader()

    chk_is_new = not os.path.exists(CHECKPOINT_CSV)
    chk_file = open(CHECKPOINT_CSV, "a", newline="")
    chk_writer = csv.writer(chk_file)
    if chk_is_new:
        chk_writer.writerow(["rain_id", "strength_id", "status"])

    success = 0
    failed  = 0
    total   = remaining
    solve_times = []

    for rid in available:
        gsz_path = os.path.join(SOLVED_DIR, f"rain_{rid:04d}.gsz")
        rp = rain_params[rid]

        # Read PWP once per rainfall scenario (from solved GSZ)
        # We need the min-FS step from Stage 1 to know which step has PWP
        # But since strength changes FS, we'll extract PWP from the NEW solve
        # However, PWP doesn't change — so we can cache it from Stage 1
        #
        # Strategy: extract PWP from ALL steps of the Stage 1 GSZ once,
        # then for each strength combo just find min-FS step and look up PWP.
        #
        # Actually simpler: just extract PWP after each Stage 2 solve,
        # since the SEEP/W results are identical.

        for sid in range(1, N_STRENGTH + 1):
            if (rid, sid) in completed:
                continue

            c_awyc, phi_awyc, c_wyc, phi_wyc = strength_samples[sid - 1]

            try:
                # Prepare work directory
                work_dir = os.path.join(TEMP_ROOT, f"r{rid:04d}_s{sid:03d}")
                if os.path.exists(work_dir):
                    shutil.rmtree(work_dir)
                os.makedirs(work_dir)

                # Build GSZ with new strength, keeping SEEP/W results
                work_gsz, n_stripped = prepare_strength_gsz(
                    gsz_path, c_awyc, phi_awyc, c_wyc, phi_wyc, work_dir)

                # Solve
                t0 = time.time()
                run_solver(solver, work_gsz)
                dt = time.time() - t0
                solve_times.append(dt)

                # Extract FS
                fs_all = extract_fs_all_steps(work_gsz)
                n_fs   = len(fs_all)
                min_fs = min(fs_all.values()) if fs_all else None
                min_step = min(fs_all, key=fs_all.get) if fs_all else None

                # Extract PWP at min-FS step
                pwp = read_all_node_pwp(work_gsz, min_step) if min_step else {}

                # Build output row
                row = {
                    "rain_id": rid,
                    "strength_id": sid,
                    "slope_height_ft": HEIGHT,
                    "return_period_yr":    rp["return_period_yr"],
                    "storm_duration_days": rp["storm_duration_days"],
                    "shape_param":         rp["shape_param"],
                    "antecedent_state":    rp["antecedent_state"],
                    "total_depth_in":      rp["total_depth_in"],
                    "API_7d_mm":           rp["API_7d_mm"],
                    "API_14d_mm":          rp["API_14d_mm"],
                    "API_21d_mm":          rp["API_21d_mm"],
                    "API_30d_mm":          rp["API_30d_mm"],
                    "c_awyc_psf":          c_awyc,
                    "phi_awyc_deg":        phi_awyc,
                    "c_wyc_psf":           c_wyc,
                    "phi_wyc_deg":         phi_wyc,
                }
                for n in range(1, n_nodes + 1):
                    row[f"PWP_N{n:03d}"] = round(pwp.get(n, float('nan')), 4)
                row["min_FS"]      = round(min_fs, 6) if min_fs else "N/A"
                row["min_FS_step"] = min_step if min_step is not None else "N/A"
                row["n_fs_steps"]  = n_fs
                row["solve_time_s"] = round(dt, 1)
                row["converged"]   = 1 if min_fs is not None else 0

                out_writer.writerow(row)
                out_file.flush()

                chk_writer.writerow([rid, sid, "OK"])
                chk_file.flush()

                success += 1
                fs_str = f"{min_fs:.4f}" if min_fs else "N/A"
                avg_t = np.mean(solve_times[-10:])
                print(f"  [R{rid:03d}/S{sid:02d}]  "
                      f"c'={c_awyc:6.1f}/{c_wyc:6.1f}  "
                      f"phi'={phi_awyc:4.1f}/{phi_wyc:4.1f}  "
                      f"FS={fs_str}  ({dt:.0f}s, avg={avg_t:.0f}s)  "
                      f"[{success}/{total}]")

            except Exception as e:
                failed += 1
                chk_writer.writerow([rid, sid, f"FAILED: {e}"])
                chk_file.flush()
                print(f"  [R{rid:03d}/S{sid:02d}]  FAILED - {e}")

            finally:
                if 'work_dir' in dir() and work_dir and os.path.exists(work_dir):
                    for _ in range(5):
                        try:
                            shutil.rmtree(work_dir)
                            break
                        except Exception:
                            time.sleep(1)

    out_file.close()
    chk_file.close()

    # Clean up temp root
    if os.path.exists(TEMP_ROOT):
        try:
            shutil.rmtree(TEMP_ROOT)
        except Exception:
            pass

    # Summary
    print(f"\n{'=' * 65}")
    print(f"Stage 2 Complete: {success} OK | {failed} failed")
    print(f"  Output: {OUT_CSV}")

    if solve_times:
        arr = np.array(solve_times)
        print(f"\n  Solve time stats:")
        print(f"    Mean   : {arr.mean():.1f}s")
        print(f"    Median : {np.median(arr):.1f}s")
        print(f"    Min    : {arr.min():.1f}s")
        print(f"    Max    : {arr.max():.1f}s")
        if arr.mean() < 60:
            print(f"    --> SEEP/W reuse is working (fast solves)")
        else:
            print(f"    --> Solver may be re-running SEEP/W (slow)")
            print(f"        Results are still correct, just not as fast")

    # Quick FS stats
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
        print(f"\n  FS summary ({len(arr)} valid samples):")
        print(f"    Mean        : {arr.mean():.4f}")
        print(f"    Std         : {arr.std():.4f}")
        print(f"    Min / Max   : {arr.min():.4f} / {arr.max():.4f}")
        print(f"    P(FS < 1.0) : {(arr < 1.0).mean()*100:.1f}%")
        print(f"    P(FS < 1.5) : {(arr < 1.5).mean()*100:.1f}%")
        if arr.std() < 0.01:
            print(f"\n  WARNING: FS std is very low — strength variation "
                  f"may not be propagating correctly")
        else:
            print(f"\n  FS spread looks good — surrogate training data is viable")

    print(f"\n{'=' * 65}")


if __name__ == "__main__":
    main()
