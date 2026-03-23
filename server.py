"""
GeoStudio MCP Server
====================
Exposes GeoStudio capabilities as MCP tools for Claude.
Supports two backends:
  - "official"  : GeoStudio 2025.1+ built-in Python scripting API
  - "pygeostudio": PyGeoStudio open-source library (reads/writes .gsz files)

Set the GEOSTUDIO_BACKEND environment variable to choose:
  export GEOSTUDIO_BACKEND=official        (default)
  export GEOSTUDIO_BACKEND=pygeostudio
"""

import os
import json
import asyncio
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------
BACKEND = os.environ.get("GEOSTUDIO_BACKEND", "official").lower()

app = Server("geostudio-mcp")

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _ok(data: Any) -> list[types.TextContent]:
    """Wrap a result as a successful MCP text response."""
    if isinstance(data, (dict, list)):
        text = json.dumps(data, indent=2, default=str)
    else:
        text = str(data)
    return [types.TextContent(type="text", text=text)]


def _err(msg: str) -> list[types.TextContent]:
    """Wrap an error message as an MCP text response."""
    return [types.TextContent(type="text", text=f"ERROR: {msg}")]


# ---------------------------------------------------------------------------
# Official GeoStudio 2025.1+ scripting API backend
# ---------------------------------------------------------------------------

def _get_official_api():
    """
    Import the GeoStudio scripting module shipped with GeoStudio 2025.1+.
    The module is typically named 'geostudio' and lives in the GeoStudio
    Python distribution (configured via the GeoStudio app settings).
    """
    try:
        import geostudio  # noqa: F401 - provided by GeoStudio installation
        return geostudio
    except ImportError as e:
        raise ImportError(
            "Could not import the 'geostudio' module. "
            "Make sure you are running this server with the Python interpreter "
            "that ships with GeoStudio 2025.1+, or that GeoStudio's Python "
            "distribution is on your PYTHONPATH.\n"
            f"Original error: {e}"
        )


def official_open_project(file_path: str):
    gs = _get_official_api()
    return gs.open(file_path)


def official_list_analyses(file_path: str) -> list[dict]:
    gs = _get_official_api()
    project = gs.open(file_path)
    analyses = []
    for a in project.analyses:
        analyses.append({
            "name": a.name,
            "type": a.analysis_type,
            "parent": getattr(a, "parent_name", None),
        })
    return analyses


def official_run_analysis(file_path: str, analysis_name: str) -> dict:
    gs = _get_official_api()
    project = gs.open(file_path)
    analysis = project.get_analysis(analysis_name)
    analysis.solve()
    return {"status": "solved", "analysis": analysis_name}


def official_get_slope_results(file_path: str, analysis_name: str) -> dict:
    gs = _get_official_api()
    project = gs.open(file_path)
    analysis = project.get_analysis(analysis_name)
    results = analysis.results
    return {
        "analysis": analysis_name,
        "critical_fos": results.critical_slip_surface.factor_of_safety,
        "critical_slip_surface": {
            "x_entry": results.critical_slip_surface.entry_x,
            "y_entry": results.critical_slip_surface.entry_y,
            "x_exit": results.critical_slip_surface.exit_x,
            "y_exit": results.critical_slip_surface.exit_y,
        },
        "num_slip_surfaces": results.num_slip_surfaces,
    }


def official_get_seep_results(file_path: str, analysis_name: str,
                               x: float, y: float) -> dict:
    gs = _get_official_api()
    project = gs.open(file_path)
    analysis = project.get_analysis(analysis_name)
    results = analysis.results
    pt = results.query_point(x, y)
    return {
        "analysis": analysis_name,
        "x": x, "y": y,
        "pore_water_pressure": pt.pore_water_pressure,
        "total_head": pt.total_head,
        "pressure_head": pt.pressure_head,
        "flow_velocity_x": pt.flow_velocity_x,
        "flow_velocity_y": pt.flow_velocity_y,
    }


