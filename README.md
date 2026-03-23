# GeoStudio MCP Server

Connect Claude to GeoStudio for AI-assisted geotechnical analysis.
Supports SLOPE/W (slope stability) and SEEP/W (seepage) workflows.

---

## What this does

Once connected, you can ask Claude things like:
- "List all analyses in my project file"
- "Run the slope stability analysis and tell me the critical FOS"
- "Run a sensitivity analysis on cohesion from 5 to 25 kPa and plot the FOS"
- "Update the piezometric line with these sensor readings and re-solve"
- "What are the seep pressures at point (15.3, 8.7)?"

---

## Setup - choose your backend

### Option A: Official GeoStudio 2025.1+ API (recommended)

This uses the Python scripting engine built into GeoStudio 2025.1+.

**Step 1 - Find the GeoStudio Python interpreter**

- Windows: `C:\Program Files\Seequent\GeoStudio 2025\Python\python.exe`
- macOS: `/Applications/GeoStudio 2025.app/Contents/Python/bin/python3`

**Step 2 - Install the MCP package into that interpreter**

```bash
# Windows
"C:\Program Files\Seequent\GeoStudio 2025\Python\python.exe" -m pip install mcp

# macOS
/Applications/GeoStudio\ 2025.app/Contents/Python/bin/python3 -m pip install mcp
```

**Step 3 - Add to Claude Desktop config**

Open `~/.config/claude/claude_desktop_config.json` (macOS/Linux)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows) and add:

```json
{
  "mcpServers": {
    "geostudio": {
      "command": "C:\\Program Files\\Seequent\\GeoStudio 2025\\Python\\python.exe",
      "args": ["C:\\path\\to\\geostudio-mcp\\server.py"],
      "env": {
        "GEOSTUDIO_BACKEND": "official"
      }
    }
  }
}
```

> On macOS replace the paths accordingly.

---

### Option B: PyGeoStudio (open-source, any Python)

Works without GeoStudio 2025.1+ - reads/writes .gsz files directly.
Does not require GeoStudio to be running.

**Step 1 - Install dependencies**

```bash
pip install mcp PyGeoStudio
```

**Step 2 - Add to Claude Desktop config**

```json
{
  "mcpServers": {
    "geostudio": {
      "command": "python",
      "args": ["/path/to/geostudio-mcp/server.py"],
      "env": {
        "GEOSTUDIO_BACKEND": "pygeostudio"
      }
    }
  }
}
```

---

## Available tools

| Tool | Description | Backend |
|------|-------------|---------|
| `list_analyses` | List all analyses in a .gsz file | Both |
| `list_materials` | List all materials and their properties | Both |
| `run_analysis` | Solve a specific analysis | Both |
| `get_slope_results` | Get critical FOS and slip surface | Official only |
| `get_seep_results` | Query seepage results at a point (x, y) | Official only |
| `get_seep_summary` | Min/max/mean seepage stats across all nodes | PyGeoStudio only |
| `update_material` | Change material properties (cohesion, phi, ksat, etc.) | Both |
| `update_piezometric_line` | Update water table with new sensor data | Official only |
| `sensitivity_analysis` | Vary a property and get FOS for each value | Both |
| `get_backend_info` | Check which backend is active | Both |

---

## Property names by backend

### Official API
- `cohesion` - effective cohesion (kPa)
- `friction_angle` - effective friction angle (degrees)
- `unit_weight` - total unit weight (kN/m³)
- `hydraulic_conductivity_x` - kx (m/s)
- `hydraulic_conductivity_y` - ky (m/s)

### PyGeoStudio
- `cohesion` - effective cohesion (kPa)
- `phi` - friction angle (degrees)
- `unit_weight` - total unit weight (kN/m³)
- `ksat` - saturated hydraulic conductivity (m/s)

---

## Verify it works

After restarting Claude Desktop, open a new chat and type:

```
Use the geostudio tool to check the backend info
```

Then try:

```
List all analyses in /path/to/my/project.gsz
```

---

## Troubleshooting

**"Could not import geostudio"**
- You are not using GeoStudio's bundled Python interpreter
- Check the path in your claude_desktop_config.json

**"Could not import PyGeoStudio"**
- Run: `pip install PyGeoStudio`

**Tool not appearing in Claude**
- Restart Claude Desktop after editing the config
- Check the config JSON is valid (no trailing commas)
- On Windows MSIX installs, see the note about config path in the MCP docs
