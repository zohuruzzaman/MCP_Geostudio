"""
SLOPE/W Strength Back-Calibration
====================================
Back-calibrates effective cohesion (c') and friction angle (phi') for the
Weathered Yazoo Clay in Active Zone (AWYC, Material ID=2) against the observed
slope failure (FS ≈ 1.0) at Metro-Center.

Uses Metro-Center-calibrated.gsz as the base — this file has the SEEP/W
hydraulic parameters already calibrated (KSat_WYC=5.64e-6, KYX_AWYC=80827).

The "FS" analysis in the project is a standalone SLOPE/W run with imported
field pore-pressure data (not dependent on SEEP/W). It gives FS ~ 1.016 with
the current lab-derived strength parameters — very close to the failure target.

Parameters calibrated:
  - c_AWYC   : effective cohesion (psf) for Weathered YC in Active Zone
  - phi_AWYC : effective friction angle (°) for Weathered YC in Active Zone

Target:
  - Minimum FS = 1.0 (forensic back-analysis at failure)
  - Objective: minimise (FS - 1.0)^2

Approach:
  1. Copy calibrated GSZ to temp folder, patch c'/phi' in XML
  2. Run GeoStudio solver
  3. Read converged Morgenstern-Price FS from FS/001/lambdafos_*.csv
  4. scipy.optimize (Nelder-Mead) minimises (FS - 1.0)^2
  5. Save calibrated-slope GSZ and trial log

Usage:
    python calibrate_slope.py
"""

import sys, os, glob, shutil, zipfile, csv, re, warnings, subprocess, time
import numpy as np
from scipy.optimize import minimize

warnings.filterwarnings("ignore")

sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

CALIB_DIR    = r"E:\Github\MCP_Geostudio\calibration"
BASE_GSZ     = os.path.join(CALIB_DIR, "Metro-Center-calibrated.gsz")   # seep-calibrated base
OUT_DIR      = CALIB_DIR
LOG_CSV      = os.path.join(OUT_DIR, "slope_calibration_trial_log.csv")
OUT_GSZ      = os.path.join(OUT_DIR, "Metro-Center-calibrated-slope.gsz")

# Target analysis (standalone SLOPE/W with imported pore pressures)
SLOPE_ANALYSIS = "FS"

# Material to calibrate (ID=2: Weathered Yazoo Clay in Active Zone)
TARGET_MAT_ID   = 2
TARGET_MAT_NAME = "Weathered Yazoo Clay in Active Zone (AWYC)"

# Starting / baseline values from model
C_PRIME_BASE   = 79.3    # psf
PHI_PRIME_BASE = 19.0    # degrees

# Search bounds
C_PRIME_MIN,   C_PRIME_MAX   =   0.0, 300.0   # psf
PHI_PRIME_MIN, PHI_PRIME_MAX =  10.0,  28.0   # degrees

# Optimiser
FS_TARGET       = 1.0
MAX_OPT_ITER    = 20
SOLVER_TIMEOUT  = 1800   # seconds per trial (30 min)

# Analysis folders (needed to identify root XML inside the zip)
ANALYSIS_FOLDERS = ["Initial Condition", "Rainfall Simulation", "Slope Stability", "FS"]


# ---------------------------------------------------------------------------
# Solver detection
# ---------------------------------------------------------------------------