def official_update_material(file_path: str, material_name: str,
                              properties: dict, save: bool = True) -> dict:
    gs = _get_official_api()
    project = gs.open(file_path)
    mat = project.get_material(material_name)
    for prop, value in properties.items():
        setattr(mat, prop, value)
    if save:
        project.save()
    return {"material": material_name, "updated_properties": properties}


def official_update_piezometric_line(file_path: str, analysis_name: str,
                                     points: list[dict], save: bool = True) -> dict:
    """
    Update a piezometric line in a SEEP/W or SLOPE/W analysis.
    points: list of {"x": float, "y": float}
    """
    gs = _get_official_api()
    project = gs.open(file_path)
    analysis = project.get_analysis(analysis_name)
    coords = [(p["x"], p["y"]) for p in points]
    analysis.set_piezometric_line(coords)
    if save:
        project.save()
    return {"analysis": analysis_name, "piezometric_line_updated": True,
            "num_points": len(coords)}


def official_sensitivity_analysis(file_path: str, analysis_name: str,
                                   material_name: str, property_name: str,
                                   values: list[float]) -> dict:
    gs = _get_official_api()
    project = gs.open(file_path)
    mat = project.get_material(material_name)
    results = []
    for v in values:
        setattr(mat, property_name, v)
        analysis = project.get_analysis(analysis_name)
        analysis.solve()
        fos = analysis.results.critical_slip_surface.factor_of_safety
        results.append({"value": v, "fos": fos})
    return {
        "analysis": analysis_name,
        "material": material_name,
        "property": property_name,
        "sensitivity": results,
    }


def official_list_materials(file_path: str) -> list[dict]:
    gs = _get_official_api()
    project = gs.open(file_path)
    mats = []
    for m in project.materials:
        props = {}
        for attr in ["cohesion", "friction_angle", "unit_weight",
                     "hydraulic_conductivity_x", "hydraulic_conductivity_y",
                     "saturated_water_content", "residual_water_content"]:
            val = getattr(m, attr, None)
            if val is not None:
                props[attr] = val
        mats.append({"name": m.name, "type": m.material_type, "properties": props})
    return mats


# ---------------------------------------------------------------------------
# PyGeoStudio backend
# ---------------------------------------------------------------------------

def _get_pygeostudio():
    try:
        import PyGeoStudio as pgs  # noqa: F401
        return pgs
    except ImportError as e:
        raise ImportError(
            "Could not import 'PyGeoStudio'. Install it with:\n"
            "  pip install PyGeoStudio\n"
            f"Original error: {e}"
        )


def _find_analysis(study, analysis_name: str):
    """Find an analysis by name from study.analyses list."""
    for a in study.analyses:
        if a.data.get("Name") == analysis_name:
            return a
    raise ValueError(f"Analysis '{analysis_name}' not found. Available: {[a.data.get('Name') for a in study.analyses]}")


def _find_material(study, material_name: str):
    """Find a material by name from study.materials list."""
    for m in study.materials:
        if m.data.get("Name") == material_name:
            return m
    raise ValueError(f"Material '{material_name}' not found. Available: {[m.data.get('Name') for m in study.materials]}")


def pygs_list_analyses(file_path: str) -> list[dict]:
    pgs = _get_pygeostudio()
    study = pgs.GeoStudioFile(file_path)
    return [
        {
            "name": a.data.get("Name", "Unknown"),
            "type": a.data.get("Kind", "Unknown"),
            "id": a.data.get("ID", ""),
        }
        for a in study.analyses
    ]


def pygs_list_materials(file_path: str) -> list[dict]:
    pgs = _get_pygeostudio()
    study = pgs.GeoStudioFile(file_path)
    mats = []
    for mat in study.materials:
        d = mat.data
        props = {"SlopeModel": d.get("SlopeModel")}
        # StressStrain holds cohesion/phi/unit_weight - extract if available
        ss = d.get("StressStrain")
        if ss is not None and hasattr(ss, "data"):
            for key in ["Cohesion", "Phi", "UnitWeight", "Ksat"]:
                val = ss.data.get(key)
                if val is not None:
                    props[key] = val
        mats.append({"name": d.get("Name", "Unknown"), "id": d.get("ID", ""), "properties": props})
    return mats


