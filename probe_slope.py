"""
Probe: SLOPE/W strength parameters in XML + FS/slip geometry from archive
==========================================================================
Run from: E:\\Github\\MCP_Geostudio\\
Usage:    python probe_slope_xml.py
"""
import zipfile, csv, io, re

FILE = r"E:\Github\MCP_Geostudio\calibration\Metro-Center_cal.gsz"

with zipfile.ZipFile(FILE, 'r') as z:
    xml_name = [f for f in z.namelist() if f.endswith('.xml') and '/' not in f][0]
    xml = z.read(xml_name).decode('utf-8', errors='replace')
    all_files = sorted(z.namelist())

lines = xml.splitlines()

# ===================================================================
# PART 1: Strength parameters — CohesionPrime, PhiPrime
# ===================================================================
print("=" * 65)
print("PART 1: Strength parameters in XML")
print("=" * 65)

targets = ['CohesionPrime', 'PhiPrime', 'Cohesion', 'FrictionAngle',
           'PhiB', 'UnsaturatedShear', 'StressStrain']
for i, line in enumerate(lines):
    if any(t in line for t in targets):
        start = max(0, i - 8)
        end = min(len(lines), i + 3)
        print(f"\n--- Line {i} ---")
        for j in range(start, end):
            marker = " >>>" if j == i else "    "
            print(f"{marker} {j:4d}: {lines[j]}")

# ===================================================================
# PART 2: Full material blocks for the two target materials
# ===================================================================
print(f"\n\n{'=' * 65}")
print("PART 2: Full material blocks for AWYC and WYC")
print("=" * 65)

for mat_name in ["Weathered Yazoo Clay in Active Zone",
                 "Weathered Yazoo Clay"]:
    marker = f">{mat_name}<"
    pos = xml.find(marker)
    if pos == -1:
        print(f"\n  '{mat_name}' NOT FOUND")
        continue

    # Find enclosing <Material>...</Material>
    mat_start = xml.rfind("<Material", 0, pos)
    mat_end = xml.find("</Material>", pos)
    if mat_start == -1 or mat_end == -1:
        print(f"\n  Could not find <Material> boundaries for '{mat_name}'")
        continue

    block = xml[mat_start:mat_end + len("</Material>")]
    print(f"\n--- {mat_name} ({len(block)} chars) ---")
    for line in block.splitlines():
        print(f"  {line}")

# ===================================================================
# PART 3: FS result files in archive
# ===================================================================
print(f"\n\n{'=' * 65}")
print("PART 3: FS and Slope Stability result files")
print("=" * 65)

for prefix in ["FS/", "Slope Stability/"]:
    files = [f for f in all_files if f.startswith(prefix)]
    if files:
        print(f"\n  {prefix} ({len(files)} files)")
        for f in files[:30]:
            with zipfile.ZipFile(FILE, 'r') as z:
                info = z.getinfo(f)
            print(f"    {f}  ({info.file_size} bytes)")
        if len(files) > 30:
            print(f"    ... and {len(files) - 30} more")

# ===================================================================
# PART 4: Read lambdafos CSV (FS values)
# ===================================================================
print(f"\n\n{'=' * 65}")
print("PART 4: lambdafos CSV (Factor of Safety)")
print("=" * 65)

with zipfile.ZipFile(FILE, 'r') as z:
    lf_files = [f for f in all_files
                if "lambdafos" in f.lower() and f.endswith(".csv")]
    if not lf_files:
        print("  No lambdafos CSV found")
    else:
        for lf in lf_files[:4]:
            print(f"\n  {lf}:")
            content = z.read(lf).decode('utf-8', errors='replace')
            csv_lines = content.splitlines()
            # Print header + first 5 rows
            for k, line in enumerate(csv_lines[:6]):
                print(f"    {line}")
            if len(csv_lines) > 6:
                print(f"    ... ({len(csv_lines)-1} total rows)")

            # Extract min FS
            reader = csv.DictReader(io.StringIO(content))
            best_fs = None
            for row in reader:
                try:
                    ff = float(row.get("FOSByForce", 9999))
                    fm = float(row.get("FOSByMoment", 9999))
                    fs = (ff + fm) / 2.0
                    if best_fs is None or fs < best_fs:
                        best_fs = fs
                except (ValueError, TypeError):
                    pass
            if best_fs:
                print(f"    → Critical FS = {best_fs:.4f}")

# ===================================================================
# PART 5: Read slip_surface CSV (geometry)
# ===================================================================
print(f"\n\n{'=' * 65}")
print("PART 5: slip_surface CSV (geometry)")
print("=" * 65)

with zipfile.ZipFile(FILE, 'r') as z:
    ss_files = [f for f in all_files
                if "slip_surface" in f.lower() and f.endswith(".csv")]
    if not ss_files:
        print("  No slip_surface CSV found")
    else:
        for sf in ss_files[:4]:
            print(f"\n  {sf}:")
            content = z.read(sf).decode('utf-8', errors='replace')
            csv_lines = content.splitlines()
            for k, line in enumerate(csv_lines[:10]):
                print(f"    {line}")
            if len(csv_lines) > 10:
                print(f"    ... ({len(csv_lines)-1} total rows)")

# ===================================================================
# PART 6: Check if Silty Clay and Unweathered also have strength
# ===================================================================
print(f"\n\n{'=' * 65}")
print("PART 6: All materials with strength parameters")
print("=" * 65)

pos = 0
while True:
    mat_start = xml.find("<Material>", pos)
    if mat_start == -1:
        mat_start = xml.find("<Material ", pos)
    if mat_start == -1:
        break
    mat_end = xml.find("</Material>", mat_start)
    if mat_end == -1:
        break
    mat_end += len("</Material>")
    block = xml[mat_start:mat_end]

    # Extract name
    nm = re.search(r'>([^<]+)<', block[block.find("<Name"):] if "<Name" in block else block[block.find("<n"):])
    name = nm.group(1) if nm else "?"

    has_c   = "CohesionPrime" in block
    has_phi = "PhiPrime" in block
    has_ss  = "StressStrain" in block

    if has_ss:
        # Extract values
        c_match   = re.search(r'CohesionPrime["\s>=]+([0-9eE.+\-]+)', block)
        phi_match = re.search(r'PhiPrime["\s>=]+([0-9eE.+\-]+)', block)
        c_val   = c_match.group(1) if c_match else "?"
        phi_val = phi_match.group(1) if phi_match else "?"
        print(f"  {name}: c'={c_val}  phi'={phi_val}")

    pos = mat_end

print(f"\n{'=' * 65}")
print("DONE")
print("=" * 65)