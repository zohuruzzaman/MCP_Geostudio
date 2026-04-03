"""
GeoStudio MCP Server
====================
Exposes GeoStudio 2025.2+ capabilities as MCP tools using the official
gsi scripting API (gRPC-based).

Requirements
------------
- Python 3.12.x  (>=3.12.1, <3.13.0 — enforced by gsi wheel)
- gsi package installed:
    pip install -r "C:\\Program Files\\Seequent\\GeoStudio 2025.2\\API\\requirements.txt"
- GeoStudio 2025.2 background service running (starts automatically with GeoStudio)

.mcp.json example
-----------------
{
  "mcpServers": {
    "geostudio": {
      "command": "C:\\\\Python312\\\\python.exe",
      "args": ["E:\\\\Github\\\\MCP_Geostudio\\\\server.py"]
    }
  }
}
"""

import os
import json
import asyncio
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ---------------------------------------------------------------------------
# gsi import — clear error if not installed / wrong Python version
# ---------------------------------------------------------------------------
try:
    import grpc
    from google.protobuf.json_format import MessageToDict
    import gsi
    _GSI_AVAILABLE = True
    _GSI_ERROR     = None
except ImportError as _e:
    _GSI_AVAILABLE = False
    _GSI_ERROR     = (
        f"gsi module not available: {_e}\n"
        "Install with:\n"
        r'  pip install -r "C:\Program Files\Seequent\GeoStudio 2025.2\API\requirements.txt"'
        "\nAlso ensure you are using Python 3.12.x (required by gsi)."
    )

app = Server("geostudio-mcp")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(data: Any) -> list[types.TextContent]:
    if isinstance(data, (dict, list)):
        text = json.dumps(data, indent=2, default=str)
    else:
        text = str(data)
    return [types.TextContent(type="text", text=text)]


def _err(msg: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=f"ERROR: {msg}")]


def _require_gsi():
    """Raise RuntimeError with setup instructions if gsi is not available."""
    if not _GSI_AVAILABLE:
        raise RuntimeError(_GSI_ERROR)


def _open(file_path: str):
    """Open a GeoStudio project safely, raising RuntimeError on failure."""
    _require_gsi()
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"GSZ file not found: {file_path}")
    try:
        return gsi.OpenProject(file_path)
    except Exception as e:
        raise RuntimeError(
            f"Could not connect to GeoStudio service: {e}\n"
            "Ensure GeoStudio 2025.2 is running (the background service starts with the app)."
        )

