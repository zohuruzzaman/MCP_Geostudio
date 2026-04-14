"""
Build 4-Year Transient SEEP/W from Real Sensor Data
=====================================================
Takes the S2 precipitation record (Aug 2018 - Jun 2021) and patches it
into calibrated GSZ files for each slope height.

This generates the PWP training data for Module 1 (infiltration forward model).
Unlike IDF-based MC scenarios, real rainfall includes:
  - Zero rain days (68% of all days) -> PWP decreases via drainage
  - Sub-Ksat light rain -> partial infiltration
  - Heavy storms -> Ksat-limited infiltration
  - Seasonal wet/dry cycles -> full PWP range

The script:
  1. Reads S2.csv, aggregates hourly precip to daily (mm -> in/day)
  2. Builds GeoStudio rainfall function (~1034 daily points)
  3. Builds time increments (save every 3 days = ~345 PWP snapshots)
  4. Patches into calibrated GSZ, strips old results
  5. Outputs ready-to-solve GSZ per height

After solving, use extract_all_timesteps.py to pull the PWP data.

Usage:
    python build_transient_seepw.py --sensor_csv data/S2.csv --height 20
    python build_transient_seepw.py --sensor_csv data/S2.csv --all
    python build_transient_seepw.py --sensor_csv data/S2.csv --height 20 --solve

Estimated solve time: 2-4 hours per height (SEEP/W + SLOPE/W transient, ~345 steps)
"""

import sys, os, re, glob, shutil, zipfile, csv, io, argparse, warnings, subprocess, time
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIG - Update these paths for your system
# ---------------------------------------------------------------------------

# Calibrated GSZ per height (same as stage1_rainfall_mc.py)
GSZ_BY_HEIGHT = {
    15: os.path.join("calibration", "Metro-Center-slope-H15.gsz"),
    20: os.path.join("calibration", "Metro-Center-slope-H20.gsz"),
    25: os.path.join("calibration", "Metro-Center-slope-H25.gsz"),
    30: os.path.join("calibration", "Metro-Center-slope-H30.gsz"),
    40: os.path.join("calibration", "Metro-Center-slope-H40.gsz"),
}

# Output directory
OUT_DIR = "training"

# Analysis names (must match what's in the GSZ)
ANALYSIS_FS   = "FS"
ANALYSIS_SEEP = "Rainfall Simulation"
ANALYSIS_INIT = "Initial Condition"

# Save interval (days) - every 3 days gives ~345 snapshots for ~1034 days
SAVE_INTERVAL_DAYS = 1

# Solver
SOLVER_TIMEOUT  = 1440000   # 4 hours max (long transient)
SOLVER_OVERRIDE = None

sys.path.insert(0, r"C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages")


# ---------------------------------------------------------------------------
# Step 1: Read sensor data and build daily rainfall
# ---------------------------------------------------------------------------

def load_daily_precipitation(sensor_csv, position="Crest"):
    """
    Read S2.csv, filter to one position, aggregate hourly -> daily.
    Returns DataFrame with columns: [date, precip_mm, precip_in]
    """
    print(f"  Reading {sensor_csv}...")
    df = pd.read_csv(sensor_csv, parse_dates=["timestamp"], low_memory=False)

    # Filter to one position (avoid triple-counting)
    df = df[df["position"] == position].copy()
    df = df.sort_values("timestamp")

    print(f"  {len(df)} hourly records ({position})")
    print(f"  Date range: {df.timestamp.min().date()} to {df.timestamp.max().date()}")

    # Aggregate to daily
    daily = df.groupby(df.timestamp.dt.date)["Precipitation"].sum().reset_index()
    daily.columns = ["date", "precip_mm"]
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values("date").reset_index(drop=True)

    # Convert mm -> inches
    daily["precip_in"] = daily["precip_mm"] / 25.4

    # Fill any gaps with zero rainfall
    full_range = pd.date_range(daily.date.min(), daily.date.max(), freq="D")
    daily = daily.set_index("date").reindex(full_range, fill_value=0).reset_index()
    daily.columns = ["date", "precip_mm", "precip_in"]

    # Summary
    total_days = len(daily)
    annual = daily.precip_in.sum() / (total_days / 365)
    zero_days = (daily.precip_in == 0).sum()
    sub_ksat = ((daily.precip_in > 0) & (daily.precip_in < 0.104)).sum()
    over_ksat = (daily.precip_in >= 0.104).sum()

    print(f"  Total days: {total_days}")
    print(f"  Annual rainfall: {annual:.1f} in/yr")
    print(f"  Zero rain days: {zero_days} ({zero_days/total_days*100:.0f}%)")
    print(f"  Sub-Ksat days (< 0.104 in): {sub_ksat}")
    print(f"  Over-Ksat days (>= 0.104 in): {over_ksat}")
    print(f"  Max daily: {daily.precip_in.max():.2f} in")

    return daily


# ---------------------------------------------------------------------------
# Step 2: Build GeoStudio rainfall function
# ---------------------------------------------------------------------------

