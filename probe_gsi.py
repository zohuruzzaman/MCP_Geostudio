import os
import sys
import grpc
from google.protobuf.json_format import MessageToDict
import gsi
import json

def probe_project():
    project = None
    with open("probe_out.txt", "w") as f:
        try:
            project_path = r"E:\Github\MCP_Geostudio\calibration\Metro-Center_cal.gsz"
            f.write(f"Opening {project_path} ...\n")
            project = gsi.OpenProject(project_path)
            
            # 1. Probe TimeIncrements
            analysis_name = "Rainfall Simulation"
            f.write(f"\n--- Checking TimeIncrements for {analysis_name} ---\n")
            try:
                req = gsi.GetRequest(analysis=analysis_name, object='CurrentAnalysis.TimeIncrements')
                res = project.Get(req)
                f.write("TimeIncrements:\n" + json.dumps(MessageToDict(res.data.struct_value), indent=2) + "\n")
            except Exception as e:
                f.write(f"Error getting TimeIncrements: {e}\n")

            # 2. Probe Rainfall boundary condition
            f.write("\n--- Checking Rainfall Boundary Function ---\n")
            try:
                # Based on gsi_all_types, Functions property of project. 
                # Let's try Functions["rainfall"]
                req = gsi.GetRequest(analysis=analysis_name, object='Functions["rainfall"]')
                res = project.Get(req)
                f.write("Rainfall Function:\n" + json.dumps(MessageToDict(res.data.struct_value), indent=2) + "\n")
            except Exception as e:
                f.write(f"Error getting Rainfall Function: {e}\n")

            # 3. Probe Material
            f.write("\n--- Checking Material ---\n")
            mat_name = "Weathered Yazoo Clay in Active Zone"
            try:
                req = gsi.GetRequest(analysis=analysis_name, object=f'Materials["{mat_name}"]')
                res = project.Get(req)
                f.write(f"Material {mat_name}:\n" + json.dumps(MessageToDict(res.data.struct_value), indent=2) + "\n")
            except Exception as e:
                f.write(f"Error getting Material: {e}\n")

        except Exception as e:
            f.write(f"Fatal error: {e}\n")
        finally:
            if project:
                project.Close()

if __name__ == '__main__':
    probe_project()
