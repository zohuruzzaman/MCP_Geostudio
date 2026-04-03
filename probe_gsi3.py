import os, json
import grpc
from google.protobuf.json_format import MessageToDict
import gsi

def probe_functions():
    project = None
    with open("probe_out3.txt", "w") as f:
        try:
            project_path = r"E:\Github\MCP_Geostudio\calibration\Metro-Center_cal.gsz"
            project = gsi.OpenProject(project_path)
            
            # List all functions in the first analysis to see where rainfall lives
            try:
                # Based on the API, maybe 'Functions' or 'CurrentAnalysis.Functions' or 'Project.Functions'
                req = gsi.GetRequest(analysis="Rainfall Simulation", object='Functions')
                res = project.Get(req)
                f.write("Analysis Functions:\n" + json.dumps(MessageToDict(res.data.struct_value), indent=2) + "\n")
            except Exception as e:
                f.write(f"Error getting Analysis Functions: {e}\n")

            try:
                # Also check globally but we saw earlier that analysis= is required.
                # Let's try getting all objects?
                req = gsi.GetRequest(analysis="Rainfall Simulation", object='BoundaryConditions')
                res = project.Get(req)
                f.write("\nBoundaryConditions:\n" + json.dumps(MessageToDict(res.data.struct_value), indent=2) + "\n")
            except Exception as e:
                f.write(f"Error getting BoundaryConditions: {e}\n")

        except Exception as e:
            f.write(f"Fatal error: {e}\n")
        finally:
            if project:
                project.Close()

if __name__ == '__main__':
    probe_functions()
