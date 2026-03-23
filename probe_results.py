import sys
sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')
import PyGeoStudio as pgs

FILE = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"
study = pgs.GeoStudioFile(FILE)

for analysis in study.analyses:
    name = analysis.data.get("Name")
    kind = analysis.data.get("Kind")
    if kind != "SLOPE/W":
        continue
    print(f"\n=== {name} ({kind}) ===")
    results = analysis.data.get("Results")
    print(f"Results type: {type(results)}")
    print(f"Results attrs: {[a for a in dir(results) if not a.startswith('_')]}")
    if hasattr(results, 'data'):
        print(f"Results.data keys: {list(results.data.keys()) if results.data else 'empty'}")