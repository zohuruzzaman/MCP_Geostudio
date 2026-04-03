"""
Diagnostic: SEEP/W Full Pipeline Test
=======================================
Tests ALL fixes in one run:
  1. Dumps original TimeIncrements XML (save-steps diagnosis)
  2. Patches Ksat with FIXED search window (was 500, now to </Material>)
  3. Verifies each patch took effect with before/after prints
  4. Strips SLOPE/W analyses (halves solve time)
  5. Solves and inspects what got saved
  6. Runs a SECOND solve with different Ksat to confirm RMSE changes

Run from: E:\Github\MCP_Geostudio\
Usage:    python calibration\diag_seep.py
"""

import sys, os, re, shutil, zipfile, csv, io, glob, subprocess, time, struct
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
CALIB_DIR      = r"E:\Github\MCP_Geostudio\calibration"
BASE_GSZ       = os.path.join(CALIB_DIR, "Metro-Center_cal.gsz")
SENSOR_CSV     = os.path.join(CALIB_DIR, "S2.csv")
DIAG_DIR       = os.path.join(CALIB_DIR, "_diag_test")
SEEP_FOLDER    = "Rainfall Simulation"
SOLVER_TIMEOUT = 900

CALIB_START    = "2018-08-22"
CALIB_END      = "2018-12-16"

CREST_X        = 195.0
SURFACE_Y      = 83.0
KPA_TO_PSF     = 20.8854

ORIG_KSAT_WYC  = 1.004e-07
ORIG_KSAT_UYC  = 1.004e-07

# Two test parameter sets to confirm RMSE actually changes
TEST_A = {"ksat_wyc": 5.640e-06, "kyx_awyc": 80827.0, "ksat_uyc": 1.004e-07}
TEST_B = {"ksat_wyc": 5.640e-04, "kyx_awyc": 80827.0, "ksat_uyc": 1.004e-05}

# Result folders to strip
RESULT_PREFIXES = [] 


def find_solver():
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


# ===================================================================
# PART 1: TimeIncrements diagnosis
# ===================================================================

def dump_time_increments(xml_text, label):
    """Extract and print the TimeIncrements block for Rainfall Simulation."""
    print(f"\n{'='*65}")
    print(f"TimeIncrements ({label})")
    print(f"{'='*65}")

    idx = xml_text.find(">Rainfall Simulation<")
    if idx == -1:
        print("  NOT FOUND: '>Rainfall Simulation<'")
        for variant in [">rainfall simulation<", ">Rainfall simulation<"]:
            pos = xml_text.lower().find(variant)
            if pos != -1:
                print(f"  Found case variant at pos {pos}")
                idx = pos
                break
        if idx == -1:
            return

    analysis_end = xml_text.find("</Analysis>", idx)
    if analysis_end == -1:
        print("  ERROR: no </Analysis> found")
        return

    chunk = xml_text[idx:analysis_end]

    ti_start = chunk.find("<TimeIncrements")
    if ti_start == -1:
        print("  ERROR: no <TimeIncrements in analysis block")
        return

    ti_end = chunk.find("</TimeIncrements>", ti_start)
    if ti_end == -1:
        print("  ERROR: no </TimeIncrements>")
        return

    ti_block = chunk[ti_start:ti_end + len("</TimeIncrements>")]

    # Print full block
    for line in ti_block.splitlines():
        print(f"  {line}")

    # Stats
    saves_true  = ti_block.count('Save="true"')
    saves_false = ti_block.count('Save="false"')
    timesteps   = ti_block.count("<TimeStep")
    print(f"\n  Summary: {timesteps} TimeSteps | "
          f"Save=true: {saves_true} | Save=false: {saves_false} | "
          f"No Save attr: {timesteps - saves_true - saves_false}")

    # Check regex will match
    match = re.search(r'<TimeIncrements>.*?</TimeIncrements>',
                      chunk, flags=re.DOTALL)
    if match:
        print(f"  Regex <TimeIncrements>...</TimeIncrements> WILL match")
    else:
        print(f"  WARNING: Regex will NOT match bare <TimeIncrements>!")
        # Check for attributes
        match2 = re.search(r'<TimeIncrements[^>]*>.*?</TimeIncrements>',
                           chunk, flags=re.DOTALL)
        if match2:
            # Show the actual opening tag
            tag_end = match2.group().find('>')
            print(f"  Actual opening tag: {match2.group()[:tag_end+1]}")
            print(f"  -> Tag has ATTRIBUTES - need broader regex")
        else:
            print(f"  Neither regex matches!")

    # Scan for other save-related elements
    for attr in ["SaveIntermediate", "SaveResults", "SaveEvery",
                 "OutputSteps", "SaveNodeResults", "NumSaveSteps",
                 "SaveFreq", "SaveInterval", "AdaptiveTimeStep",
                 "MaxSubIncrements"]:
        if attr in chunk:
            apos = chunk.find(attr)
            snippet = chunk[max(0, apos-30):apos+80].replace('\n', ' ').strip()
            print(f"  Found '{attr}': ...{snippet}...")


