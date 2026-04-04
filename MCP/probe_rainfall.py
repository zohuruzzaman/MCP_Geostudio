import sys, zipfile, warnings
warnings.filterwarnings("ignore")

FILE = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"

print("=== imported_water.rxml (rainfall BC) ===")
with zipfile.ZipFile(FILE, 'r') as z:
    with z.open("FS/imported_water.rxml") as f:
        content = f.read().decode('utf-8', errors='replace')
        lines = content.splitlines()
        # Print first 60 lines to understand structure
        for i, line in enumerate(lines[:60]):
            print(f"{i:4d}: {line}")

print("\n=== imported_water_time.csv ===")
with zipfile.ZipFile(FILE, 'r') as z:
    with z.open("FS/imported_water_time.csv") as f:
        print(f.read().decode('utf-8', errors='replace'))

print("\n=== Rainfall Simulation/time.csv ===")
with zipfile.ZipFile(FILE, 'r') as z:
    with z.open("Rainfall Simulation/time.csv") as f:
        print(f.read().decode('utf-8', errors='replace'))

print("\n=== Main XML - rainfall/flux boundary sections ===")
with zipfile.ZipFile(FILE, 'r') as z:
    with z.open("Metro-Center.xml") as f:
        content = f.read().decode('utf-8', errors='replace')
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if any(k in line.lower() for k in ['rainfall', 'flux', 'precip', 'boundary', 'unitflux', 'rainflux', 'q=', 'infiltr', 'waterbc']):
                # print surrounding context
                start = max(0, i-1)
                end = min(len(lines), i+3)
                for j in range(start, end):
                    print(f"{j:4d}: {lines[j]}")
                print("---")