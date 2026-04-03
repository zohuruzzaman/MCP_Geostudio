"""
Quick probe to fix two issues:
  1. Find actual SEEP/W mesh nodes (not geometry points)
  2. Check QueryResultsAvailability attribute names
  3. Test pore pressure extraction at discovered nodes

Run this, paste the output back.
"""
import sys, os, zipfile, csv, warnings
import numpy as np

warnings.filterwarnings("ignore")

try:
    import gsi
    from google.protobuf.json_format import MessageToDict
    GSI_OK = True
except ImportError:
    GSI_OK = False

FILE = r"E:\Github\MCP_Geostudio\calibration\Metro-Center_cal.gsz"

CREST_X    = 195.0
SURFACE_Y  = 83.0
TARGETS = {
    "1.5m": SURFACE_Y - 4.92,    # ~78.08 ft
    "3.0m": SURFACE_Y - 9.84,    # ~73.16 ft
    "5.0m": SURFACE_Y - 16.40,   # ~66.60 ft
}

# ---------------------------------------------------------------
# 1. Find mesh nodes from solved archive (CSV node files)
# ---------------------------------------------------------------
print("=" * 60)
print("PART 1: Finding mesh nodes from solved GSZ archive")
print("=" * 60)

with zipfile.ZipFile(FILE, 'r') as z:
    all_files = z.namelist()

    # List relevant files in Rainfall Simulation folder
    rs_files = [f for f in all_files if f.startswith("Rainfall Simulation/")]
    print(f"\nFiles in 'Rainfall Simulation/' folder: {len(rs_files)}")
    for f in sorted(rs_files)[:30]:
        info = z.getinfo(f)
        print(f"  {f}  ({info.file_size} bytes)")
    if len(rs_files) > 30:
        print(f"  ... and {len(rs_files) - 30} more")

    # Look for node coordinate files
    node_files = [f for f in all_files
                  if any(k in f.lower() for k in ['node', 'mesh', 'coord'])
                  and f.endswith('.csv')]
    print(f"\nCSV files with 'node/mesh/coord' in name:")
    for f in node_files[:10]:
        print(f"  {f}")

    # Also check for any CSV in the Rainfall Simulation results
    rs_csvs = [f for f in rs_files if f.endswith('.csv')]
    print(f"\nAll CSVs in Rainfall Simulation/:")
    for f in sorted(rs_csvs)[:20]:
        # Print header
        with z.open(f) as csvf:
            header = csvf.readline().decode('utf-8', errors='replace').strip()
        print(f"  {f}: {header[:100]}")

    # Try reading the root XML MeshNodes more carefully
    print(f"\nSearching ALL XML files for mesh node data...")
    import xml.etree.ElementTree as ET

    for xml_file in [f for f in all_files if f.endswith('.xml')]:
        try:
            xml_bytes = z.read(xml_file)
            root = ET.fromstring(xml_bytes.decode('utf-8', errors='replace'))

            # Count nodes in various tags
            for tag in ["MeshNodes", "Nodes", "MeshNode", "Node"]:
                containers = root.findall(f".//{tag}")
                for container in containers:
                    children = list(container)
                    if len(children) > 10:
                        print(f"  {xml_file}: <{tag}> with {len(children)} children")
                        # Print first few
                        for c in children[:3]:
                            print(f"    {ET.tostring(c, encoding='unicode').strip()}")
        except Exception:
            pass