def build_rainfall_points(daily):
    """
    Build list of {X, Y} dicts for GeoStudio rainfall function.
    X = elapsed seconds, Y = rainfall depth in inches for that day.
    GeoStudio Step function: Y is the rate applied from X to the next X.
    """
    points = []
    t0 = daily.date.iloc[0]

    for _, row in daily.iterrows():
        elapsed_s = int((row["date"] - t0).total_seconds())
        points.append({
            "X": str(elapsed_s),
            "Y": max(0.0, float(row["precip_in"]))
        })

    return points


# ---------------------------------------------------------------------------
# Step 3: Build time increments
# ---------------------------------------------------------------------------

def build_time_steps(total_days, save_every=SAVE_INTERVAL_DAYS):
    """
    Build save points for SEEP/W transient analysis.
    Uniform interval for simplicity.
    Returns list of elapsed seconds.
    """
    steps = []
    day = save_every
    while day <= total_days:
        steps.append(day * 86400)
        day += save_every

    # Make sure the last day is included
    if steps[-1] != total_days * 86400:
        steps.append(total_days * 86400)

    return steps


# ---------------------------------------------------------------------------
# Step 4: XML patching (reused from stage1_rainfall_mc.py)
# ---------------------------------------------------------------------------

def _patch_rainfall(xml_text, rain_points):
    """Patch rainfall climate function. Case-insensitive search."""
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
    """Patch TimeIncrements for a named analysis."""
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
# Step 5: Build the patched GSZ
# ---------------------------------------------------------------------------

def build_transient_gsz(height, rain_points, time_steps_s, total_days,
                        output_path):
    """
    Patch calibrated GSZ with real rainfall and long time increments.
    Strips all old results so solver regenerates from scratch.
    """
    gsz_path = GSZ_BY_HEIGHT[height]
    if not os.path.exists(gsz_path):
        raise FileNotFoundError(f"Calibrated GSZ not found: {gsz_path}")

    gsz_name = os.path.basename(gsz_path)
    gsz_stem = os.path.splitext(gsz_name)[0]
    total_duration_s = total_days * 86400

    # Read base archive
    with zipfile.ZipFile(gsz_path, "r") as zin:
        all_items = zin.infolist()
        all_data = {item.filename: zin.read(item.filename) for item in all_items}

    # Find root XML
    analysis_folders = [ANALYSIS_FS, ANALYSIS_SEEP, ANALYSIS_INIT,
                        "Slope Stability"]
    root_xml_key = next(
        (k for k in all_data
         if k.endswith(".xml") and "/" not in k), None)
    if root_xml_key is None:
        raise RuntimeError(f"Root XML not found in {gsz_name}")

    print(f"  Root XML: {root_xml_key}")
    xml_str = all_data[root_xml_key].decode("utf-8")

    # Patch rainfall
    xml_str = _patch_rainfall(xml_str, rain_points)
    print(f"  Rainfall patched: {len(rain_points)} daily points")

    # Patch time increments for BOTH transient analyses
    xml_str = _patch_time_increments(xml_str, ANALYSIS_SEEP,
                                     time_steps_s, total_duration_s)
    xml_str = _patch_time_increments(xml_str, ANALYSIS_FS,
                                     time_steps_s, total_duration_s)
    print(f"  Time increments patched: {len(time_steps_s)} save points "
          f"(every {SAVE_INTERVAL_DAYS} days)")

    # Strip ALL old results
    result_prefixes = tuple(af + "/" for af in analysis_folders)

    skipped = 0
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in all_items:
            fname = item.filename
            data = all_data[fname]

            if any(fname.startswith(p) for p in result_prefixes):
                skipped += 1
                continue

            if fname == root_xml_key:
                data = xml_str.encode("utf-8")

            zout.writestr(item, data)

    size_mb = os.path.getsize(output_path) / 1e6
    print(f"  Stripped {skipped} old result files")
    print(f"  Saved: {output_path} ({size_mb:.1f} MB)")

    return output_path