def dump_all_analyses(xml_text):
    """List every analysis block - name, kind, save settings."""
    print(f"\n{'='*65}")
    print("All analyses in XML")
    print(f"{'='*65}")

    pos = 0
    while True:
        a_start = xml_text.find("<Analysis>", pos)
        if a_start == -1:
            a_start = xml_text.find("<Analysis ", pos)
        if a_start == -1:
            break
        a_end = xml_text.find("</Analysis>", a_start)
        if a_end == -1:
            break
        a_end += len("</Analysis>")
        block = xml_text[a_start:a_end]

        # Name
        name = "?"
        nm = re.search(r'<n>([^<]+)</n>', block)
        if nm:
            name = nm.group(1)
        else:
            nm2 = re.search(r'<Name>([^<]+)</Name>', block)
            if nm2:
                name = nm2.group(1)

        # Kind
        kind = "?"
        km = re.search(r'<Kind>([^<]+)</Kind>', block)
        if km:
            kind = km.group(1)

        ts_count = block.count("<TimeStep")
        saves    = block.count('Save="true"')
        print(f"  {name} ({kind}) - {ts_count} TimeSteps, {saves} Save=true")

        pos = a_end


# ===================================================================
# PART 2: FIXED XML patching functions
# ===================================================================

def _patch_material_ksat(xml_text, material_name, new_ksat):
    """
    Patch KSat="..." on the <Hydraulic> tag for a material.
    FIXED: searches to </Material> instead of 500-char window.
    """
    marker = f">{material_name}<"
    start = xml_text.find(marker)
    if start == -1:
        print(f"  x FAILED: '{material_name}' not found in XML")
        return xml_text

    # Search to end of material block, not fixed 500 chars
    mat_end = xml_text.find("</Material>", start)
    if mat_end == -1:
        mat_end = start + 2000

    hyd_start = xml_text.find("<Hydraulic", start)
    if hyd_start == -1 or hyd_start > mat_end:
        print(f"  x FAILED: no <Hydraulic> within {material_name} "
              f"(searched {mat_end - start} chars)")
        return xml_text

    hyd_end = xml_text.find("/>", hyd_start) + 2
    hyd_tag = xml_text[hyd_start:hyd_end]

    if 'KSat=' not in hyd_tag:
        print(f"  - SKIP: {material_name} has no KSat (tag: {hyd_tag[:60]})")
        return xml_text

    old_match = re.search(r'KSat="([^"]*)"', hyd_tag)
    old_val = old_match.group(1) if old_match else "?"

    new_tag = re.sub(r'KSat="[^"]*"', f'KSat="{new_ksat:.6g}"', hyd_tag)
    print(f"  OK {material_name}: KSat {old_val} -> {new_ksat:.6g}")
    return xml_text[:hyd_start] + new_tag + xml_text[hyd_end:]


def _patch_material_kyxratio(xml_text, material_name, new_kyx):
    """
    Patch KYXRatio="..." on the <Hydraulic> tag.
    FIXED: searches to </Material> instead of 500-char window.
    """
    marker = f">{material_name}<"
    start = xml_text.find(marker)
    if start == -1:
        print(f"  x FAILED: '{material_name}' not found in XML")
        return xml_text

    mat_end = xml_text.find("</Material>", start)
    if mat_end == -1:
        mat_end = start + 2000

    hyd_start = xml_text.find("<Hydraulic", start)
    if hyd_start == -1 or hyd_start > mat_end:
        print(f"  x FAILED: no <Hydraulic> within {material_name}")
        return xml_text

    hyd_end = xml_text.find("/>", hyd_start) + 2
    hyd_tag = xml_text[hyd_start:hyd_end]

    if 'KYXRatio=' not in hyd_tag:
        print(f"  - SKIP: {material_name} has no KYXRatio")
        return xml_text

    old_match = re.search(r'KYXRatio="([^"]*)"', hyd_tag)
    old_val = old_match.group(1) if old_match else "?"

    new_tag = re.sub(r'KYXRatio="[^"]*"', f'KYXRatio="{new_kyx:.6g}"', hyd_tag)
    print(f"  OK {material_name}: KYXRatio {old_val} -> {new_kyx:.6g}")
    return xml_text[:hyd_start] + new_tag + xml_text[hyd_end:]