# ---------------------------------------------------------------
# 2. Probe GSI QueryResultsAvailability
# ---------------------------------------------------------------
if GSI_OK:
    print(f"\n{'=' * 60}")
    print("PART 2: Probing GSI QueryResultsAvailability")
    print("=" * 60)

    project = None
    try:
        project = gsi.OpenProject(FILE)
        project.LoadResults(gsi.LoadResultsRequest(analysis="Rainfall Simulation"))

        avail = project.QueryResultsAvailability(
            gsi.QueryResultsAvailabilityRequest(analysis="Rainfall Simulation"))

        print(f"\n  Response type: {type(avail)}")
        print(f"  Response dir (non-private):")
        for attr in dir(avail):
            if not attr.startswith('_'):
                try:
                    val = getattr(avail, attr)
                    if not callable(val):
                        print(f"    .{attr} = {val}")
                except Exception as e:
                    print(f"    .{attr} -> ERROR: {e}")

        # Try common attribute names
        for attr_name in ['available_steps', 'availableSteps', 'steps',
                          'AvailableSteps', 'step_numbers', 'time_steps']:
            try:
                val = getattr(avail, attr_name)
                print(f"\n  SUCCESS: avail.{attr_name} = {list(val)[:10]}")
            except AttributeError:
                pass

    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if project:
            project.Close()

    # ---------------------------------------------------------------
    # 3. Try to query node coordinates via GSI
    # ---------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print("PART 3: Query node XY via GSI results")
    print("=" * 60)

    project = None
    try:
        project = gsi.OpenProject(FILE)
        project.LoadResults(gsi.LoadResultsRequest(analysis="Rainfall Simulation"))

        # Try querying node X and Y coordinates from results
        # This gets the ACTUAL mesh node positions used in the solve
        for step_try in [0, 1]:
            try:
                coord_req = gsi.QueryResultsRequest(
                    analysis="Rainfall Simulation", step=step_try,
                    table=gsi.ResultType.Node,
                    dataparams=[gsi.DataParamType.eXCoord, gsi.DataParamType.eYCoord],
                )
                coord_res = project.QueryResults(coord_req)

                x_entry = coord_res.results.get(gsi.DataParamType.eXCoord)
                y_entry = coord_res.results.get(gsi.DataParamType.eYCoord)

                if x_entry and y_entry:
                    nx = list(x_entry.values)
                    ny = list(y_entry.values)
                    print(f"\n  Step {step_try}: Got {len(nx)} node X coords, {len(ny)} node Y coords")
                    print(f"  X range: {min(nx):.2f} to {max(nx):.2f} ft")
                    print(f"  Y range: {min(ny):.2f} to {max(ny):.2f} ft")

                    # Find nearest nodes to sensors
                    arr_x = np.array(nx)
                    arr_y = np.array(ny)
                    for label, target_y in TARGETS.items():
                        dist = np.sqrt((arr_x - CREST_X)**2 + (arr_y - target_y)**2)
                        idx = int(np.argmin(dist))
                        print(f"  Sensor {label}: node_idx={idx}  "
                              f"x={arr_x[idx]:.2f}  y={arr_y[idx]:.2f}  "
                              f"dist={dist[idx]:.2f} ft")

                    # Also try extracting pore pressure at these nodes
                    print(f"\n  Testing pore pressure extraction at step {step_try}...")
                    pwp_req = gsi.QueryResultsRequest(
                        analysis="Rainfall Simulation", step=step_try,
                        table=gsi.ResultType.Node,
                        dataparams=[gsi.DataParamType.eWaterPressure],
                    )
                    pwp_res = project.QueryResults(pwp_req)
                    pwp_entry = pwp_res.results.get(gsi.DataParamType.eWaterPressure)
                    if pwp_entry:
                        pwp_vals = list(pwp_entry.values)
                        print(f"  Got {len(pwp_vals)} pore pressure values")
                        print(f"  PWP range: {min(pwp_vals):.1f} to {max(pwp_vals):.1f} psf")
                        for label, target_y in TARGETS.items():
                            dist = np.sqrt((arr_x - CREST_X)**2 + (arr_y - target_y)**2)
                            idx = int(np.argmin(dist))
                            kpa = pwp_vals[idx] / 20.8854
                            print(f"    {label}: PWP = {pwp_vals[idx]:.1f} psf = {kpa:.2f} kPa")
                    break  # got what we need
                else:
                    print(f"  Step {step_try}: No X/Y coordinate data returned")
            except Exception as e:
                print(f"  Step {step_try}: {e}")

    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if project:
            project.Close()

else:
    print("\nGSI not available - skipping parts 2 and 3")

print("\n" + "=" * 60)
print("DONE — paste this output back")
print("=" * 60)
