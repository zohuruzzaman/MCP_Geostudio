import os, json
import gsi
from google.protobuf.json_format import MessageToDict

def find_path(obj, target_name, current_path="Functions"):
    if isinstance(obj, dict):
        if obj.get("Name") == target_name or obj.get("name") == target_name:
            print(f"FOUND EXACT PATH: {current_path}")
            return current_path
        for k, v in obj.items():
            res = find_path(v, target_name, f"{current_path}.{k}")
            if res: return res
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            res = find_path(item, target_name, f"{current_path}[{i}]")
            if res: return res
    return None

def main():
    gsz = r"E:\Github\MCP_Geostudio\calibration\Metro-Center_cal.gsz"
    project = gsi.OpenProject(gsz)
    try:
        req = gsi.GetRequest(analysis="Rainfall Simulation", object="Functions")
        res = project.Get(req)
        d = MessageToDict(res.data.struct_value)
        find_path(d, "rainfall")
        
        # Also find materials Ksat
        req = gsi.GetRequest(analysis="Rainfall Simulation", object="Materials")
        res = project.Get(req)
        d2 = MessageToDict(res.data.struct_value)
        with open("materials_dump.txt", "w") as f:
            f.write(json.dumps(d2, indent=2))
        print("Done digging.")
    finally:
        project.Close()

if __name__ == "__main__":
    main()
