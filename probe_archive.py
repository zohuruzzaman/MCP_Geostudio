import sys, zipfile, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, r'C:\Users\Zaman\AppData\Roaming\Python\Python314\site-packages')

FILE = r"E:\Github\MCP_Geostudio\Metro-Center.gsz"

print("=== FILES INSIDE .gsz ARCHIVE ===")
with zipfile.ZipFile(FILE, 'r') as z:
    for name in sorted(z.namelist()):
        info = z.getinfo(name)
        print(f"  {name}  ({info.file_size} bytes)")

print("\n=== READING Metro-Center.xml (main config) ===")
with zipfile.ZipFile(FILE, 'r') as z:
    xml_files = [n for n in z.namelist() if n.endswith('.xml')]
    for xf in xml_files[:1]:
        content = z.read(xf).decode('utf-8', errors='replace')
        # Look for FS-related tags
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if any(k in line for k in ['CriticalFS', 'FactorOfSafety', 'FOS', 'SlipSurface', 'MinFS']):
                print(f"  Line {i}: {line.strip()}")