def pygs_update_material(file_path: str, material_name: str,
                          properties: dict, output_path: str = None) -> dict:
    pgs = _get_pygeostudio()
    study = pgs.GeoStudioFile(file_path)
    mat = _find_material(study, material_name)
    for prop, value in properties.items():
        mat.data[prop] = value
    save_path = output_path or file_path
    study.save(save_path)
    return {"material": material_name, "updated": properties, "saved_to": save_path}


def pygs_run_analysis(file_path: str, analysis_name: str) -> dict:
    pgs = _get_pygeostudio()
    study = pgs.GeoStudioFile(file_path)
    analysis = _find_analysis(study, analysis_name)
    analysis.solve()
    return {"status": "solved", "analysis": analysis_name}


def pygs_get_seep_results(file_path: str, analysis_name: str) -> dict:
    """Export SEEP/W nodal results to a summary dict."""
    pgs = _get_pygeostudio()
    study = pgs.GeoStudioFile(file_path)
    analysis = _find_analysis(study, analysis_name)
    result = analysis.data.get("Results")
    if result is None:
        raise ValueError(f"No results found for analysis '{analysis_name}'. Has it been solved?")
    nodes = result.get_nodes() if hasattr(result, "get_nodes") else []
    pwps = [n.pore_water_pressure for n in nodes if hasattr(n, "pore_water_pressure")]
    heads = [n.total_head for n in nodes if hasattr(n, "total_head")]
    return {
        "analysis": analysis_name,
        "num_nodes": len(nodes),
        "pore_water_pressure": {
            "min": min(pwps) if pwps else None,
            "max": max(pwps) if pwps else None,
            "mean": sum(pwps) / len(pwps) if pwps else None,
        },
        "total_head": {
            "min": min(heads) if heads else None,
            "max": max(heads) if heads else None,
            "mean": sum(heads) / len(heads) if heads else None,
        },
    }


def pygs_sensitivity_analysis(file_path: str, analysis_name: str,
                               material_name: str, property_name: str,
                               values: list[float]) -> dict:
    pgs = _get_pygeostudio()
    study = pgs.GeoStudioFile(file_path)
    results_list = []
    for v in values:
        mat = _find_material(study, material_name)
        mat.data[property_name] = v
        analysis = _find_analysis(study, analysis_name)
        analysis.solve()
        fos = None
        result = analysis.data.get("Results")
        if result and hasattr(result, "critical_factor_of_safety"):
            fos = result.critical_factor_of_safety
        results_list.append({"value": v, "fos": fos})
    return {
        "analysis": analysis_name,
        "material": material_name,
        "property": property_name,
        "sensitivity": results_list,
    }


