import sys
sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')
import warnings
warnings.filterwarnings("ignore")
import PyGeoStudio as pgs

FILE = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"
study = pgs.GeoStudioFile(FILE)

for analysis in study.analyses:
    name = analysis.data.get("Name")
    kind = analysis.data.get("Kind")
    if kind != "SLOPE/W":
        continue
    print(f"\n=== {name} ===")
    results = analysis.data.get("Results")
    try:
        vars = results.getOutputVariables()
        print(f"getOutputVariables(): {vars}")
    except Exception as e:
        print(f"getOutputVariables() error: {e}")
    try:
        times = results.getOutputTimes()
        print(f"getOutputTimes(): {times}")
        if times:
            snap = results.getSnapshot(times[0])
            print(f"getSnapshot type: {type(snap)}")
            print(f"getSnapshot attrs: {[a for a in dir(snap) if not a.startswith('_')]}")
            if hasattr(snap, 'data'):
                print(f"getSnapshot.data keys: {list(snap.data.keys())[:10]}")
    except Exception as e:
        print(f"getSnapshot() error: {e}")
    try:
        print(f"f_src: {results.f_src}")
    except Exception as e:
        print(f"f_src error: {e}")