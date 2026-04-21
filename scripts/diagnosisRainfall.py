"""
Diagnose Rainfall Patching Issue
==================================
Checks whether the rainfall function in a solved GSZ matches
what stage1_rainfall_mc.py should have written.

Run on one of your H20 solved GSZ files:
    python diagnose_rainfall.py E:/training/H20/solved_gsz/rain_0001.gsz

Also run on the BASE calibrated GSZ to compare:
    python diagnose_rainfall.py E:/calibration/Metro-Center-slope-H20.gsz
"""

import sys, os, re, zipfile, csv, io
import numpy as np

def read_rainfall_from_xml(gsz_path):
    """Extract raw rainfall function from GSZ XML."""
    with zipfile.ZipFile(gsz_path, "r") as z:
        root_xmls = [n for n in z.namelist() if n.endswith(".xml") and "/" not in n]
        if not root_xmls:
            print("ERROR: No root XML found")
            return
        root = root_xmls[0]
        xml = z.read(root).decode("utf-8", errors="replace")

    print(f"Root XML: {root}")
    print(f"XML size: {len(xml)} chars")

    # Find rainfall function
    idx = xml.lower().find(">rainfall<")
    if idx == -1:
        print("ERROR: '>rainfall<' not found in XML")
        # Try to find any ClimateFn
        climate_matches = list(re.finditer(r'<ClimateFn>', xml))
        print(f"  Found {len(climate_matches)} <ClimateFn> blocks")
        for i, m in enumerate(climate_matches):
            snippet = xml[m.start():m.start()+200]
            print(f"  Block {i}: {snippet}...")
        return

    print(f"\n'>rainfall<' found at position {idx}")

    # Extract the full ClimateFn block
    fn_start = xml.rfind("<ClimateFn>", 0, idx)
    fn_end = xml.find("</ClimateFn>", idx)
    if fn_start == -1 or fn_end == -1:
        print("ERROR: Could not find <ClimateFn> boundaries")
        return

    climate_block = xml[fn_start:fn_end + len("</ClimateFn>")]
    print(f"ClimateFn block: {len(climate_block)} chars")

    # Show the block (truncated)
    print(f"\n--- RAW CLIMATEFN BLOCK (first 500 chars) ---")
    print(climate_block[:500])
    if len(climate_block) > 500:
        print(f"... ({len(climate_block) - 500} more chars)")

    # Extract Points
    points_match = re.search(r'<Points Len="(\d+)">(.*?)</Points>',
                             climate_block, re.DOTALL)
    if not points_match:
        print("\nERROR: <Points> block not found in rainfall function")
        return

    n_pts = int(points_match.group(1))
    pts_block = points_match.group(2)
    points = re.findall(r'<Point X="([^"]+)" Y="([^"]+)"', pts_block)

    print(f"\n--- RAINFALL POINTS ({n_pts} declared, {len(points)} found) ---")
    print(f"{'Day':>6} {'X_seconds':>12} {'Y_value':>14} {'Notes':>20}")
    print("-" * 55)

    total = 0
    for x_str, y_str in points[:45]:  # first 45 days
        x = float(x_str)
        y = float(y_str)
        day = x / 86400.0
        total += y

        note = ""
        if y > 10:
            note = "<-- EXTREME!"
        elif y > 1:
            note = "<-- high"
        elif y < 0.001 and y > 0:
            note = "<-- trace"

        print(f"{day:>6.1f} {x:>12.0f} {y:>14.6f} {note:>20}")

    if len(points) > 45:
        print(f"  ... {len(points) - 45} more points")

    print(f"\n  Total depth (sum of Y): {total:.2f}")
    print(f"  Total days: {float(points[-1][0])/86400:.1f}")

    # Check if there's a unit or scaling tag nearby
    print(f"\n--- UNIT/SCALING CHECKS ---")
    for tag in ["<Unit>", "<Scale>", "<Factor>", "<Multiplier>",
                "<RainType>", "<Type>", "<DataUnit>"]:
        idx2 = climate_block.find(tag)
        if idx2 != -1:
            end = climate_block.find("</" + tag[1:], idx2)
            if end != -1:
                print(f"  Found: {climate_block[idx2:end + len(tag) + 1]}")


