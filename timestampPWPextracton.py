"""
Extract All Timestep Data from Stage 1 GSZ Files
==================================================
Point this at your training folder. It auto-detects height subdirectories,
extracts PWP at ALL nodes at ALL timesteps + FS, and merges with
stage1_samples.csv for full rainfall metadata.

Output: Single CSV per height with one row per (rain_id, timestep).

Directory structure expected:
    training/
      H20/
        solved_gsz/rain_0001.gsz ... rain_0100.gsz
        stage1_samples.csv
        stage1_rainfall_log.csv
      H15/
        solved_gsz/rain_0001.gsz ... rain_NNNN.gsz
        stage1_samples.csv
      ...

Usage:
    python extract_all_timesteps.py E:/training                     # all heights
    python extract_all_timesteps.py E:/training --height 20         # just H20
    python extract_all_timesteps.py E:/training/H20/solved_gsz      # direct folder
    python extract_all_timesteps.py --single E:/training/H20/solved_gsz/rain_0001.gsz
"""

import sys, os, re, glob, zipfile, csv, io, argparse, time as timer_mod
from collections import OrderedDict

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
ANALYSIS_SEEP = "Rainfall Simulation"
ANALYSIS_FS   = "FS"


# ---------------------------------------------------------------------------
# Core extraction (from geostudio-probe skill patterns)
# ---------------------------------------------------------------------------

def discover_root_xml(z):
    root_xmls = [n for n in z.namelist() if n.endswith(".xml") and "/" not in n]
    if not root_xmls:
        raise RuntimeError("No root XML found in archive")
    return root_xmls[0]


def extract_time_mapping(z):
    target = f"{ANALYSIS_SEEP}/time.csv"
    if target not in z.namelist():
        return {}
    content = z.read(target).decode("utf-8", errors="replace")
    mapping = {}
    for row in csv.DictReader(io.StringIO(content)):
        step = int(row["Step"])
        t_s = float(row["Time"])
        mapping[step] = t_s / 86400.0
    return mapping


def extract_seep_steps(z):
    prefix = ANALYSIS_SEEP + "/"
    steps = set()
    for f in z.namelist():
        if f.startswith(prefix) and f.endswith("/node.csv") and "node-" not in f:
            parts = f[len(prefix):].split("/")
            if len(parts) == 2:
                try:
                    steps.add(int(parts[0]))
                except ValueError:
                    pass
    return sorted(steps)


def read_node_pwp(z, step_idx):
    target = f"{ANALYSIS_SEEP}/{step_idx:03d}/node.csv"
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


def extract_fs_at_step(z, step_idx):
    prefix = f"{ANALYSIS_FS}/{step_idx:03d}/"
    lf_files = [f for f in z.namelist()
                if f.startswith(prefix) and "lambdafos_" in f
                and f.endswith(".csv")]
    if not lf_files:
        return None
    content = z.read(lf_files[0]).decode("utf-8", errors="replace")
    best, best_diff = None, float("inf")
    for row in csv.DictReader(content.splitlines()):
        try:
            ff = float(row["FOSByForce"])
            fm = float(row["FOSByMoment"])
            d = abs(ff - fm)
            if d < best_diff:
                best_diff = d
                best = (ff + fm) / 2.0
        except (ValueError, KeyError):
            continue
    return best


def extract_rainfall_from_xml(z, root_xml_key):
    xml = z.read(root_xml_key).decode("utf-8", errors="replace")
    idx = xml.lower().find(">rainfall<")
    if idx == -1:
        return {}
    chunk = xml[idx:idx + 15000]
    points = re.findall(r'<Point X="(\d+)" Y="([\d.e+-]+)"', chunk)
    rain = OrderedDict()
    for x, y in points:
        day = int(x) / 86400.0
        rain[day] = float(y)
    return rain


def compute_step_rain(rain_daily, t_prev, t_curr):
    total = 0.0
    for day in sorted(rain_daily.keys()):
        if day <= t_prev:
            continue
        if day > t_curr:
            break
        total += rain_daily[day]
    return total


# ---------------------------------------------------------------------------
# Load stage1_samples.csv for metadata merge
# ---------------------------------------------------------------------------