def _patch_ksat_in_kfn(xml_text, kfn_name, new_ksat, orig_ksat):
    """
    Scale K-function point Y values and update HydKSat in Estimate.
    Only matters for materials with KFnNum (e.g. Seep AWYC -> HC AWYC).
    Seep WYC/UYC have no KFnNum, so their K-functions are computed from KSat.
    """
    marker = f">{kfn_name}<"
    start = xml_text.find(marker)
    if start == -1:
        print(f"  - SKIP K-fn '{kfn_name}': not found")
        return xml_text

    end = xml_text.find("</KFn>", start)
    if end == -1:
        return xml_text
    end += len("</KFn>")

    block = xml_text[start:end]
    ratio = new_ksat / orig_ksat

    def scale_point(m):
        x_val = m.group(1)
        y_val = float(m.group(2))
        return f'<Point X="{x_val}" Y="{y_val * ratio:.15g}" />'

    new_block = re.sub(r'<Point X="([^"]*)" Y="([^"]*)" />',
                       scale_point, block)

    new_block = re.sub(r'HydKSat=[0-9eE.+\-]+',
                       f'HydKSat={new_ksat:.6g}', new_block)

    n_pts = len(re.findall(r'<Point ', new_block))
    print(f"  OK K-fn '{kfn_name}': {n_pts} points scaled {ratio:.4g}x")
    return xml_text[:start] + new_block + xml_text[end:]


def _patch_rainfall(xml_text, rain_points):
    """Patch rainfall function points."""
    idx = xml_text.find(">rainfall<")
    if idx == -1:
        print(f"  x FAILED: '>rainfall<' not found")
        return xml_text
    chunk_end = xml_text.find("</ClimateFn>", idx)
    chunk = xml_text[idx:chunk_end]
    new_pts = f'<Points Len="{len(rain_points)}">\n'
    for pt in rain_points:
        new_pts += f'            <Point X="{pt["X"]}" Y="{pt["Y"]:.8f}" />\n'
    new_pts += "          </Points>"
    chunk = re.sub(r'<Points.*?</Points>', new_pts, chunk,
                   count=1, flags=re.DOTALL)
    print(f"  OK rainfall: {len(rain_points)} points")
    return xml_text[:idx] + chunk + xml_text[chunk_end:]


def _patch_time_increments(xml_text, rain_points):
    """Patch TimeIncrements with Save='true' on every step.
    Two-stage regex: try bare tag first, then tag-with-attributes."""
    idx = xml_text.find(">Rainfall Simulation<")
    if idx == -1:
        print(f"  x FAILED: analysis marker not found")
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
                   f'ElapsedTime="{start_s + w * week_s}" Save="true" />\n')
    new_ti += "        </TimeSteps>\n      </TimeIncrements>"

    old_chunk = chunk

    # Try 1: bare <TimeIncrements>
    chunk = re.sub(r'<TimeIncrements>.*?</TimeIncrements>',
                   new_ti, chunk, count=1, flags=re.DOTALL)

    if chunk != old_chunk:
        print(f"  OK TimeIncrements: {n_weeks} steps, all Save='true' "
              f"(bare tag match)")
    else:
        # Try 2: <TimeIncrements SomeAttr="...">
        chunk = re.sub(r'<TimeIncrements[^>]*>.*?</TimeIncrements>',
                       new_ti, old_chunk, count=1, flags=re.DOTALL)
        if chunk != old_chunk:
            print(f"  OK TimeIncrements: {n_weeks} steps, all Save='true' "
                  f"(attributed tag match)")
        else:
            print(f"  x FAILED: neither regex matched TimeIncrements!")
            ti_pos = old_chunk.find("<TimeIncrements")
            if ti_pos != -1:
                snippet = old_chunk[ti_pos:ti_pos+150]
                print(f"  Actual tag: {repr(snippet)}")
            else:
                print(f"  No <TimeIncrements found in Rainfall Simulation block")
            return xml_text

    return xml_text[:idx] + chunk + xml_text[chunk_end:]