def compute_expected_rainfall(rain_id):
    """Compute what build_full_rainfall should produce for rain_id=1."""
    from scipy.stats import qmc

    SEED = 42
    NOAA_RETURN_PERIODS = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]
    NOAA_IDF = {
        1:  [3.77, 4.36, 5.34, 6.18, 7.38, 8.32, 9.28, 10.3, 11.7, 12.7],
        2:  [4.38, 5.04, 6.12, 7.03, 8.29, 9.28, 10.3, 11.3, 12.6, 13.7],
        3:  [4.82, 5.50, 6.61, 7.54, 8.83, 9.83, 10.8, 11.9, 13.2, 14.3],
        4:  [5.21, 5.89, 7.01, 7.95, 9.25, 10.3, 11.3, 12.3, 13.7, 14.7],
        7:  [6.20, 6.90, 8.05, 9.01, 10.3, 11.4, 12.4, 13.4, 14.8, 15.9],
        10: [7.04, 7.78, 8.99, 10.0, 11.4, 12.5, 13.5, 14.6, 16.1, 17.2],
    }
    API_BOUNDS = {
        "dry":    (0, 4, 0, 15, 0, 30, 0, 88),
        "normal": (4, 50, 10, 80, 20, 120, 88, 200),
        "wet":    (50, 150, 70, 200, 100, 280, 200, 350),
    }

    sampler = qmc.LatinHypercube(d=4, seed=SEED)
    lhs = sampler.random(n=100)
    s = lhs[rain_id - 1]

    rp = float(10 ** (s[0] * 3.0))
    duration = int(np.clip(round(1 + s[1] * 9), 1, 10))
    shape = float(s[2])
    state = "dry" if s[3] < 0.25 else ("wet" if s[3] >= 0.75 else "normal")

    # IDF interpolation
    rp_arr = np.array(NOAA_RETURN_PERIODS, dtype=float)
    dur_arr = np.array(sorted(NOAA_IDF.keys()), dtype=float)
    rp_c = np.clip(rp, 1.0, 1000.0)

    if duration <= dur_arr[0]:
        d_lo = d_hi = dur_arr[0]
    elif duration >= dur_arr[-1]:
        d_lo = d_hi = dur_arr[-1]
    else:
        d_lo = dur_arr[dur_arr <= duration][-1]
        d_hi = dur_arr[dur_arr >= duration][0]

    def interp_rp(depths):
        return float(10 ** np.interp(
            np.log10(rp_c), np.log10(rp_arr),
            np.log10(np.array(depths, dtype=float))))

    if d_lo == d_hi:
        total_depth = interp_rp(NOAA_IDF[int(d_lo)])
    else:
        depth_lo = interp_rp(NOAA_IDF[int(d_lo)])
        depth_hi = interp_rp(NOAA_IDF[int(d_hi)])
        t = (np.log10(duration) - np.log10(d_lo)) / (np.log10(d_hi) - np.log10(d_lo))
        total_depth = float(10 ** (np.log10(depth_lo) + t * (np.log10(depth_hi) - np.log10(depth_lo))))

    # Build rainfall
    rng = np.random.default_rng(SEED + rain_id)
    b = API_BOUNDS[state]
    a7  = rng.uniform(b[0], b[1])
    a14 = rng.uniform(max(b[2], a7), b[3])
    a21 = rng.uniform(max(b[4], a14), b[5])
    a30 = rng.uniform(max(b[6], a21), b[7])

    mm_to_in = 1.0 / 25.4
    antecedent = np.zeros(30)
    antecedent[23:30] = (a7 * mm_to_in) / 7.0
    antecedent[16:23] = max(0.0, (a14 - a7) * mm_to_in) / 7.0
    antecedent[9:16] = max(0.0, (a21 - a14) * mm_to_in) / 7.0
    antecedent[0:9] = max(0.0, (a30 - a21) * mm_to_in) / 9.0

    # Storm
    if duration == 1:
        storm = np.array([total_depth])
    else:
        days = np.linspace(0, 1, duration)
        if abs(shape - 0.5) < 0.01:
            w = np.ones(duration)
        elif shape < 0.5:
            w = np.exp(-4.0 * (0.5 - shape) * days)
        else:
            w = np.exp(4.0 * (shape - 0.5) * days)
        w /= w.sum()
        storm = total_depth * w

    all_depths = np.concatenate([antecedent, storm, np.full(5, 0.01)])

    print(f"\n--- EXPECTED RAINFALL for rain_id={rain_id} ---")
    print(f"  RP={rp:.3f}yr, dur={duration}d, shape={shape:.4f}, state={state}")
    print(f"  Total storm depth: {total_depth:.4f} in")
    print(f"  API: 7d={a7:.1f}mm, 14d={a14:.1f}mm, "
          f"21d={a21:.1f}mm, 30d={a30:.1f}mm")
    print(f"\n  {'Day':>6} {'Expected_in/day':>15}")
    print("  " + "-" * 25)
    for d, v in enumerate(all_depths[:45]):
        marker = ""
        if d == 30:
            marker = " <-- storm starts"
        elif d == 30 + duration:
            marker = " <-- storm ends"
        print(f"  {d:>6} {v:>15.6f}{marker}")
    print(f"\n  Total expected: {sum(all_depths):.4f} in")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python diagnose_rainfall.py <path_to.gsz>")
        print("  python diagnose_rainfall.py <path_to.gsz> --compare <rain_id>")
        sys.exit(1)

    gsz_path = sys.argv[1]
    if not os.path.exists(gsz_path):
        print(f"ERROR: File not found: {gsz_path}")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"RAINFALL DIAGNOSTIC: {os.path.basename(gsz_path)}")
    print(f"{'=' * 60}")

    read_rainfall_from_xml(gsz_path)

    # Also compute expected if --compare flag given
    if "--compare" in sys.argv:
        idx = sys.argv.index("--compare")
        rid = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 1
        compute_expected_rainfall(rid)
    else:
        # Auto-detect rain_id from filename
        import re as re_mod
        match = re_mod.search(r'(\d+)', os.path.basename(gsz_path))
        if match:
            rid = int(match.group(1))
            compute_expected_rainfall(rid)

    print(f"\n{'=' * 60}")
    print("PASTE THIS OUTPUT BACK TO CLAUDE")
    print(f"{'=' * 60}")