# ---------------------------------------------------------------------------
# Step 6: Solver (optional - user can run manually)
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
    print(f"\n  Solving {os.path.basename(gsz_path)}...")
    print(f"  This will take 2-4 hours for the full transient.")
    print(f"  Solver: {solver_exe}")

    t0 = time.time()
    result = subprocess.run(
        [solver_exe, "/solve", gsz_path],
        capture_output=True, text=True, timeout=SOLVER_TIMEOUT
    )
    dt = time.time() - t0

    if result.returncode != 0:
        raise RuntimeError(
            f"Solver exit {result.returncode}: "
            f"{(result.stderr or result.stdout)[:500]}")

    print(f"  Solved in {dt/60:.1f} minutes")
    return dt


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build 4-year transient SEEP/W from real S2 sensor data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --sensor_csv data/S2.csv --height 20
  %(prog)s --sensor_csv data/S2.csv --all
  %(prog)s --sensor_csv data/S2.csv --height 20 --solve
  %(prog)s --sensor_csv data/S2.csv --all --save_every 1  # daily saves
        """)

    parser.add_argument("--sensor_csv", type=str, required=True,
                        help="Path to S2.csv sensor data file")
    parser.add_argument("--height", type=int, default=None,
                        choices=sorted(GSZ_BY_HEIGHT.keys()),
                        help="Build for one height only (15, 20, 25, or 30)")
    parser.add_argument("--all", action="store_true",
                        help="Build for ALL heights")
    parser.add_argument("--solve", action="store_true",
                        help="Also run GeoCmd solver after building")
    parser.add_argument("--save_every", type=int, default=SAVE_INTERVAL_DAYS,
                        help=f"Save PWP every N days (default: {SAVE_INTERVAL_DAYS})")
    parser.add_argument("--output_dir", type=str, default=None,
                        help="Override output directory")
    parser.add_argument("--position", type=str, default="Crest",
                        choices=["Crest", "Middle", "Toe"],
                        help="Which sensor position for precipitation (default: Crest)")

    args = parser.parse_args()

    if not args.height and not args.all:
        parser.print_help()
        print("\nERROR: Specify --height <N> or --all")
        return

    # Determine which heights to process
    heights = sorted(GSZ_BY_HEIGHT.keys()) if args.all else [args.height]

    # Check all GSZ files exist before starting
    for h in heights:
        if not os.path.exists(GSZ_BY_HEIGHT[h]):
            print(f"ERROR: Calibrated GSZ not found for H{h}: {GSZ_BY_HEIGHT[h]}")
            print(f"  Check that your calibration/ folder has the right files.")
            sys.exit(1)

    print("=" * 70)
    print("BUILD 4-YEAR TRANSIENT SEEP/W FROM REAL SENSOR DATA")
    print("=" * 70)
    print(f"  Sensor data: {args.sensor_csv}")
    print(f"  Heights: {[f'H{h}' for h in heights]}")
    print(f"  Save interval: every {args.save_every} days")
    print("=" * 70)

    # Step 1: Load precipitation
    print("\n--- STEP 1: Load daily precipitation ---")
    daily = load_daily_precipitation(args.sensor_csv, position=args.position)

    # Step 2: Build rainfall function
    print("\n--- STEP 2: Build GeoStudio rainfall function ---")
    rain_points = build_rainfall_points(daily)
    total_days = len(daily)
    print(f"  {len(rain_points)} daily rainfall points")
    print(f"  Duration: {total_days} days ({total_days/365:.1f} years)")

    # Step 3: Build time steps
    print("\n--- STEP 3: Build time increments ---")
    time_steps_s = build_time_steps(total_days, save_every=args.save_every)
    print(f"  {len(time_steps_s)} save points (every {args.save_every} days)")

    # Quick rainfall summary for verification
    print("\n--- RAINFALL VERIFICATION ---")
    daily_in = [float(p["Y"]) for p in rain_points]
    print(f"  Total depth: {sum(daily_in):.2f} in over {total_days} days")
    print(f"  Annual rate: {sum(daily_in)/(total_days/365):.1f} in/yr")
    print(f"  Max daily: {max(daily_in):.3f} in")
    print(f"  Zero days: {sum(1 for d in daily_in if d == 0)}")
    print(f"  First 10 days: {[f'{d:.3f}' for d in daily_in[:10]]}")

    # Find solver if --solve requested
    solver = None
    if args.solve:
        solver = find_solver()
        if solver is None:
            print("\nWARNING: Solver not found. Will build GSZ but not solve.")
            print("  You can solve manually with:")
            print('  "C:\\Program Files\\Seequent\\GeoStudio 2025*\\Bin\\GeoCmd.exe" /solve <file.gsz>')

    # Step 4: Build GSZ for each height
    output_files = []
    for h in heights:
        out_dir = args.output_dir or os.path.join(OUT_DIR, f"H{h}")
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, f"transient_real_H{h}.gsz")

        print(f"\n--- H{h}: Building transient GSZ ---")
        build_transient_gsz(h, rain_points, time_steps_s, total_days, output_path)
        output_files.append((h, output_path))

        # Solve if requested
        if solver and args.solve:
            try:
                run_solver(solver, output_path)
            except Exception as e:
                print(f"  SOLVE FAILED: {e}")
                print(f"  You can retry manually.")

    # Summary
    print(f"\n{'=' * 70}")
    print("BUILD COMPLETE")
    print(f"{'=' * 70}")
    for h, path in output_files:
        solved = " (SOLVED)" if args.solve and solver else ""
        print(f"  H{h}: {path}{solved}")

    if not args.solve:
        print(f"\nNext steps:")
        print(f"  1. Solve each GSZ (2-4 hours per height):")
        for h, path in output_files:
            print(f"     GeoCmd.exe /solve \"{path}\"")
        print(f"  2. Extract PWP data:")
        for h, path in output_files:
            print(f"     python extract_all_timesteps.py --single \"{path}\"")

    print(f"\nExpected output per height:")
    print(f"  ~{len(time_steps_s)} timesteps x 201 nodes = "
          f"~{len(time_steps_s)} rows in transient_data.csv")
    print(f"  PWP should vary significantly (68% dry days, seasonal cycles)")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()