# ---------------------------------------------------------------------------
# MCP tool registry
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_analyses",
            description=(
                "List all analyses defined in a GeoStudio project file (.gsz). "
                "Returns name, type (SLOPE/W, SEEP/W, etc.), and parent analysis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz GeoStudio project file."
                    }
                },
                "required": ["file_path"]
            }
        ),
        types.Tool(
            name="list_materials",
            description=(
                "List all materials defined in a GeoStudio project file, "
                "including their geotechnical properties (cohesion, friction angle, "
                "unit weight, hydraulic conductivity, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz GeoStudio project file."
                    }
                },
                "required": ["file_path"]
            }
        ),
        types.Tool(
            name="run_analysis",
            description=(
                "Run (solve) a specific analysis in a GeoStudio project. "
                "Works for SLOPE/W and SEEP/W analyses."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz GeoStudio project file."
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the analysis to solve."
                    }
                },
                "required": ["file_path", "analysis_name"]
            }
        ),
        types.Tool(
            name="get_slope_results",
            description=(
                "Get SLOPE/W results from a solved analysis: "
                "critical factor of safety (FOS), critical slip surface geometry, "
                "and total number of slip surfaces evaluated."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz GeoStudio project file."
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the SLOPE/W analysis."
                    }
                },
                "required": ["file_path", "analysis_name"]
            }
        ),
        types.Tool(
            name="get_seep_results",
            description=(
                "Get SEEP/W results at a specific point (x, y) in a solved analysis: "
                "pore water pressure, total head, pressure head, and flow velocity components. "
                "(Official API only - for PyGeoStudio use get_seep_summary instead.)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz GeoStudio project file."
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the SEEP/W analysis."
                    },
                    "x": {
                        "type": "number",
                        "description": "X coordinate of the query point."
                    },
                    "y": {
                        "type": "number",
                        "description": "Y coordinate of the query point."
                    }
                },
                "required": ["file_path", "analysis_name", "x", "y"]
            }
        ),
        types.Tool(
            name="get_seep_summary",
            description=(
                "Get a statistical summary of SEEP/W results across all mesh nodes: "
                "min/max/mean pore water pressure and total head. "
                "(PyGeoStudio backend only.)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz GeoStudio project file."
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the SEEP/W analysis."
                    }
                },
                "required": ["file_path", "analysis_name"]
            }
        ),
        types.Tool(
            name="update_material",
            description=(
                "Update material properties in a GeoStudio project. "
                "For example, change cohesion, friction angle, unit weight, "
                "or hydraulic conductivity values. Saves the file after updating."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz GeoStudio project file."
                    },
                    "material_name": {
                        "type": "string",
                        "description": "Name of the material to update."
                    },
                    "properties": {
                        "type": "object",
                        "description": (
                            "Dictionary of property names and new values. "
                            "Official API keys: cohesion, friction_angle, unit_weight, "
                            "hydraulic_conductivity_x, hydraulic_conductivity_y. "
                            "PyGeoStudio keys: cohesion, phi, unit_weight, ksat."
                        )
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Optional output path to save to a new file (PyGeoStudio only). Defaults to overwriting the input file."
                    }
                },
                "required": ["file_path", "material_name", "properties"]
            }
        ),
        types.Tool(
            name="update_piezometric_line",
            description=(
                "Update the piezometric line in a SLOPE/W or SEEP/W analysis. "
                "Useful for incorporating real-time sensor data or changing water table conditions. "
                "(Official API only.)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz GeoStudio project file."
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the analysis to update."
                    },
                    "points": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "number"},
                                "y": {"type": "number"}
                            },
                            "required": ["x", "y"]
                        },
                        "description": "List of {x, y} coordinate pairs defining the piezometric line."
                    }
                },
                "required": ["file_path", "analysis_name", "points"]
            }
        ),
        types.Tool(
            name="sensitivity_analysis",
            description=(
                "Run a parametric sensitivity analysis: vary a single material property "
                "across a list of values, re-solve the analysis each time, and return "
                "the factor of safety (SLOPE/W) for each value. "
                "Great for understanding how FOS changes with cohesion, friction angle, etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .gsz GeoStudio project file."
                    },
                    "analysis_name": {
                        "type": "string",
                        "description": "Name of the SLOPE/W analysis."
                    },
                    "material_name": {
                        "type": "string",
                        "description": "Name of the material whose property will be varied."
                    },
                    "property_name": {
                        "type": "string",
                        "description": (
                            "Property to vary. "
                            "Official API: cohesion, friction_angle, unit_weight. "
                            "PyGeoStudio: cohesion, phi, unit_weight."
                        )
                    },
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "List of values to test for the property."
                    }
                },
                "required": ["file_path", "analysis_name", "material_name",
                             "property_name", "values"]
            }
        ),
        types.Tool(
            name="get_backend_info",
            description="Return which backend is active (official or pygeostudio) and check if the required library is importable.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
    ]