def find_solver():
    patterns = [
        r"C:\Program Files\Seequent\GeoStudio 2024.2\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio*\Bin\GeoCmd.exe",
        r"C:\Program Files\GeoSlope\GeoStudio*\GeoCmd.exe",
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


# ---------------------------------------------------------------------------
# XML patching
# ---------------------------------------------------------------------------

def patch_material_strength(xml_str, mat_id, c_prime, phi_prime):
    """
    Replace CohesionPrime and PhiPrime inside the <Material> block
    whose <ID> matches mat_id.
    """
    # Match the whole <Material>...</Material> block containing <ID>{mat_id}</ID>
    mat_pattern = (
        r'(<Material>\s*<ID>' + str(mat_id) + r'</ID>'
        r'(?:(?!</Material>).)*</Material>)'
    )

    def replacer(m):
        block = m.group(1)
        block = re.sub(
            r'<CohesionPrime>[^<]*</CohesionPrime>',
            f'<CohesionPrime>{c_prime:.4f}</CohesionPrime>',
            block
        )
        block = re.sub(
            r'<PhiPrime>[^<]*</PhiPrime>',
            f'<PhiPrime>{phi_prime:.4f}</PhiPrime>',
            block
        )
        return block

    new_xml, count = re.subn(mat_pattern, replacer, xml_str, flags=re.DOTALL)
    if count == 0:
        print(f"  WARNING: Material ID={mat_id} block not found in XML")
    return new_xml


# ---------------------------------------------------------------------------
# Prepare trial GSZ
# ---------------------------------------------------------------------------

def prepare_trial_gsz(trial_idx, c_prime, phi_prime):
    """
    Copy base GSZ to an isolated temp folder, patch c'/phi' in root XML,
    re-zip with correct entry names.
    Returns (temp_gsz_path, temp_dir).
    """
    temp_dir = os.path.join(OUT_DIR, f"_slope_{trial_idx:04d}")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    gsz_name = os.path.basename(BASE_GSZ)
    temp_gsz = os.path.join(temp_dir, "Metro-Center_slope.gsz")
    shutil.copy2(BASE_GSZ, temp_gsz)

    with zipfile.ZipFile(temp_gsz, "r") as zin:
        all_items = zin.infolist()
        all_data  = {item.filename: zin.read(item.filename) for item in all_items}

    # Identify root XML (not inside an analysis subfolder)
    root_xml_key = next(
        (k for k in all_data
         if k.endswith(".xml")
         and not any(k.startswith(af + "/") for af in ANALYSIS_FOLDERS)),
        None
    )
    if root_xml_key is None:
        raise RuntimeError(f"Root XML not found in {temp_gsz}")

    xml_stem = os.path.splitext(os.path.basename(root_xml_key))[0]
    xml_str  = all_data[root_xml_key].decode("utf-8")

    # Patch strength parameters
    xml_str = patch_material_strength(xml_str, TARGET_MAT_ID, c_prime, phi_prime)

    # Re-zip
    with zipfile.ZipFile(temp_gsz, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data  = all_data[fname]
            if xml_stem + ".xml" in fname:
                fixed_name = xml_stem + ".xml"
                for af in ANALYSIS_FOLDERS:
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
        raise RuntimeError(
            f"Solver error {result.returncode}: {result.stderr or result.stdout}"
        )


# ---------------------------------------------------------------------------
# Read FS from solved archive
# ---------------------------------------------------------------------------

def read_slope_fs(temp_gsz, analysis_name):
    """
    Read the converged Morgenstern-Price FS from the lambdafos CSV
    inside the solved archive.

    Looks for the lambdafos_*.csv file in {analysis_name}/001/ (or /000/).
    The converged FS is where |FOSByForce - FOSByMoment| is minimised.
    Falls back to FOSByMoment at lambda=0 if convergence row isn't clear.

    Returns float FS, or None on failure.
    """
    try:
        with zipfile.ZipFile(temp_gsz, "r") as z:
            all_files = z.namelist()
            prefix = analysis_name + "/"

            # Find lambdafos CSV — try step 001 first, then 000
            lambda_key = None
            for step in ["001", "000", "002"]:
                candidates = [
                    f for f in all_files
                    if f.startswith(prefix + step + "/") and "lambdafos" in f and f.endswith(".csv")
                ]
                if candidates:
                    lambda_key = candidates[0]
                    break

            if lambda_key is None:
                print(f"  WARNING: no lambdafos CSV found under '{prefix}'")
                # Probe what's available
                avail = [f for f in all_files if f.startswith(prefix)]
                for f in avail[:15]:
                    print(f"    available: {f}")
                return None

            content = z.read(lambda_key).decode("utf-8", errors="ignore")
            reader  = csv.DictReader(content.splitlines())
            rows    = []
            for row in reader:
                try:
                    lam     = float(row["LambdaX"])
                    fos_f   = float(row["FOSByForce"])
                    fos_m   = float(row["FOSByMoment"])
                    rows.append((lam, fos_f, fos_m))
                except (KeyError, ValueError):
                    continue

            if not rows:
                print(f"  WARNING: lambdafos CSV empty or unreadable: {lambda_key}")
                return None

            # Find converged row: minimum |FOSByForce - FOSByMoment|
            rows_valid = [(lam, ff, fm) for lam, ff, fm in rows
                          if np.isfinite(ff) and np.isfinite(fm)
                          and ff > 0 and fm > 0 and ff < 50 and fm < 50]

            if not rows_valid:
                return None

            best = min(rows_valid, key=lambda r: abs(r[1] - r[2]))
            converged_fs = (best[1] + best[2]) / 2.0
            return converged_fs

    except Exception as e:
        print(f"  WARNING: could not read FS — {e}")
        return None


# ---------------------------------------------------------------------------
# Objective
# ---------------------------------------------------------------------------

iter_count = [0]
trial_log  = []

def objective(params, solver):
    c_prime, phi_prime = params

    # Clip to bounds
    c_prime   = float(np.clip(c_prime,   C_PRIME_MIN,   C_PRIME_MAX))
    phi_prime = float(np.clip(phi_prime, PHI_PRIME_MIN, PHI_PRIME_MAX))

    iter_count[0] += 1
    idx = iter_count[0]
    print(f"  Trial {idx:3d}: c'={c_prime:.2f} psf  phi'={phi_prime:.3f}°")

    temp_dir = None
    try:
        temp_gsz, temp_dir = prepare_trial_gsz(idx, c_prime, phi_prime)
        run_solver(solver, temp_gsz)

        fs = read_slope_fs(temp_gsz, SLOPE_ANALYSIS)

        if fs is None:
            print(f"    -> FS unreadable — penalty")
            score = 9999.0
        else:
            score = (fs - FS_TARGET) ** 2
            print(f"    -> FS = {fs:.4f}  |FS-1| = {abs(fs - FS_TARGET):.4f}")

        trial_log.append({
            "trial":     idx,
            "c_prime":   round(c_prime,   4),
            "phi_prime": round(phi_prime, 4),
            "fs":        round(fs, 6) if fs is not None else None,
            "objective": round(score, 8),
        })
        return score

    except Exception as e:
        print(f"    -> FAILED: {e}")
        trial_log.append({
            "trial":     idx,
            "c_prime":   round(c_prime, 4),
            "phi_prime": round(phi_prime, 4),
            "fs":        None,
            "objective": 9999.0,
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

def save_calibrated_gsz(c_prime, phi_prime):
    """Save a permanent copy of the GSZ with calibrated strength parameters."""
    shutil.copy2(BASE_GSZ, OUT_GSZ)

    with zipfile.ZipFile(OUT_GSZ, "r") as zin:
        all_items = zin.infolist()
        all_data  = {item.filename: zin.read(item.filename) for item in all_items}

    root_xml_key = next(
        (k for k in all_data
         if k.endswith(".xml")
         and not any(k.startswith(af + "/") for af in ANALYSIS_FOLDERS)),
        None
    )
    if root_xml_key is None:
        print("WARNING: could not save — root XML not found")
        return

    xml_stem = os.path.splitext(os.path.basename(root_xml_key))[0]
    xml_str  = all_data[root_xml_key].decode("utf-8")
    xml_str  = patch_material_strength(xml_str, TARGET_MAT_ID, c_prime, phi_prime)

    with zipfile.ZipFile(OUT_GSZ, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data  = all_data[fname]
            if xml_stem + ".xml" in fname:
                fixed_name = xml_stem + ".xml"
                for af in ANALYSIS_FOLDERS:
                    if fname.startswith(af + "/") or ("/" + af + "/") in fname:
                        fixed_name = af + "/" + xml_stem + ".xml"
                        break
                item.filename = fixed_name
                if fixed_name == xml_stem + ".xml":
                    data = xml_str.encode("utf-8")
            zout.writestr(item, data)

    print(f"\nCalibrated-slope GSZ saved to: {OUT_GSZ}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 65)
    print("SLOPE/W Strength Back-Calibration — Metro-Center")
    print(f"  Base GSZ       : {BASE_GSZ}")
    print(f"  Target analysis: {SLOPE_ANALYSIS} (standalone SLOPE/W, imported pore-p)")
    print(f"  Calibrate mat  : ID={TARGET_MAT_ID}  {TARGET_MAT_NAME}")
    print(f"  FS target      : {FS_TARGET}")
    print(f"  Max iterations : {MAX_OPT_ITER}")
    print("=" * 65)

    if not os.path.exists(BASE_GSZ):
        print(f"\nERROR: Base GSZ not found: {BASE_GSZ}")
        sys.exit(1)

    solver = find_solver()
    if solver is None:
        print("\nERROR: GeoStudio solver not found.")
        sys.exit(1)
    print(f"\n  Solver: {solver}")

    # Check Trial-0: run the base GSZ once to confirm starting FS
    print(f"\nBaseline check — running base GSZ to confirm starting FS...")
    temp_dir0 = os.path.join(OUT_DIR, "_slope_0000")
    if os.path.exists(temp_dir0):
        shutil.rmtree(temp_dir0)
    os.makedirs(temp_dir0)
    base_copy = os.path.join(temp_dir0, "Metro-Center_slope.gsz")
    shutil.copy2(BASE_GSZ, base_copy)
    try:
        run_solver(solver, base_copy)
        base_fs = read_slope_fs(base_copy, SLOPE_ANALYSIS)
        if base_fs is not None:
            print(f"  Baseline FS = {base_fs:.4f}  (target = {FS_TARGET})")
        else:
            print("  WARNING: could not read baseline FS — continuing anyway")
    except Exception as e:
        print(f"  WARNING: baseline solve failed ({e}) — continuing")
    finally:
        _cleanup(temp_dir0)

    # Starting point
    x0 = np.array([C_PRIME_BASE, PHI_PRIME_BASE])
    print(f"\nStarting parameters:")
    print(f"  c'  = {C_PRIME_BASE:.2f} psf")
    print(f"  phi'= {PHI_PRIME_BASE:.2f} °")
    print(f"\nRunning optimisation (max {MAX_OPT_ITER} iterations)...\n")

    result = minimize(
        objective,
        x0,
        args=(solver,),
        method="Nelder-Mead",
        options={
            "maxiter": MAX_OPT_ITER,
            "xatol":   1.0,      # tolerance in psf / degrees
            "fatol":   0.0001,   # tolerance in (FS-1)^2
            "disp":    True,
            "initial_simplex": np.array([
                [C_PRIME_BASE,        PHI_PRIME_BASE      ],
                [C_PRIME_BASE * 0.7,  PHI_PRIME_BASE      ],
                [C_PRIME_BASE,        PHI_PRIME_BASE - 1.0],
            ]),
        }
    )

    best_c, best_phi = result.x
    best_c   = float(np.clip(best_c,   C_PRIME_MIN,   C_PRIME_MAX))
    best_phi = float(np.clip(best_phi, PHI_PRIME_MIN, PHI_PRIME_MAX))

    # Best trial from log
    valid = [t for t in trial_log if t["fs"] is not None]
    if valid:
        best_trial = min(valid, key=lambda t: abs(t["fs"] - FS_TARGET))
        best_c, best_phi = best_trial["c_prime"], best_trial["phi_prime"]

    print(f"\n{'=' * 65}")
    print(f"Optimisation complete")
    print(f"  Status      : {result.message}")
    print(f"  Iterations  : {result.nit}")
    print(f"  Best (FS-1)^2 : {result.fun:.6f}")
    print(f"  Best FS       : {best_trial['fs']:.4f}  (target = {FS_TARGET})")
    print(f"  c'  AWYC      : {best_c:.2f} psf   (prior: {C_PRIME_BASE:.2f})")
    print(f"  phi' AWYC     : {best_phi:.3f} °    (prior: {PHI_PRIME_BASE:.2f})")

    # Save trial log
    if trial_log:
        with open(LOG_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trial_log[0].keys())
            writer.writeheader()
            writer.writerows(trial_log)
        print(f"\nTrial log saved to: {LOG_CSV}")

    # Save calibrated GSZ
    save_calibrated_gsz(best_c, best_phi)

    # Cleanup
    for item in os.listdir(OUT_DIR):
        if item.startswith("_slope_"):
            _cleanup(os.path.join(OUT_DIR, item))

    print("\nDone.")


if __name__ == "__main__":
    main()
