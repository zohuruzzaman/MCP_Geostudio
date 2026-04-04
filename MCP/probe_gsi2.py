import os, json, grpc
from google.protobuf.json_format import MessageToDict
import gsi

def probe_project():
    project = None
    with open("probe_out2.txt", "w") as f:
        try:
            project_path = r"E:\Github\MCP_Geostudio\calibration\Metro-Center_cal.gsz"
            project = gsi.OpenProject(project_path)
            
            # Global Function
            try:
                # Omit analysis to get project-level object
                req = gsi.GetRequest(object='Functions["rainfall"]')
                res = project.Get(req)
                f.write("Functions['rainfall']:\n" + json.dumps(MessageToDict(res.data.struct_value), indent=2) + "\n")
            except Exception as e:
                f.write(f"Error getting Functions['rainfall']: {e}\n")

            # Project-level Material for Seep WYC (KSat, KYXRatio)
            try:
                req = gsi.GetRequest(object='Materials["Weathered Yazoo Clay"]')
                res = project.Get(req)
                f.write("\nMaterial WYC globally:\n" + json.dumps(MessageToDict(res.data.struct_value), indent=2) + "\n")
            except Exception as e:
                f.write(f"Error getting Material: {e}\n")
                
            # Project-level Material for Seep AWYC
            try:
                req = gsi.GetRequest(object='Materials["Weathered Yazoo Clay in Active Zone"]')
                res = project.Get(req)
                f.write("\nMaterial AWYC globally:\n" + json.dumps(MessageToDict(res.data.struct_value), indent=2) + "\n")
            except Exception as e:
                pass

        except Exception as e:
            f.write(f"Fatal error: {e}\n")
        finally:
            if project:
                project.Close()

if __name__ == '__main__':
    probe_project()
