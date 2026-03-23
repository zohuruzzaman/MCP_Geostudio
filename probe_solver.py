import sys, zipfile, warnings, os, glob
warnings.filterwarnings("ignore")

FILE = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"

print("=== slip_surface.csv (first 5 lines) ===")
with zipfile.ZipFile(FILE, 'r') as z:
    with z.open("FS/001/slip_surface.csv") as f:
        for i, line in enumerate(f):
            print(line.decode('utf-8', errors='replace').strip())
            if i >= 6:
                break

print("\n=== lambdafos csv ===")
with zipfile.ZipFile(FILE, 'r') as z:
    with z.open("FS/001/lambdafos_383.csv") as f:
        for i, line in enumerate(f):
            print(line.decode('utf-8', errors='replace').strip())
            if i >= 6:
                break

print("\n=== Looking for GeoStudio solver executable ===")
search_paths = [
    r"C:\Program Files\Seequent\GeoStudio 2024\**\*.exe",
    r"C:\Program Files\GeoStudio*\**\*.exe",
    r"C:\Program Files (x86)\Seequent\**\*.exe",
]
for pattern in search_paths:
    hits = glob.glob(pattern, recursive=True)
    for h in hits:
        if any(k in h.lower() for k in ['solve', 'geostudio', 'slope', 'gssolv']):
            print(f"  {h}")

print("\n=== Seequent AppData ===")
appdata = os.environ.get("APPDATA", "")
local = os.environ.get("LOCALAPPDATA", "")
for base in [appdata, local]:
    p = os.path.join(base, "Seequent")
    if os.path.exists(p):
        for root, dirs, files in os.walk(p):
            for f in files:
                if f.endswith('.exe'):
                    print(os.path.join(root, f))