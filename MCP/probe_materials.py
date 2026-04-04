import sys
sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')
import PyGeoStudio as pgs

FILE = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"
study = pgs.GeoStudioFile(FILE)

print("=== MATERIALS ===")
for mat in study.materials:
    d = mat.data
    print(f"\nName: {d.get('Name')}")
    print(f"  SlopeModel: {d.get('SlopeModel')}")
    ss = d.get('StressStrain')
    if ss is not None:
        print(f"  StressStrain type: {type(ss)}")
        if hasattr(ss, 'data'):
            print(f"  StressStrain.data: {ss.data}")
        if hasattr(ss, 'getAllProperties'):
            print(f"  StressStrain.getAllProperties: {ss.getAllProperties()}")