def _strip_slope_analyses(xml_text):
    """Remove SLOPE/W analysis blocks so GeoCmd only solves SEEP/W."""
    stripped = []
    for slope_name in ["Slope Stability", "FS"]:
        marker = f">{slope_name}<"
        pos = xml_text.find(marker)
        if pos == -1:
            continue
        block_start = xml_text.rfind("<Analysis", 0, pos)
        block_end = xml_text.find("</Analysis>", pos)
        if block_start == -1 or block_end == -1:
            continue
        block_end += len("</Analysis>")
        xml_text = xml_text[:block_start] + xml_text[block_end:]
        stripped.append(slope_name)
    return xml_text, stripped


# ===================================================================
# PART 3: Sensor data + rainfall (minimal)
# ===================================================================

def load_sensor_daily():
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
    return daily.loc[CALIB_START:CALIB_END].dropna(
        subset=["Suction_1.5m", "Suction_3m", "Suction_5m"], how="all")


def build_rainfall(daily):
    mm_to_in = 1.0 / 25.4
    weekly = daily["Precipitation"].resample("W").sum()
    points = []
    t0 = daily.index[0]
    for ts, total_mm in weekly.items():
        elapsed_s = max(int((ts - t0).total_seconds()), 86400)
        points.append({"X": float(elapsed_s), "Y": (total_mm * mm_to_in) / 7.0})
    return points


# ===================================================================
# PART 4: Mesh + PWP reading
# ===================================================================

def read_mesh_ply(gsz_path):
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
        if len(parts) >= 3 and parts[0] == "element" and parts[1] in ("vertex", "node"):
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
        text_lines = content[header_len:].decode('utf-8', errors='replace').splitlines()
        for i in range(n_nodes):
            parts = text_lines[i].split()
            xs.append(float(parts[0]))
            ys.append(float(parts[1]))

    return np.array(xs), np.array(ys)


def find_sensor_nodes(gsz_path):
    sensor_y = {
        "1.5m": SURFACE_Y - 4.92,
        "3.0m": SURFACE_Y - 9.84,
        "5.0m": SURFACE_Y - 16.40,
    }
    arr_x, arr_y = read_mesh_ply(gsz_path)
    print(f"  Mesh: {len(arr_x)} nodes")
    result = {}
    for label, target_y in sensor_y.items():
        dist = np.sqrt((arr_x - CREST_X)**2 + (arr_y - target_y)**2)
        idx = int(np.argmin(dist))
        result[label] = idx
        print(f"  {label}: node_idx={idx}  x={arr_x[idx]:.1f}  "
              f"y={arr_y[idx]:.1f}  dist={dist[idx]:.2f} ft")
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
    """Each time step gets its own folder: 000/, 001/, ..., 016/"""
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
    """Read from Rainfall Simulation/{step:03d}/node.csv"""
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

# ===================================================================
# PART 5: Build work GSZ with all fixes
# ===================================================================

