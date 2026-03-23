"""
Monte Carlo Simulation - GeoStudio SLOPE/W + SEEP/W
=====================================================
Sequential execution. Each iteration:
  1. Copies the original .gsz to an isolated temp folder (new name)
  2. Opens it with PyGeoStudio, modifies material + rainfall params
  3. Saves via study.save() - the correct PyGeoStudio API
  4. Runs GeoCmd.exe /solve on the temp file
  5. Reads FS from lambdafos_*.csv in the solved archive
  6. Writes row to CSV immediately, deletes temp folder

This approach avoids GeoStudio's XML backup/restore mechanism
which was overwriting our changes in the previous approach.

Varies per iteration:
  - CohesionPrime : Lognormal (COV 40% weathered, 30% intact)
  - PhiPrime      : Normal    (COV 12% weathered,  8% intact)
  - Rainfall (non-zero points only) : Lognormal (COV 35%)

Output CSV:
  - Metadata block at top (# comment lines)
  - 500 data rows (inputs + FS outputs)
  - Summary statistics row at bottom

Usage:
    python monte_carlo_geostudio.py
"""

import sys, os, glob, shutil, zipfile, csv, warnings, subprocess, time
import numpy as np
from datetime import datetime

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

FILE           = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"
N_ITER         = 500
SEED           = 42
SLOPE_ANALYSES = ["Slope Stability", "FS"]

# Per-material COV - calibrated to Yazoo Clay (Phoon & Kulhawy 1999)
# Distributions: CohesionPrime = Lognormal | PhiPrime = Normal
MATERIAL_CONFIG = {
    "Silty Clay":                           {"cov_c": 0.35, "cov_phi": 0.10},
    "Weathered Yazoo Clay in Active Zone":  {"cov_c": 0.40, "cov_phi": 0.12},
    "Weathered Yazoo Clay":                 {"cov_c": 0.40, "cov_phi": 0.12},
    "Unweathered Yazoo Clay":               {"cov_c": 0.30, "cov_phi": 0.08},
}

# Rainfall COV - Lognormal, non-zero time points only
COV_RAINFALL = 0.35

# Manual solver override - leave None for auto-detection
SOLVER_EXE_OVERRIDE = None

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')
import PyGeoStudio as pgs

OUT_DIR  = os.path.dirname(os.path.abspath(FILE))
OUT_CSV  = os.path.join(OUT_DIR, "monte_carlo_results.csv")

MAT_SHORT = {
    "Silty Clay":                           "SC",
    "Weathered Yazoo Clay in Active Zone":  "AWYC",
    "Weathered Yazoo Clay":                 "WYC",
    "Unweathered Yazoo Clay":               "UYC",
}


# ---------------------------------------------------------------------------
# Solver detection
# ---------------------------------------------------------------------------

