"""
Probe script - extracts:
1. Geometry points (regions/zones) to find crest/middle/toe x-coordinates
2. XML structure of Seep material hydraulic parameters (Ksat, SWCC)
3. SEEP/W mesh node locations if available
"""
import sys, zipfile, json
sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')
import PyGeoStudio as pgs

FILE = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"

# ---------------------------------------------------------------------------
# 1. Geometry - find points/regions to identify crest, middle, toe x-coords
# ---------------------------------------------------------------------------
print("=== GEOMETRY ===")
study = pgs.GeoStudioFile(FILE)
for analysis in study.analyses:
    name = analysis.data.get("Name")
    kind = analysis.data.get("Kind")
    geom = analysis.data.get("Geometry")
    if geom is None:
        continue
    print(f"\nAnalysis: {name} ({kind})")
    if hasattr(geom, 'data'):
        keys = list(geom.data.keys()) if geom.data else []
        print(f"  Geometry keys: {keys}")
        # Look for points, regions, lines
        for k in ["Points", "Regions", "Lines", "SoilLayers", "Nodes"]:
            if k in keys:
                val = geom.data[k]
                print(f"  {k} type: {type(val)}")
                if hasattr(val, 'data'):
                    print(f"  {k} data (first 500 chars): {str(val.data)[:500]}")
    break  # just first analysis for geometry

# ---------------------------------------------------------------------------
# 2. XML - extract Seep material hydraulic params structure
# ---------------------------------------------------------------------------
print("\n\n=== SEEP MATERIAL XML STRUCTURE ===")
gsz_stem = "Metro-Center"
with zipfile.ZipFile(FILE, 'r') as z:
    xml_candidates = [f for f in z.namelist()
                      if f.endswith(gsz_stem + ".xml")
                      and "/" not in f]
    if not xml_candidates:
        # try any root xml
        xml_candidates = [f for f in z.namelist()
                          if f.endswith(".xml") and "/" not in f]
    xml_str = z.read(xml_candidates[0]).decode("utf-8")

lines = xml_str.splitlines()

# Find Seep material blocks
seep_targets = ["Seep SC", "Seep AWYC", "Seep WYC", "Seep UYC"]
for target in seep_targets[:2]:  # just first two to keep output short
    print(f"\n--- {target} ---")
    in_mat = False
    depth = 0
    for i, line in enumerate(lines):
        if target in line:
            in_mat = True
            start = max(0, i-2)
        if in_mat:
            print(f"  {i:4d}: {line}")
            if any(k in line for k in ["Ksat", "Ks ", "HydCond", "SWCC", "VWC",
                                        "WaterContent", "MatricSuction",
                                        "HydraulicConductivity", "VolumetricWater"]):
                pass  # already printing
            # stop after 60 lines or closing material tag
            if in_mat and i > start + 60:
                print("  ... (truncated)")
                break

# ---------------------------------------------------------------------------
# 3. Find geometry x-range to estimate crest/mid/toe coordinates
# ---------------------------------------------------------------------------
print("\n\n=== GEOMETRY POINT COORDINATES (from XML) ===")
in_points = False
point_count = 0
for i, line in enumerate(lines):
    ls = line.strip()
    if "<Points" in ls and "Len" in ls:
        in_points = True
    if in_points and ls.startswith("<Point ") and point_count < 50:
        print(f"  {ls}")
        point_count += 1
    if in_points and "</Points>" in ls:
        in_points = False
        if point_count > 0:
            break  # got first points block

with zipfile.ZipFile(FILE, 'r') as z:
    xml_candidates = [f for f in z.namelist() if f.endswith(".xml") and "/" not in f]
    xml_str = z.read(xml_candidates[0]).decode("utf-8")

lines = xml_str.splitlines()
in_fn = False
for i, line in enumerate(lines):
    if any(k in line for k in ["<HydCondFn", "<VolWCFn", "<KFn", "FnNum", "<Fn ", "<Function"]):
        in_fn = True
    if in_fn:
        print(f"{i:4d}: {line}")
    if in_fn and i > 0 and "</Fn" in line:
        in_fn = False
        print("---")

print(f"\nTotal points printed: {point_count}")
