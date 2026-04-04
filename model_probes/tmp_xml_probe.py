import zipfile, xml.etree.ElementTree as ET
with zipfile.ZipFile(r'E:\Github\MCP_Geostudio\calibration\Metro-Center_cal.gsz') as z:
    xml_file = [f for f in z.namelist() if f.endswith('.xml') and '/' not in f][0]
    root = ET.fromstring(z.read(xml_file))
    print("--- ClimateFns ---")
    for fn in root.findall('.//ClimateFn'):
        print(ET.tostring(fn).decode('utf-8')[:500])
        print("==========")
    print("--- TimeIncrements ---")
    for fn in root.findall('.//TimeIncrements'):
        print(ET.tostring(fn).decode('utf-8')[:500])
        print("==========")