# ---------------------------------------------------------------------------
# MCP tool dispatcher
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        # ---- backend info -----------------------------------------------
        if name == "get_backend_info":
            info = {"backend": BACKEND}
            if BACKEND == "official":
                try:
                    _get_official_api()
                    info["importable"] = True
                except ImportError as e:
                    info["importable"] = False
                    info["error"] = str(e)
            else:
                try:
                    _get_pygeostudio()
                    info["importable"] = True
                except ImportError as e:
                    info["importable"] = False
                    info["error"] = str(e)
            return _ok(info)

        # ---- list analyses -----------------------------------------------
        if name == "list_analyses":
            fp = arguments["file_path"]
            if BACKEND == "official":
                return _ok(official_list_analyses(fp))
            else:
                return _ok(pygs_list_analyses(fp))

        # ---- list materials ----------------------------------------------
        if name == "list_materials":
            fp = arguments["file_path"]
            if BACKEND == "official":
                return _ok(official_list_materials(fp))
            else:
                return _ok(pygs_list_materials(fp))

        # ---- run analysis ------------------------------------------------
        if name == "run_analysis":
            fp = arguments["file_path"]
            an = arguments["analysis_name"]
            if BACKEND == "official":
                return _ok(official_run_analysis(fp, an))
            else:
                return _ok(pygs_run_analysis(fp, an))

        # ---- slope results -----------------------------------------------
        if name == "get_slope_results":
            fp = arguments["file_path"]
            an = arguments["analysis_name"]
            if BACKEND == "official":
                return _ok(official_get_slope_results(fp, an))
            else:
                return _err(
                    "get_slope_results is only available with the official backend. "
                    "Set GEOSTUDIO_BACKEND=official and use GeoStudio 2025.1+."
                )

        # ---- seep point results (official only) --------------------------
        if name == "get_seep_results":
            fp = arguments["file_path"]
            an = arguments["analysis_name"]
            x = arguments["x"]
            y = arguments["y"]
            if BACKEND == "official":
                return _ok(official_get_seep_results(fp, an, x, y))
            else:
                return _err(
                    "get_seep_results (point query) requires the official backend. "
                    "Use get_seep_summary for PyGeoStudio, or switch to GEOSTUDIO_BACKEND=official."
                )

        # ---- seep summary (pygeostudio) ----------------------------------
        if name == "get_seep_summary":
            fp = arguments["file_path"]
            an = arguments["analysis_name"]
            if BACKEND == "pygeostudio":
                return _ok(pygs_get_seep_results(fp, an))
            else:
                return _err(
                    "get_seep_summary is only available with the pygeostudio backend. "
                    "Use get_seep_results for point queries with the official API."
                )

        # ---- update material --------------------------------------------
        if name == "update_material":
            fp = arguments["file_path"]
            mn = arguments["material_name"]
            props = arguments["properties"]
            if BACKEND == "official":
                return _ok(official_update_material(fp, mn, props))
            else:
                out = arguments.get("output_path")
                return _ok(pygs_update_material(fp, mn, props, out))

        # ---- update piezometric line (official only) --------------------
        if name == "update_piezometric_line":
            fp = arguments["file_path"]
            an = arguments["analysis_name"]
            pts = arguments["points"]
            if BACKEND == "official":
                return _ok(official_update_piezometric_line(fp, an, pts))
            else:
                return _err(
                    "update_piezometric_line requires the official GeoStudio 2025.1+ backend. "
                    "Set GEOSTUDIO_BACKEND=official."
                )

        # ---- sensitivity analysis ---------------------------------------
        if name == "sensitivity_analysis":
            fp = arguments["file_path"]
            an = arguments["analysis_name"]
            mn = arguments["material_name"]
            pn = arguments["property_name"]
            vals = arguments["values"]
            if BACKEND == "official":
                return _ok(official_sensitivity_analysis(fp, an, mn, pn, vals))
            else:
                return _ok(pygs_sensitivity_analysis(fp, an, mn, pn, vals))

        return _err(f"Unknown tool: {name}")

    except Exception as e:
        return _err(f"Tool '{name}' raised an exception: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())