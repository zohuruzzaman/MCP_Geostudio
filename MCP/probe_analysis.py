import sys
sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')
import PyGeoStudio as pgs

study = pgs.GeoStudioFile(r"E:\Github\MCP_Geostudio\Metro-Center.gsz")
for a in study.analyses:
    d = a.data
    print(f"\nName   : {d.get('Name')}")
    print(f"Kind   : {d.get('Kind')}")
    print(f"Parent : {d.get('ParentID') or d.get('Parent') or d.get('ParentName') or 'None'}")
    print(f"PWP    : {d.get('PWPMethod') or d.get('PWP') or d.get('PoreWaterPressure') or 'not found'}")
    print(f"All keys: {list(d.keys())}")