"""Quick probe: dump XML around KSat and KYXRatio tags"""
import zipfile

FILE = r"E:\Github\MCP_Geostudio\calibration\Metro-Center_cal.gsz"

with zipfile.ZipFile(FILE, 'r') as z:
    xml_name = [f for f in z.namelist() if f.endswith('.xml') and '/' not in f][0]
    xml = z.read(xml_name).decode('utf-8', errors='replace')

lines = xml.splitlines()

# Find every line with KSat, KYX, Ksat, ksat, HydCond, Conductivity
targets = ['KSat', 'Ksat', 'ksat', 'KYX', 'HydCond', 'Conductivity', 'KRatio', 'Aniso']
for i, line in enumerate(lines):
    if any(t in line for t in targets):
        start = max(0, i - 5)
        end = min(len(lines), i + 3)
        print(f"--- Line {i} ---")
        for j in range(start, end):
            marker = " >>>" if j == i else "    "
            print(f"{marker} {j:4d}: {lines[j]}")
        print()

# Also find material name tags near these
print("\n=== ALL MATERIAL NAMES ===")
for i, line in enumerate(lines):
    if '<Name>' in line and ('Clay' in line or 'Silt' in line or 'Seep' in line or 'Sand' in line):
        print(f"  {i:4d}: {line.strip()}")