# ---------------------------------------------------------------------------
# Tool: get_backend_info
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_backend_info",
            description=(
                "Check the GeoStudio MCP backend status: whether the gsi module is "
                "installed, the Python version, and whether the GeoStudio service is reachable."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="list_analyses",
            description=(
                "List all analyses in a GeoStudio project file (.gsz), "
                "including their type (SEEP/W, SLOPE/W, etc.) and parent analysis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz project file.",
                    }
                },
                "required": ["file_path"],
            },
        ),
        types.Tool(
            name="list_materials",
            description=(
                "List all materials in a GeoStudio project file with their geotechnical "
                "properties (cohesion, friction angle, unit weight, hydraulic conductivity, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz project file.",
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the analysis to query materials from.",
                    },
                },
                "required": ["file_path", "analysis_name"],
            },
        ),
        types.Tool(
            name="get_slope_results",
            description=(
                "Get SLOPE/W results from a solved analysis: critical factor of safety (FOS), "
                "critical slip surface geometry, and total number of slip surfaces evaluated."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz project file.",
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the SLOPE/W analysis.",
                    },
                },
                "required": ["file_path", "analysis_name"],
            },
        ),
        types.Tool(
            name="get_seep_results",
            description=(
                "Get SEEP/W results at a specific (x, y) coordinate: pore water pressure, "
                "total head, pressure head, and volumetric water content."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz project file.",
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the SEEP/W analysis.",
                    },
                    "x": {"type": "number", "description": "X coordinate (model units)."},
                    "y": {"type": "number", "description": "Y coordinate (model units)."},
                    "step": {
                        "type": "integer",
                        "description": "Timestep number (1-based). Use 1 for steady-state.",
                        "default": 1,
                    },
                },
                "required": ["file_path", "analysis_name", "x", "y"],
            },
        ),
        types.Tool(
            name="get_seep_summary",
            description=(
                "Get a summary of SEEP/W nodal results (pore pressure, total head, VWC) "
                "across the entire mesh at a given timestep: min, max, mean values."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz project file.",
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the SEEP/W analysis.",
                    },
                    "step": {
                        "type": "integer",
                        "description": "Timestep number (1-based). Use 1 for steady-state.",
                        "default": 1,
                    },
                },
                "required": ["file_path", "analysis_name"],
            },
        ),
        types.Tool(
            name="run_analysis",
            description=(
                "Solve one or more GeoStudio analyses. "
                "Set solve_dependencies=true to also solve all upstream parent analyses."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz project file.",
                    },
                    "analysis_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of analysis names to solve.",
                    },
                    "solve_dependencies": {
                        "type": "boolean",
                        "description": "If true, solve parent analyses first. Default: true.",
                        "default": True,
                    },
                },
                "required": ["file_path", "analysis_names"],
            },
        ),
        types.Tool(
            name="update_material",
            description=(
                "Update one or more properties of a named material in a GeoStudio project "
                "and save the file. Supports strength (cohesion, phi), hydraulic (KSat, KYX), "
                "and any other gsi-accessible material property."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz project file.",
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the analysis containing the material.",
                    },
                    "material_name": {
                        "type": "string",
                        "description": "Name of the material to update.",
                    },
                    "properties": {
                        "type": "object",
                        "description": (
                            "Key-value pairs of properties to update. "
                            "Keys use GeoStudio property names, e.g. "
                            "{\"CohesionPrime\": 80.0, \"PhiPrime\": 18.5}"
                        ),
                    },
                },
                "required": ["file_path", "analysis_name", "material_name", "properties"],
            },
        ),
        types.Tool(
            name="update_piezometric_line",
            description=(
                "Update the piezometric surface (water table) in a SLOPE/W or SEEP/W analysis "
                "by replacing its control points. "
                "Points should be provided in order from left to right."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz project file.",
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the analysis.",
                    },
                    "line_name": {
                        "type": "string",
                        "description": "Name of the piezometric line object.",
                    },
                    "points": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                            },
                            "required": ["x", "y"],
                        },
                        "description": "Ordered list of {x, y} points defining the water table.",
                    },
                },
                "required": ["file_path", "analysis_name", "line_name", "points"],
            },
        ),
        types.Tool(
            name="sensitivity_analysis",
            description=(
                "Run a one-at-a-time (OAT) sensitivity analysis on material strength or "
                "hydraulic parameters. Varies each parameter over a range, solves the target "
                "analysis, and returns the FS or other result metric at each value."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz project file.",
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the SLOPE/W analysis to evaluate FS in.",
                    },
                    "material_name": {
                        "type": "string",
                        "description": "Name of the material whose parameter is varied.",
                    },
                    "parameter": {
                        "type": "string",
                        "description": "Property name to vary, e.g. 'CohesionPrime' or 'PhiPrime'.",
                    },
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "List of values to test for the parameter.",
                    },
                    "seep_analysis_name": {
                        "type": "string",
                        "description": "If provided, re-solve this SEEP/W analysis before each FS solve.",
                    },
                },
                "required": ["file_path", "analysis_name", "material_name", "parameter", "values"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "get_backend_info":
            return await _tool_get_backend_info()

        elif name == "list_analyses":
            return await _tool_list_analyses(**arguments)

        elif name == "list_materials":
            return await _tool_list_materials(**arguments)

        elif name == "get_slope_results":
            return await _tool_get_slope_results(**arguments)

        elif name == "get_seep_results":
            return await _tool_get_seep_results(**arguments)

        elif name == "get_seep_summary":
            return await _tool_get_seep_summary(**arguments)

        elif name == "run_analysis":
            return await _tool_run_analysis(**arguments)

        elif name == "update_material":
            return await _tool_update_material(**arguments)

        elif name == "update_piezometric_line":
            return await _tool_update_piezometric_line(**arguments)

        elif name == "sensitivity_analysis":
            return await _tool_sensitivity_analysis(**arguments)

        else:
            return _err(f"Unknown tool: {name}")

    except Exception as e:
        return _err(str(e))


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def _tool_get_backend_info() -> list[types.TextContent]:
    import sys
    info: dict[str, Any] = {
        "python_version": sys.version,
        "gsi_available":  _GSI_AVAILABLE,
    }
    if not _GSI_AVAILABLE:
        info["setup_error"] = _GSI_ERROR
        return _ok(info)

    # Try to reach the GeoStudio service with a lightweight connection test
    info["gsi_module"] = str(getattr(gsi, "__version__", "installed (version unknown)"))
    try:
        # Opening a non-existent path will still tell us if the service is up
        gsi.OpenProject("__ping__")
    except FileNotFoundError:
        info["service_status"] = "reachable (file not found is expected for ping)"
    except Exception as e:
        if "not found" in str(e).lower() or "no such file" in str(e).lower():
            info["service_status"] = "reachable"
        else:
            info["service_status"] = f"error — {e}"
    return _ok(info)


async def _tool_list_analyses(file_path: str) -> list[types.TextContent]:
    project = _open(file_path)
    try:
        resp = project.Get(gsi.GetRequest(object="Analyses"))
        data = MessageToDict(resp.data.struct_value)
        analyses = []
        for entry in data.get("analyses", data.get("Analyses", [])):
            analyses.append({
                "name":   entry.get("Name", entry.get("name", "")),
                "type":   entry.get("Type", entry.get("type", entry.get("Kind", ""))),
                "parent": entry.get("ParentName", entry.get("parentName", None)),
            })
        return _ok({"analyses": analyses, "count": len(analyses)})
    finally:
        project.Close()


async def _tool_list_materials(
    file_path: str, analysis_name: str
) -> list[types.TextContent]:
    project = _open(file_path)
    try:
        resp = project.Get(gsi.GetRequest(
            analysis=analysis_name,
            object="Materials",
        ))
        data = MessageToDict(resp.data.struct_value)
        # Normalise — gsi may return a list or a dict keyed by name
        raw = data.get("materials", data.get("Materials", data))
        if isinstance(raw, dict):
            mats = [{"name": k, **v} for k, v in raw.items()]
        elif isinstance(raw, list):
            mats = raw
        else:
            mats = [data]
        return _ok({"materials": mats, "count": len(mats)})
    finally:
        project.Close()


async def _tool_get_slope_results(
    file_path: str, analysis_name: str
) -> list[types.TextContent]:
    project = _open(file_path)
    try:
        # Load results into memory
        project.LoadResults(gsi.LoadResultsRequest(analysis=analysis_name))

        # Query critical slip surface data
        crit_req = gsi.QueryResultsRequest(
            analysis=analysis_name,
            step=1,
            table=gsi.ResultType.CriticalSlip,
            dataparams=[
                gsi.DataParamType.eSlipFOSMin,
                gsi.DataParamType.eXCoord,
                gsi.DataParamType.eYCoord,
                gsi.DataParamType.eSlipNum,
            ],
        )
        crit_resp = project.QueryResults(crit_req)

        def _vals(param):
            entry = crit_resp.results.get(param)
            return list(entry.values) if entry else []

        fos_vals = _vals(gsi.DataParamType.eSlipFOSMin)
        x_vals   = _vals(gsi.DataParamType.eXCoord)
        y_vals   = _vals(gsi.DataParamType.eYCoord)

        # Count total slip surfaces via Slip table
        slip_req = gsi.QueryResultsRequest(
            analysis=analysis_name,
            step=1,
            table=gsi.ResultType.Slip,
            dataparams=[gsi.DataParamType.eSlipNum],
        )
        slip_resp = project.QueryResults(slip_req)
        slip_nums = list(slip_resp.results.get(gsi.DataParamType.eSlipNum, {}).values or [])

        critical_fos = min(fos_vals) if fos_vals else None

        return _ok({
            "analysis":            analysis_name,
            "critical_fos":        critical_fos,
            "critical_slip": {
                "x_points":        x_vals,
                "y_points":        y_vals,
            },
            "total_slip_surfaces": len(slip_nums),
        })
    finally:
        project.Close()


async def _tool_get_seep_results(
    file_path: str,
    analysis_name: str,
    x: float,
    y: float,
    step: int = 1,
) -> list[types.TextContent]:
    project = _open(file_path)
    try:
        project.LoadResults(gsi.LoadResultsRequest(analysis=analysis_name))

        req = gsi.QueryResultsAtCoordinatesRequest(
            analysis=analysis_name,
            step=step,
            dataparams=[
                gsi.DataParamType.eWaterPressure,
                gsi.DataParamType.eWaterPressureHead,
                gsi.DataParamType.eWaterTotalHead,
                gsi.DataParamType.eVolWC,
                gsi.DataParamType.eMatricSuction,
            ],
            points=[gsi.Point(x=x, y=y)],
        )
        resp = project.QueryResultsAtCoordinates(req)

        def _val(param):
            entry = resp.results.get(param)
            return entry.values[0] if entry and entry.values else None

        return _ok({
            "analysis":             analysis_name,
            "step":                 step,
            "x":                    x,
            "y":                    y,
            "pore_water_pressure":  _val(gsi.DataParamType.eWaterPressure),
            "pressure_head":        _val(gsi.DataParamType.eWaterPressureHead),
            "total_head":           _val(gsi.DataParamType.eWaterTotalHead),
            "volumetric_wc":        _val(gsi.DataParamType.eVolWC),
            "matric_suction":       _val(gsi.DataParamType.eMatricSuction),
        })
    finally:
        project.Close()


async def _tool_get_seep_summary(
    file_path: str,
    analysis_name: str,
    step: int = 1,
) -> list[types.TextContent]:
    import statistics

    project = _open(file_path)
    try:
        project.LoadResults(gsi.LoadResultsRequest(analysis=analysis_name))

        req = gsi.QueryResultsRequest(
            analysis=analysis_name,
            step=step,
            table=gsi.ResultType.Nodes,
            dataparams=[
                gsi.DataParamType.eWaterPressure,
                gsi.DataParamType.eWaterTotalHead,
                gsi.DataParamType.eVolWC,
                gsi.DataParamType.eMatricSuction,
            ],
        )
        resp = project.QueryResults(req)

        def _summarise(param, label):
            entry = resp.results.get(param)
            if not entry or not entry.values:
                return {label: "no data"}
            vals = [v for v in entry.values if v is not None]
            return {
                f"{label}_min":  round(min(vals), 4),
                f"{label}_max":  round(max(vals), 4),
                f"{label}_mean": round(statistics.mean(vals), 4),
            }

        result = {
            "analysis": analysis_name,
            "step":     step,
            **_summarise(gsi.DataParamType.eWaterPressure,  "pore_pressure"),
            **_summarise(gsi.DataParamType.eWaterTotalHead, "total_head"),
            **_summarise(gsi.DataParamType.eVolWC,          "vwc"),
            **_summarise(gsi.DataParamType.eMatricSuction,  "matric_suction"),
        }
        return _ok(result)
    finally:
        project.Close()


async def _tool_run_analysis(
    file_path: str,
    analysis_names: list[str],
    solve_dependencies: bool = True,
) -> list[types.TextContent]:
    project = _open(file_path)
    try:
        req  = gsi.SolveAnalysesRequest(
            analyses=analysis_names,
            solve_dependencies=solve_dependencies,
        )
        resp = project.SolveAnalyses(req)

        status = {}
        for name, result in resp.completion_status.items():
            status[name] = {
                "succeeded":     result.succeeded,
                "error_message": result.error_message or None,
            }
        return _ok({
            "all_succeeded": resp.all_succeeded,
            "analyses":      status,
        })
    finally:
        project.Close()


async def _tool_update_material(
    file_path: str,
    analysis_name: str,
    material_name: str,
    properties: dict,
) -> list[types.TextContent]:
    project = _open(file_path)
    try:
        updated = {}
        for prop, value in properties.items():
            obj_path = f'Materials["{material_name}"].{prop}'
            if isinstance(value, bool):
                data = gsi.Value(bool_value=value)
            elif isinstance(value, str):
                data = gsi.Value(string_value=value)
            else:
                data = gsi.Value(number_value=float(value))

            project.Set(gsi.SetRequest(
                analysis=analysis_name,
                object=obj_path,
                data=data,
            ))
            updated[prop] = value

        return _ok({
            "material":           material_name,
            "analysis":           analysis_name,
            "updated_properties": updated,
            "status":             "saved",
        })
    finally:
        project.Close()


async def _tool_update_piezometric_line(
    file_path: str,
    analysis_name: str,
    line_name: str,
    points: list[dict],
) -> list[types.TextContent]:
    project = _open(file_path)
    try:
        from google.protobuf.struct_pb2 import Value, ListValue, Struct

        pt_list = []
        for pt in points:
            s = Struct()
            s.fields["x"].number_value = float(pt["x"])
            s.fields["y"].number_value = float(pt["y"])
            v = Value()
            v.struct_value.CopyFrom(s)
            pt_list.append(v)

        lv = ListValue()
        lv.values.extend(pt_list)
        data = gsi.Value()
        data.list_value.CopyFrom(lv)

        obj_path = f'CurrentAnalysis.Objects.PiezometricSurfaces["{line_name}"].Points'
        project.Set(gsi.SetRequest(
            analysis=analysis_name,
            object=obj_path,
            data=data,
        ))

        return _ok({
            "line_name":    line_name,
            "analysis":     analysis_name,
            "points_set":   len(points),
            "status":       "saved",
        })
    finally:
        project.Close()


async def _tool_sensitivity_analysis(
    file_path: str,
    analysis_name: str,
    material_name: str,
    parameter: str,
    values: list[float],
    seep_analysis_name: str | None = None,
) -> list[types.TextContent]:
    project = _open(file_path)
    try:
        results = []
        obj_path = f'Materials["{material_name}"].{parameter}'

        for val in values:
            # Set parameter
            project.Set(gsi.SetRequest(
                analysis=analysis_name,
                object=obj_path,
                data=gsi.Value(number_value=float(val)),
            ))

            # Solve (optionally re-solve SEEP first)
            analyses_to_solve = []
            if seep_analysis_name:
                analyses_to_solve.append(seep_analysis_name)
            analyses_to_solve.append(analysis_name)

            solve_resp = project.SolveAnalyses(gsi.SolveAnalysesRequest(
                analyses=analyses_to_solve,
                solve_dependencies=False,
            ))

            if not solve_resp.all_succeeded:
                results.append({"value": val, "fos": None, "error": "solve failed"})
                continue

            # Read FS from critical slip
            project.LoadResults(gsi.LoadResultsRequest(analysis=analysis_name))
            crit_resp = project.QueryResults(gsi.QueryResultsRequest(
                analysis=analysis_name,
                step=1,
                table=gsi.ResultType.CriticalSlip,
                dataparams=[gsi.DataParamType.eSlipFOSMin],
            ))
            fos_entry = crit_resp.results.get(gsi.DataParamType.eSlipFOSMin)
            fos = min(fos_entry.values) if fos_entry and fos_entry.values else None

            results.append({
                "value":     val,
                "fos":       round(fos, 4) if fos is not None else None,
            })

        return _ok({
            "material":   material_name,
            "parameter":  parameter,
            "analysis":   analysis_name,
            "results":    results,
        })
    finally:
        project.Close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
