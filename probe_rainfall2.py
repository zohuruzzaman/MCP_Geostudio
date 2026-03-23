import sys, zipfile, warnings, re
warnings.filterwarnings("ignore")

FILE = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"

with zipfile.ZipFile(FILE, 'r') as z:
    with z.open("Metro-Center.xml") as f:
        content = f.read().decode('utf-8', errors='replace')

lines = content.splitlines()

# Find the rainfall function block and print it fully
in_rainfall = False
brace_depth = 0
for i, line in enumerate(lines):
    if '<Name>rainfall</Name>' in line:
        in_rainfall = True
        start = max(0, i-2)
        print(f"=== Rainfall function block (starting line {start}) ===")
    if in_rainfall:
        print(f"{i:4d}: {line}")
        if '</ClimateFn>' in line:
            break

print("\n=== All ClimateFn names and point counts ===")
for i, line in enumerate(lines):
    if '<Name>' in line and i > 210 and i < 310:
        print(f"{i:4d}: {line.strip()}")
    if '<Points Len=' in line and i > 210 and i < 310:
        print(f"{i:4d}: {line.strip()}")
    if '<Point ' in line and i > 210 and i < 310:
        print(f"{i:4d}: {line.strip()}")