def find_solver():
    if SOLVER_EXE_OVERRIDE and os.path.exists(SOLVER_EXE_OVERRIDE):
        return SOLVER_EXE_OVERRIDE
    patterns = [
        r"C:\Program Files\Seequent\GeoStudio 2024.2\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio 2024.2\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio*\Bin\GeoCmd.exe",
        r"C:\Program Files\Seequent\GeoStudio*\GeoCmd.exe",
        r"C:\Program Files\GeoSlope\GeoStudio*\GeoCmd.exe",
        r"C:\Program Files (x86)\Seequent\GeoStudio*\GeoCmd.exe",
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


# ---------------------------------------------------------------------------
# Baseline reading
# ---------------------------------------------------------------------------

def get_baselines():
    """Read material baselines and rainfall time-series from original .gsz."""
    study = pgs.GeoStudioFile(FILE)
    materials = {}
    for mat in study.materials:
        name = mat.data.get("Name")
        if name not in MATERIAL_CONFIG:
            continue
        ss = mat.data.get("StressStrain")
        if ss is None or not ss.data:
            continue
        materials[name] = {
            "cohesion": float(ss.data.get("CohesionPrime", 0)),
            "phi":      float(ss.data.get("PhiPrime", 0)),
        }

    gsz_stem = os.path.splitext(os.path.basename(FILE))[0]
    with zipfile.ZipFile(FILE, 'r') as z:
        # Root XML may be stored as just "Stem.xml" or with full path
        xml_candidates = [f for f in z.namelist()
                          if f.endswith(gsz_stem + ".xml")
                          and not any(f.startswith(a + "/") for a in
                                      ["FS", "Slope Stability",
                                       "Initial Condition", "Rainfall Simulation"])]
        if not xml_candidates:
            raise RuntimeError(f"Cannot find root XML for {gsz_stem} in {FILE}")
        xml_str = z.read(xml_candidates[0]).decode("utf-8")

    rainfall = parse_rainfall_points(xml_str)
    return materials, rainfall


def parse_rainfall_points(xml_str):
    """Extract rainfall time-series points as list of dicts.
    Handles both <Name>rainfall</Name> and <n>rainfall</n> tag formats.
    """
    points = []
    in_rainfall = False
    in_points   = False
    for line in xml_str.splitlines():
        ls = line.strip()
        low = ls.lower()
        if ">rainfall<" in low:
            in_rainfall = True
        if in_rainfall and "<Points" in ls:
            in_points = True
        if in_points and ls.startswith("<Point "):
            x = ls.split('X="')[1].split('"')[0]
            y = float(ls.split('Y="')[1].split('"')[0])
            points.append({"X": x, "Y": y, "Y_orig": y})
        if in_points and "</Points>" in ls:
            break
    return points


def sample_lognormal(rng, mean, cov):
    """Lognormal - always positive, right-skewed. Correct for cohesion."""
    if mean <= 0:
        return 0.0
    sig = np.sqrt(np.log(1 + cov ** 2))
    mu  = np.log(mean) - 0.5 * sig ** 2
    return float(rng.lognormal(mu, sig))


def sample_normal_positive(rng, mean, cov):
    """Normal clipped at 1.0 deg minimum - suitable for friction angle."""
    return float(max(rng.normal(mean, mean * cov), 1.0))


def sample_iteration(iter_idx, base_mats, base_rain):
    rng = np.random.default_rng(SEED + iter_idx)

    mats = {}
    for name, cfg in MATERIAL_CONFIG.items():
        base = base_mats.get(name)
        if base is None:
            continue
        mats[name] = {
            "cohesion": sample_lognormal(rng, base["cohesion"], cfg["cov_c"]),
            "phi":      sample_normal_positive(rng, base["phi"], cfg["cov_phi"]),
        }

    rain = []
    for pt in base_rain:
        new_y = sample_lognormal(rng, pt["Y_orig"], COV_RAINFALL) if pt["Y_orig"] > 0 else 0.0
        rain.append({"X": pt["X"], "Y": new_y})

    return mats, rain


# ---------------------------------------------------------------------------
# Write and modify temp .gsz using PyGeoStudio API + XML for rainfall
# ---------------------------------------------------------------------------

def prepare_temp_gsz(iter_idx, sampled_mats, sampled_rain):
    """
    Copy original to isolated temp folder, modify via PyGeoStudio + XML,
    save via study.save(). Returns path to temp .gsz and temp folder.
    """
    # Unique temp folder per iteration - no cross-iteration conflicts
    temp_dir = os.path.join(OUT_DIR, f"_mc_iter_{iter_idx:04d}")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    # Keep original filename so GeoStudio finds the XML by the same stem
    gsz_name = os.path.basename(FILE)
    temp_gsz = os.path.join(temp_dir, gsz_name)
    shutil.copy2(FILE, temp_gsz)

    # --- Modify materials via PyGeoStudio API ---
    study = pgs.GeoStudioFile(temp_gsz)
    for mat in study.materials:
        name = mat.data.get("Name")
        if name not in sampled_mats:
            continue
        ss = mat.data.get("StressStrain")
        if ss is None or not ss.data:
            continue
        ss.data["CohesionPrime"] = str(sampled_mats[name]["cohesion"])
        ss.data["PhiPrime"]      = str(sampled_mats[name]["phi"])
    study.save()

    # --- Fix zip entry names + patch rainfall ---
    # PyGeoStudio saves XML with full absolute path as entry name
    # e.g. "E:/path/_mc_iter_0001/Metro-Center.xml"
    # GeoCmd expects just "Metro-Center.xml" - rename entries on re-zip
    gsz_stem = os.path.splitext(os.path.basename(FILE))[0]
    analysis_folders = SLOPE_ANALYSES + ["Initial Condition", "Rainfall Simulation"]

    with zipfile.ZipFile(temp_gsz, 'r') as zin:
        all_items = zin.infolist()
        all_data  = {item.filename: zin.read(item.filename) for item in all_items}

    # Find root XML key (full path entry ending with stem.xml, not under analysis folder)
    root_xml_key = None
    for key in all_data:
        if key.endswith(gsz_stem + ".xml") and not any(
            key.startswith(af + "/") for af in analysis_folders
        ):
            root_xml_key = key
            break
    if root_xml_key is None:
        raise RuntimeError(f"No root XML found. Entries: {list(all_data.keys())[:10]}")

    # Patch rainfall in root XML
    xml_str      = all_data[root_xml_key].decode("utf-8")
    modified_xml = update_rainfall_xml(xml_str, sampled_rain)

    # Re-zip: strip full path from all XML entry names so GeoCmd can find them
    with zipfile.ZipFile(temp_gsz, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data  = all_data[fname]

            if gsz_stem + ".xml" in fname:
                # Determine correct short name
                fixed_name = gsz_stem + ".xml"
                for af in analysis_folders:
                    if fname.startswith(af + "/") or ("/" + af + "/") in fname:
                        fixed_name = af + "/" + gsz_stem + ".xml"
                        break
                item.filename = fixed_name
                if fixed_name == gsz_stem + ".xml":
                    data = modified_xml.encode("utf-8")

            zout.writestr(item, data)

    return temp_gsz, temp_dir


def update_rainfall_xml(xml_str, sampled_rain):
    """Replace rainfall Y values in XML - only the <n>rainfall</n> function."""
    start_tag = "<n>rainfall</n>"
    pos = xml_str.find(start_tag)
    if pos == -1:
        return xml_str
    pts_open  = xml_str.find("<Points", pos)
    pts_close = xml_str.find("</Points>", pts_open) + len("</Points>")
    if pts_open == -1 or pts_close <= len("</Points>"):
        return xml_str
    new_pts = f'<Points Len="{len(sampled_rain)}">\n'
    for pt in sampled_rain:
        new_pts += f'            <Point X="{pt["X"]}" Y="{pt["Y"]:.8f}" />\n'
    new_pts += "          </Points>"
    return xml_str[:pts_open] + new_pts + xml_str[pts_close:]


# ---------------------------------------------------------------------------
# Run GeoStudio solver
# ---------------------------------------------------------------------------

def run_solver(solver_exe, temp_gsz):
    result = subprocess.run(
        [solver_exe, "/solve", temp_gsz],
        capture_output=True,
        text=True,
        timeout=600  # 10 min max per iteration
    )
    if result.returncode != 0:
        raise RuntimeError(f"Solver exited {result.returncode}: {result.stderr or result.stdout}")


# ---------------------------------------------------------------------------
# Extract FS from solved archive
# ---------------------------------------------------------------------------

def extract_fs(temp_gsz, analysis_name):
    """
    Read critical FS from lambdafos_*.csv inside solved archive.
    Converged FS = row where |FOSByForce - FOSByMoment| is minimised.
    Returns average of force and moment FS at convergence.
    """
    with zipfile.ZipFile(temp_gsz, 'r') as z:
        files    = z.namelist()
        prefix   = analysis_name + "/"
        lf_files = [f for f in files if f.startswith(prefix)
                    and "lambdafos_" in f and f.endswith(".csv")]
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


# ---------------------------------------------------------------------------
# Cleanup temp folder - retry on Windows file lock
# ---------------------------------------------------------------------------

def cleanup_temp(temp_dir):
    for attempt in range(10):
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            return
        except Exception:
            time.sleep(2)


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def build_header(base_rain):
    nonzero_idx = [i for i, p in enumerate(base_rain) if p["Y_orig"] > 0]
    header = ["iter"]
    for name, short in MAT_SHORT.items():
        header += [f"{short}_cohesion_psf", f"{short}_phi_deg"]
    for idx in nonzero_idx:
        day = int(float(base_rain[idx]["X"]) / 86400)
        header.append(f"rainfall_day{day}_in_per_day")
    for a in SLOPE_ANALYSES:
        header.append(f"FS_{a.replace(' ', '_')}")
    return header, nonzero_idx


def build_data_row(i, s_mats, s_rain, nonzero_rain_idx, base_rain, fs_vals):
    row = {"iter": i}
    for name, short in MAT_SHORT.items():
        s = s_mats.get(name)
        row[f"{short}_cohesion_psf"] = round(s["cohesion"], 4) if s else ""
        row[f"{short}_phi_deg"]      = round(s["phi"],      4) if s else ""
    for idx in nonzero_rain_idx:
        day = int(float(base_rain[idx]["X"]) / 86400)
        row[f"rainfall_day{day}_in_per_day"] = round(s_rain[idx]["Y"], 6)
    for a, fs in zip(SLOPE_ANALYSES, fs_vals):
        row[f"FS_{a.replace(' ', '_')}"] = round(fs, 6) if fs is not None else "N/A"
    return row


def write_metadata(f, solver, base_mats, base_rain):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# Monte Carlo Simulation - GeoStudio SLOPE/W + SEEP/W",
        f"# Generated   : {now}",
        f"# Input file  : {FILE}",
        f"# Iterations  : {N_ITER}",
        f"# Random seed : {SEED}",
        f"# Solver      : {solver}",
        f"# Execution   : Sequential (chained SEEP/W -> SLOPE/W per iteration)",
        f"#",
        f"# --- Soil Parameter Distributions ---",
        f"# CohesionPrime = Lognormal | PhiPrime = Normal (min 1 deg)",
        f"# Reference    : Phoon & Kulhawy (1999), Duncan (2000)",
        f"#",
    ]
    for name, short in MAT_SHORT.items():
        base = base_mats.get(name, {})
        cfg  = MATERIAL_CONFIG.get(name, {})
        lines.append(
            f"# {short} ({name}): "
            f"c_mean={base.get('cohesion','?')} psf  COV={cfg.get('cov_c',0)*100:.0f}% | "
            f"phi_mean={base.get('phi','?')} deg  COV={cfg.get('cov_phi',0)*100:.0f}%"
        )
    lines += [
        f"#",
        f"# --- Rainfall Distribution ---",
        f"# Lognormal | COV = {COV_RAINFALL*100:.0f}% | Zero points fixed",
    ]
    for p in base_rain:
        if p["Y_orig"] > 0:
            day = int(float(p["X"]) / 86400)
            lines.append(f"#   Day {day}: baseline = {p['Y_orig']} in/day")
    lines += [
        f"#",
        f"# --- Column Units ---",
        f"# cohesion = psf | phi = degrees | rainfall = in/day | FS = dimensionless",
        f"#",
    ]
    for line in lines:
        f.write(line + "\n")


def write_summary_row(writer, header, all_fs):
    row = {col: "" for col in header}
    row["iter"] = "SUMMARY"
    for a, fs_list in all_fs.items():
        col = f"FS_{a.replace(' ', '_')}"
        if not fs_list:
            continue
        arr = np.array(fs_list)
        row[col] = (
            f"mean={arr.mean():.4f} | "
            f"std={arr.std():.4f} | "
            f"min={arr.min():.4f} | "
            f"max={arr.max():.4f} | "
            f"P(FS<1.0)={(arr < 1.0).mean()*100:.2f}% | "
            f"P(FS<1.5)={(arr < 1.5).mean()*100:.2f}%"
        )
    writer.writerow(row)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("GeoStudio Monte Carlo Simulation")
    print(f"  File       : {FILE}")
    print(f"  Iterations : {N_ITER}")
    print(f"  Analyses   : {SLOPE_ANALYSES}")
    print(f"  Execution  : Sequential")
    print("=" * 60)

    solver = find_solver()
    if solver is None:
        print("\nERROR: GeoStudio solver not found.")
        print("Set SOLVER_EXE_OVERRIDE at the top of this script.")
        print("Expected: C:\\Program Files\\Seequent\\GeoStudio 2024.2\\Bin\\GeoCmd.exe")
        sys.exit(1)
    print(f"\n  Solver : {solver}")

    print("\nReading baseline values...")
    base_mats, base_rain = get_baselines()

    for name, vals in base_mats.items():
        cfg = MATERIAL_CONFIG[name]
        print(f"  {MAT_SHORT[name]}: c={vals['cohesion']} psf "
              f"(COV={cfg['cov_c']*100:.0f}%), "
              f"phi={vals['phi']} deg "
              f"(COV={cfg['cov_phi']*100:.0f}%)")

    nonzero = [(int(float(p["X"])/86400), p["Y_orig"]) for p in base_rain if p["Y_orig"] > 0]
    print(f"  Rainfall: {len(nonzero)} non-zero points "
          f"(COV={COV_RAINFALL*100:.0f}%): {nonzero}")

    header, nonzero_rain_idx = build_header(base_rain)
    all_fs  = {a: [] for a in SLOPE_ANALYSES}
    failed  = 0

    print(f"\nRunning {N_ITER} iterations...\n")

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as csvfile:
        write_metadata(csvfile, solver, base_mats, base_rain)
        writer = csv.DictWriter(csvfile, fieldnames=header)
        writer.writeheader()

        for i in range(1, N_ITER + 1):
            s_mats, s_rain = sample_iteration(i, base_mats, base_rain)
            fs_vals  = [None] * len(SLOPE_ANALYSES)
            temp_dir = None

            try:
                temp_gsz, temp_dir = prepare_temp_gsz(i, s_mats, s_rain)
                run_solver(solver, temp_gsz)

                for j, a in enumerate(SLOPE_ANALYSES):
                    fs = extract_fs(temp_gsz, a)
                    fs_vals[j] = fs
                    if fs is not None:
                        all_fs[a].append(fs)

                row = build_data_row(i, s_mats, s_rain, nonzero_rain_idx, base_rain, fs_vals)
                writer.writerow(row)
                csvfile.flush()

                fs_display = [f"{fs:.4f}" if fs is not None else "N/A" for fs in fs_vals]
                print(f"  [{i:>4}/{N_ITER}]  FS = {fs_display}")

            except Exception as e:
                failed += 1
                row = {"iter": i}
                for col in header[1:]:
                    row[col] = "ERROR"
                writer.writerow(row)
                csvfile.flush()
                print(f"  [{i:>4}/{N_ITER}]  FAILED - {e}")

            finally:
                # Always clean up temp folder
                if temp_dir:
                    cleanup_temp(temp_dir)

        write_summary_row(writer, header, all_fs)

    # Final cleanup of any stray temp folders
    for item in os.listdir(OUT_DIR):
        if item.startswith("_mc_iter_"):
            cleanup_temp(os.path.join(OUT_DIR, item))

    print(f"\n{'=' * 60}")
    print(f"Completed : {N_ITER - failed}/{N_ITER} succeeded | {failed} failed")
    print(f"Output    : {OUT_CSV}")
    print(f"\n--- Factor of Safety Summary ---")
    for a in SLOPE_ANALYSES:
        fs_list = all_fs[a]
        if not fs_list:
            continue
        arr = np.array(fs_list)
        print(f"\n  {a}:")
        print(f"    Mean FS     : {arr.mean():.4f}")
        print(f"    Std Dev     : {arr.std():.4f}")
        print(f"    Min / Max   : {arr.min():.4f} / {arr.max():.4f}")
        print(f"    P(FS < 1.0) : {(arr < 1.0).mean()*100:.2f}%  <- probability of failure")
        print(f"    P(FS < 1.5) : {(arr < 1.5).mean()*100:.2f}%  <- marginal safety")


if __name__ == "__main__":
    main()