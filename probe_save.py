import sys, warnings, os, shutil
warnings.filterwarnings("ignore")
sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')
import PyGeoStudio as pgs

FILE     = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"
TEMP_DIR = r"E:\Github\MCP_Geostudio\mc_probe_test"
TEMP_GSZ = os.path.join(TEMP_DIR, "mc_probe.gsz")

# Clean up from any previous test
if os.path.exists(TEMP_DIR):
    shutil.rmtree(TEMP_DIR)
os.makedirs(TEMP_DIR)

# Copy to isolated folder with new name
shutil.copy2(FILE, TEMP_GSZ)
print(f"Copied to: {TEMP_GSZ}")

# Open with PyGeoStudio
study = pgs.GeoStudioFile(TEMP_GSZ)

# Read current value
for mat in study.materials:
    name = mat.data.get("Name")
    ss   = mat.data.get("StressStrain")
    if ss and ss.data.get("CohesionPrime"):
        print(f"\nBefore - {name}: CohesionPrime = {ss.data['CohesionPrime']}")

        # Modify
        original = ss.data["CohesionPrime"]
        ss.data["CohesionPrime"] = "999.0"
        print(f"After  - {name}: CohesionPrime = {ss.data['CohesionPrime']}")
        break

# Check what save() signature looks like
print(f"\nsave() method: {pgs.GeoStudioFile.save}")
import inspect
try:
    print(inspect.signature(study.save))
except:
    pass

# Try saving
try:
    study.save(TEMP_GSZ)
    print("save() succeeded")
except Exception as e:
    print(f"save() failed: {e}")
    try:
        study.save()
        print("save() with no args succeeded")
    except Exception as e2:
        print(f"save() no args also failed: {e2}")

# Re-read to verify change persisted
import zipfile
with zipfile.ZipFile(TEMP_GSZ, 'r') as z:
    xml = z.read("mc_probe.xml").decode("utf-8") if "mc_probe.xml" in z.namelist() else None
    if xml is None:
        # find any xml
        xmls = [f for f in z.namelist() if f.endswith(".xml") and "/" not in f]
        if xmls:
            xml = z.read(xmls[0]).decode("utf-8")
            print(f"\nRoot XML file: {xmls[0]}")

if xml:
    if "999.0" in xml:
        print("SUCCESS - change persisted in saved file")
    else:
        print("FAIL - change did NOT persist in saved file")

# Cleanup
shutil.rmtree(TEMP_DIR)
print("\nDone - temp folder cleaned up")