def create_work_gsz(params, rain_points, label):
    """Build a patched work GSZ with all fixes applied."""
    run_dir = os.path.join(DIAG_DIR, label)
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir)

    gsz_name = os.path.basename(BASE_GSZ)
    work_gsz = os.path.join(run_dir, gsz_name)

    with zipfile.ZipFile(BASE_GSZ, 'r') as zin:
        xml_name = [f for f in zin.namelist()
                    if f.endswith('.xml') and '/' not in f][0]
        xml_text = zin.read(xml_name).decode('utf-8', errors='replace')

        # Full diagnostics on first run only
        if label == "run_A":
            dump_time_increments(xml_text, "ORIGINAL (before patches)")
            dump_all_analyses(xml_text)

        print(f"\n{'='*65}")
        print(f"Patching XML ({label})")
        print(f"  Ksat_WYC={params['ksat_wyc']:.3e}  "
              f"KYX={params['kyx_awyc']:.0f}  "
              f"Ksat_UYC={params['ksat_uyc']:.3e}")
        print(f"{'='*65}")

        # 1. Material-level Ksat (the FIXED version)
        xml_text = _patch_material_ksat(xml_text, "Seep WYC",
                                        params["ksat_wyc"])
        xml_text = _patch_material_ksat(xml_text, "Seep UYC",
                                        params["ksat_uyc"])

        # 2. KYXRatio (FIXED version)
        xml_text = _patch_material_kyxratio(xml_text, "Seep AWYC",
                                            params["kyx_awyc"])

        # 3. K-function scaling (only matters for KFnNum-referenced fns)
        xml_text = _patch_ksat_in_kfn(xml_text, "HC WYC",
                                       params["ksat_wyc"], ORIG_KSAT_WYC)
        xml_text = _patch_ksat_in_kfn(xml_text, "HC UYC",
                                       params["ksat_uyc"], ORIG_KSAT_UYC)

        # 4. Rainfall
        xml_text = _patch_rainfall(xml_text, rain_points)

        # 5. Time increments (with fallback regex)
        xml_text = _patch_time_increments(xml_text, rain_points)

        # 6. Strip SLOPE/W
        #xml_text, stripped = _strip_slope_analyses(xml_text)
        stripped = []  # don't touch analysis blocks
        print(f"  Stripped SLOPE/W: {stripped}")

        if label == "run_A":
            dump_time_increments(xml_text, "AFTER all patches")

        # Write work GSZ
        with zipfile.ZipFile(work_gsz, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == xml_name:
                    continue
                if any(item.filename.startswith(pf)
                       for pf in RESULT_PREFIXES):
                    continue
                zout.writestr(item, zin.read(item.filename))
            info = zin.getinfo(xml_name)
            zout.writestr(info, xml_text.encode('utf-8'))

    return work_gsz, run_dir


# ===================================================================
# PART 6: Solve + inspect
# ===================================================================

def solve_and_inspect(solver_exe, work_gsz, sensor_nodes, label):
    """Solve, report timing, list saved steps, read PWP at sensors."""
    print(f"\n{'='*65}")
    print(f"Solving {label} (SEEP/W only)")
    print(f"{'='*65}")

    t0 = time.time()
    result = subprocess.run(
        [solver_exe, "/solve", work_gsz],
        capture_output=True, text=True, timeout=SOLVER_TIMEOUT
    )
    dt = time.time() - t0

    if result.returncode != 0:
        print(f"  SOLVER ERROR (exit {result.returncode}):")
        print(f"  {(result.stderr or result.stdout)[:500]}")
        return None, dt

    print(f"  Solve time: {dt:.1f}s")

    # List node result files
    with zipfile.ZipFile(work_gsz, 'r') as z:
        all_files = sorted(z.namelist())
        node_files = [f for f in all_files
                      if f.startswith(SEEP_FOLDER + "/")
                      and "node" in f.lower()]
        print(f"  Node result files: {len(node_files)}")
        for f in node_files:
            info = z.getinfo(f)
            print(f"    {f}  ({info.file_size} bytes)")

    # Time steps and saved steps
    time_steps = read_time_steps(work_gsz)
    saved = list_saved_steps(work_gsz)
    print(f"  Time steps in time.csv: {len(time_steps)}")
    print(f"  Saved PWP steps: {saved}")

    if not saved:
        print("  ERROR: no saved steps!")
        return None, dt

    # Check +1 offset (Mesh.ply 0-based vs node.csv 1-based)
    max_node = max(sensor_nodes.values())
    test_pwp = read_pore_pressure(work_gsz, saved[-1])
    if test_pwp is not None and max_node < len(test_pwp):
        val0 = test_pwp[max_node] if max_node < len(test_pwp) else np.nan
        val1 = (test_pwp[max_node + 1]
                if (max_node + 1) < len(test_pwp) else np.nan)
        if np.isnan(val0) and not np.isnan(val1):
            print("  Applying +1 offset (0-based -> 1-based)")
            for lbl in sensor_nodes:
                sensor_nodes[lbl] += 1

    # Print PWP at sensor nodes for each saved step
    time_for_step = {ts["step"]: ts["time_s"] for ts in time_steps}
    print(f"\n  {'Step':>4}  {'Days':>8}  "
          f"{'Sim1.5':>8} {'Sim3.0':>8} {'Sim5.0':>8}  (kPa)")
    print("  " + "-" * 52)

    results = {}
    for step_idx in saved:
        if step_idx == 0:
            continue
        pwp = read_pore_pressure(work_gsz, step_idx)
        if pwp is None:
            continue
        time_s = time_for_step.get(step_idx, 0)
        days = time_s / 86400.0
        rel_s = max(0, time_s - 432000)

        vals = {}
        parts = [f"  {step_idx:>4}  {days:>8.1f}"]
        for lbl in ["1.5m", "3.0m", "5.0m"]:
            nidx = sensor_nodes[lbl]
            if nidx < len(pwp) and np.isfinite(pwp[nidx]):
                kpa = pwp[nidx] / KPA_TO_PSF
                vals[lbl] = pwp[nidx]
                parts.append(f"  {kpa:>8.2f}")
            else:
                parts.append(f"  {'N/A':>8}")
        print("".join(parts))
        results[rel_s] = vals

    return results, dt


# ===================================================================
# MAIN
# ===================================================================

def main():
    print("=" * 65)
    print("SEEP/W Calibration Diagnostic")
    print(f"  Base GSZ : {BASE_GSZ}")
    print(f"  Sensor   : {SENSOR_CSV}")
    print("=" * 65)

    for p, lbl in [(BASE_GSZ, "Base GSZ"), (SENSOR_CSV, "Sensor CSV")]:
        if not os.path.exists(p):
            print(f"\nERROR: {lbl} not found: {p}")
            sys.exit(1)

    solver = find_solver()
    if solver is None:
        print("\nERROR: solver not found")
        sys.exit(1)
    print(f"\n  Solver: {solver}")

    # Load sensor data
    print("\nLoading sensor data...")
    daily = load_sensor_daily()
    rain = build_rainfall(daily)
    print(f"  {len(daily)} days, {len(rain)} weekly rainfall points")

    # Find sensor nodes
    print("\nLocating mesh nodes...")
    sensor_nodes = find_sensor_nodes(BASE_GSZ)

    # Clean slate
    if os.path.exists(DIAG_DIR):
        shutil.rmtree(DIAG_DIR)
    os.makedirs(DIAG_DIR)

    # ---- RUN A ----
    gsz_a, dir_a = create_work_gsz(TEST_A, rain, "run_A")
    results_a, dt_a = solve_and_inspect(
        solver, gsz_a, dict(sensor_nodes), "run_A")

    # ---- RUN B (different Ksat) ----
    gsz_b, dir_b = create_work_gsz(TEST_B, rain, "run_B")
    results_b, dt_b = solve_and_inspect(
        solver, gsz_b, dict(sensor_nodes), "run_B")

    # ---- COMPARISON ----
    print(f"\n{'='*65}")
    print("COMPARISON: different Ksat -> different PWP?")
    print(f"{'='*65}")
    print(f"  A: Ksat_WYC={TEST_A['ksat_wyc']:.3e}  "
          f"Ksat_UYC={TEST_A['ksat_uyc']:.3e}  ({dt_a:.0f}s)")
    print(f"  B: Ksat_WYC={TEST_B['ksat_wyc']:.3e}  "
          f"Ksat_UYC={TEST_B['ksat_uyc']:.3e}  ({dt_b:.0f}s)")

    if results_a and results_b:
        common = sorted(set(results_a.keys()) & set(results_b.keys()))
        if common:
            any_diff = False
            for t in common[:8]:
                for lbl in ["1.5m", "3.0m", "5.0m"]:
                    va = results_a[t].get(lbl, float('nan'))
                    vb = results_b[t].get(lbl, float('nan'))
                    if np.isfinite(va) and np.isfinite(vb):
                        diff = abs(va - vb)
                        if diff > 0.01:
                            any_diff = True
                        tag = "  <-- DIFFERENT" if diff > 0.01 else ""
                        print(f"    t={t/86400:.0f}d  {lbl}: "
                              f"A={va/KPA_TO_PSF:>8.2f}  "
                              f"B={vb/KPA_TO_PSF:>8.2f}  "
                              f"diff={diff/KPA_TO_PSF:.2f} kPa{tag}")
            if any_diff:
                print("\n  PASS: Ksat changes produce different PWP")
            else:
                print("\n  FAIL: identical PWP despite different Ksat")
        else:
            print("  No common time steps to compare")
    else:
        print("  One or both runs failed")

    # Summary
    print(f"\n{'='*65}")
    print("SUMMARY")
    print(f"{'='*65}")
    saved_a = list_saved_steps(gsz_a) if results_a else []
    saved_b = list_saved_steps(gsz_b) if results_b else []
    print(f"  Run A: {len(saved_a)} saved steps, {dt_a:.0f}s solve")
    print(f"  Run B: {len(saved_b)} saved steps, {dt_b:.0f}s solve")

    if len(saved_a) > 3:
        print(f"  Save-steps fix: WORKING")
    else:
        print(f"  Save-steps fix: STILL BROKEN - need to inspect XML")

    # Cleanup
    print(f"\nDiag files: {DIAG_DIR}")
    resp = input("Delete? (Y/n): ").strip().lower()
    if resp != 'n':
        shutil.rmtree(DIAG_DIR, ignore_errors=True)
        print("Cleaned up.")


if __name__ == "__main__":
    main()