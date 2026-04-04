import zipfile, sys, os, warnings
warnings.filterwarnings("ignore")

FILE = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"

with zipfile.ZipFile(FILE, 'r') as z:
    xml_str = z.read("Metro-Center.xml").decode("utf-8")

print("=== Searching for rainfall tag ===")
for i, line in enumerate(xml_str.splitlines()):
    ls = line.strip()
    low = ls.lower()
    if "rainfall" in low:
        print(f"  Line {i}: {repr(ls)}")
        print(f"    bytes: {ls.encode().hex()[:60]}")
        if "<" in ls and ">" in ls:
            # check each tag variant
            print(f"    '<n>rainfall</n>' in low: {'<n>rainfall</n>' in low}")
            print(f"    '<n>rainfall</n>' in low: {'<n>rainfall</n>' in low}")
            print(f"    '<n>rainfall</n>' in ls : {'<n>rainfall</n>' in ls}")