def load_samples_metadata(samples_csv):
    if not os.path.exists(samples_csv):
        return {}
    meta = {}
    with open(samples_csv, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rid = int(row["rain_id"])
            meta[rid] = {
                "return_period_yr":    float(row["return_period_yr"]),
                "storm_duration_days": int(row["duration_days"]),
                "shape_param":         float(row["shape"]),
                "antecedent_state":    row["state"],
                "total_depth_in":      float(row["total_depth_in"]),
            }
    return meta


# ---------------------------------------------------------------------------
# Process one GSZ
# ---------------------------------------------------------------------------

def process_one_gsz(gsz_path, rain_id=None, metadata=None):
    fname = os.path.basename(gsz_path)

    if rain_id is None:
        match = re.search(r'(\d+)', fname)
        rain_id = int(match.group(1)) if match else 0

    rows = []

    with zipfile.ZipFile(gsz_path, "r") as z:
        root_xml = discover_root_xml(z)
        time_map = extract_time_mapping(z)
        if not time_map:
            return []

        seep_steps = extract_seep_steps(z)
        if not seep_steps:
            return []

        rain_daily = extract_rainfall_from_xml(z, root_xml)

        first_pwp = read_node_pwp(z, seep_steps[0])
        n_nodes = max(first_pwp.keys()) if first_pwp else 0

        fs_steps = set()
        for f in z.namelist():
            if (f.startswith(ANALYSIS_FS + "/") and "lambdafos_" in f
                    and f.endswith(".csv")):
                parts = f[len(ANALYSIS_FS + "/"):].split("/")
                if len(parts) == 2:
                    try:
                        fs_steps.add(int(parts[0]))
                    except ValueError:
                        pass

        meta = (metadata or {}).get(rain_id, {})

        for step_idx in seep_steps:
            elapsed_days = time_map.get(step_idx, -1)
            pwp = read_node_pwp(z, step_idx)
            fs = extract_fs_at_step(z, step_idx) if step_idx in fs_steps else None

            if step_idx == seep_steps[0]:
                t_prev = 0.0
            else:
                prev_idx = seep_steps[seep_steps.index(step_idx) - 1]
                t_prev = time_map.get(prev_idx, 0.0)
            step_rain = compute_step_rain(rain_daily, t_prev, elapsed_days)
            cum_rain = compute_step_rain(rain_daily, -1, elapsed_days)

            row = OrderedDict()
            row["rain_id"] = rain_id
            row["step"] = step_idx
            row["elapsed_days"] = round(elapsed_days, 2)
            row["step_rain_in"] = round(step_rain, 6)
            row["cum_rain_in"] = round(cum_rain, 6)
            row["fs"] = round(fs, 6) if fs is not None else ""
            row["return_period_yr"] = meta.get("return_period_yr", "")
            row["storm_duration_days"] = meta.get("storm_duration_days", "")
            row["shape_param"] = meta.get("shape_param", "")
            row["antecedent_state"] = meta.get("antecedent_state", "")
            row["total_depth_in"] = meta.get("total_depth_in", "")
            row["n_nodes"] = len(pwp)
            for n in range(1, n_nodes + 1):
                row[f"PWP_N{n:03d}"] = round(pwp.get(n, float("nan")), 4)

            rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Auto-detect directory structure
# ---------------------------------------------------------------------------

def detect_structure(path):
    results = []

    # Check for H{xx} subdirectories
    h_dirs = sorted(glob.glob(os.path.join(path, "H*")))
    h_dirs = [d for d in h_dirs
              if os.path.isdir(d) and re.match(r'H\d+', os.path.basename(d))]

    if h_dirs:
        for hd in h_dirs:
            height = int(re.search(r'H(\d+)', os.path.basename(hd)).group(1))
            gsz_dir = os.path.join(hd, "solved_gsz")
            if not os.path.isdir(gsz_dir):
                gsz_dir = hd
            samples = os.path.join(hd, "stage1_samples.csv")
            if not os.path.exists(samples):
                samples = None
            results.append((height, gsz_dir, samples))
        return results

    # Check for solved_gsz/ subfolder
    gsz_sub = os.path.join(path, "solved_gsz")
    if os.path.isdir(gsz_sub):
        height_match = re.search(r'H(\d+)', path)
        height = int(height_match.group(1)) if height_match else None
        samples = os.path.join(path, "stage1_samples.csv")
        if not os.path.exists(samples):
            samples = None
        return [(height, gsz_sub, samples)]

    # Direct folder of GSZ files
    gsz_files = glob.glob(os.path.join(path, "rain_*.gsz"))
    if gsz_files:
        height_match = re.search(r'H(\d+)', path)
        height = int(height_match.group(1)) if height_match else None
        parent = os.path.dirname(path)
        samples = os.path.join(parent, "stage1_samples.csv")
        if not os.path.exists(samples):
            samples = None
        return [(height, path, samples)]

    return []


# ---------------------------------------------------------------------------
# Batch extraction
# ---------------------------------------------------------------------------

def extract_height(height, gsz_dir, samples_csv, output_csv):
    files = sorted(glob.glob(os.path.join(gsz_dir, "rain_*.gsz")))
    if not files:
        print(f"  No rain_*.gsz files in {gsz_dir}")
        return 0

    metadata = load_samples_metadata(samples_csv) if samples_csv else {}
    if metadata:
        print(f"  Loaded metadata for {len(metadata)} rain scenarios")

    all_rows = []
    failed = 0

    for i, gsz_path in enumerate(files):
        fname = os.path.basename(gsz_path)
        try:
            rows = process_one_gsz(gsz_path, metadata=metadata)
            all_rows.extend(rows)

            fs_vals = [r["fs"] for r in rows if r["fs"] != ""]
            min_fs = f"{min(fs_vals):.4f}" if fs_vals else "N/A"
            rp = rows[0].get("return_period_yr", "") if rows else ""
            rp_str = f"RP={float(rp):>7.1f}yr" if rp else ""

            print(f"    [{i+1:>4}/{len(files)}] {fname}: "
                  f"{len(rows)} steps  min_FS={min_fs}  {rp_str}")

        except Exception as e:
            failed += 1
            print(f"    [{i+1:>4}/{len(files)}] {fname}: FAILED - {e}")

    if not all_rows:
        print(f"  No data extracted!")
        return 0

    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
    fieldnames = list(all_rows[0].keys())
    n_nodes = sum(1 for k in fieldnames if k.startswith("PWP_"))
    rain_ids = set(r["rain_id"] for r in all_rows)

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        h_str = f"H{height}" if height else "unknown"
        f.write(f"# Transient PWP + FS - {h_str}\n")
        f.write(f"# Source: {gsz_dir}\n")
        f.write(f"# Rain scenarios: {len(rain_ids)}, "
                f"Total rows: {len(all_rows)}\n")
        f.write(f"# Units: time=days, rain=inches, PWP=psf, "
                f"FS=dimensionless\n#\n")

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n  -> {output_csv}")
    print(f"     {len(rain_ids)} rain_ids x ~{len(all_rows)//len(rain_ids)} steps "
          f"= {len(all_rows)} rows, {len(fieldnames)} cols, "
          f"{os.path.getsize(output_csv)/1e6:.1f} MB")

    return len(all_rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract all timestep PWP + FS from Stage 1 GSZ files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s E:/training                       # all heights
  %(prog)s E:/training --height 20           # just H20
  %(prog)s E:/training/H20/solved_gsz        # direct folder
  %(prog)s --single rain_0001.gsz            # test one file
        """)

    parser.add_argument("path", nargs="?", default=None,
                        help="Training directory or GSZ folder")
    parser.add_argument("--height", type=int, default=None,
                        help="Process only this height (e.g., 20)")
    parser.add_argument("--single", type=str, default=None,
                        help="Test on a single GSZ file")
    parser.add_argument("--output_dir", type=str, default=None,
                        help="Override output directory")

    args = parser.parse_args()

    if args.single:
        print(f"Processing: {args.single}\n")
        rows = process_one_gsz(args.single)
        print(f"Extracted {len(rows)} rows")
        if rows:
            print(f"\n{'Step':>4}  {'Day':>6}  {'StepRain':>10}  {'CumRain':>10}  {'FS':>10}")
            print("-" * 50)
            for r in rows:
                fs_str = f"{r['fs']:.4f}" if r['fs'] != "" else "N/A"
                print(f"{r['step']:>4}  {r['elapsed_days']:>6.1f}  "
                      f"{r['step_rain_in']:>10.4f}  {r['cum_rain_in']:>10.2f}  "
                      f"{fs_str:>10}")
        return

    if not args.path:
        parser.print_help()
        return

    path = os.path.abspath(args.path)
    if not os.path.exists(path):
        print(f"ERROR: Path not found: {path}")
        sys.exit(1)

    targets = detect_structure(path)
    if not targets:
        print(f"ERROR: No GSZ files found in {path}")
        sys.exit(1)

    if args.height:
        targets = [(h, d, s) for h, d, s in targets if h == args.height]
        if not targets:
            print(f"ERROR: No H{args.height} directory found")
            sys.exit(1)

    print("=" * 70)
    print("EXTRACT ALL TIMESTEPS FROM STAGE 1 GSZ FILES")
    print("=" * 70)
    print(f"Root: {path}")
    for h, d, s in targets:
        n = len(glob.glob(os.path.join(d, "rain_*.gsz")))
        print(f"  H{h or '?'}: {n} files in {d}")
    print("=" * 70)

    t_start = timer_mod.time()
    total_rows = 0

    for height, gsz_dir, samples_csv in targets:
        n_files = len(glob.glob(os.path.join(gsz_dir, "rain_*.gsz")))
        if n_files == 0:
            continue

        h_str = f"H{height}" if height else "direct"
        print(f"\n--- {h_str}: {n_files} files ---")

        if args.output_dir:
            out_dir = args.output_dir
        elif height:
            out_dir = os.path.dirname(gsz_dir)  # parent of solved_gsz/
        else:
            out_dir = gsz_dir

        output_csv = os.path.join(out_dir, "transient_data.csv")
        total_rows += extract_height(height, gsz_dir, samples_csv, output_csv)

    elapsed = timer_mod.time() - t_start
    print(f"\n{'=' * 70}")
    print(f"DONE in {elapsed:.1f}s - {total_rows} total rows extracted")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()