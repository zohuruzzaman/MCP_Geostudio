"""
SLOPE/W Strength Calibration - Dual Objective (FS + Inclinometer Depth)
========================================================================
Calibrates c' and phi' for AWYC and WYC against two targets simultaneously:

  1. Factor of safety at failure event   -> FS = 1.0  (back-analysis)
  2. Slip surface depth at borehole X    -> match inclinometer shear zone depth

Pore pressures come from the calibrated SEEP/W model (BASE_GSZ).

Dual cost function:
  cost = W_FS * (FS - 1.0)^2  +  W_DEPTH * ((model_depth - obs_depth) / obs_depth)^2

Both terms are dimensionless. W_FS and W_DEPTH control the trade-off.
Normalising the depth term by obs_depth makes it scale-invariant.

Inclinometer format (new @ delimited):
  - A and B directions in same row, separated by '@'
  - Row 0: title (skip)
  - Row 1: description (skip)
  - Row 2: date headers
  - Row 3: blank depth spacer (skip)
  - Row 4+: depth_ft, disp values...
  Uses A-direction only (primary cross-slope movement direction).
  Targets CALIB_DATE column specifically.

Requires
--------
    pip install scipy numpy pandas grpc gsi
    GeoStudio 2025.1+ background service running

Usage
-----
    python calibrate_slope.py
"""

from __future__ import annotations
import os, sys, shutil, warnings
import numpy as np
import pandas as pd
from scipy.optimize import minimize

try:
    import grpc                                          # noqa: F401
    from google.protobuf.json_format import MessageToDict
    from google.protobuf.struct_pb2 import Value, ListValue, Struct
    import gsi
    _GSI_OK = True
except ImportError as _e:
    _GSI_OK = False
    _GSI_ERROR = str(_e)

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
CALIB_DIR   = r"E:\Github\MCP_Geostudio\calibration"
BASE_GSZ    = os.path.join(CALIB_DIR, "Metro-Center-seep-final.gsz")
WORK_GSZ    = os.path.join(CALIB_DIR, "_slope_work.gsz")
OUT_GSZ     = os.path.join(CALIB_DIR, "Metro-Center-slope-final.gsz")
LOG_CSV     = os.path.join(CALIB_DIR, "slope_calibration_trial_log.csv")

INCL_Y1     = os.path.join(CALIB_DIR, "S2_Inclinometer_Y1.csv")
INCL_Y2     = os.path.join(CALIB_DIR, "S2_Inclinometer_Y2.csv")

# Target date for inclinometer reading - matches the failure calibration event
CALIB_DATE  = "12/18/2018"

SEEP_INITIAL   = "Initial Condition"
SEEP_TRANSIENT = "Rainfall Simulation"
SLOPE_FS       = "FS"
ANALYSES_ALL   = [SEEP_INITIAL, SEEP_TRANSIENT, "Slope Stability", SLOPE_FS]

FS_STEP = 1  # fallback if QueryResultsAvailability not supported

BOREHOLE_X  = 195.0    # ft - mid-crest platform (inclinometer location)
CREST_Y     = 83.0     # ft - crest surface elevation

# Baseline parameters (forensic study values)
C_AWYC_BASE,  PHI_AWYC_BASE = 79.3,  19.0
C_WYC_BASE,   PHI_WYC_BASE  = 248.5, 19.0

# Search bounds
C_AWYC_MIN,  C_AWYC_MAX  =   0.0, 250.0
PHI_AWYC_MIN,PHI_AWYC_MAX=  10.0,  25.0
C_WYC_MIN,   C_WYC_MAX   =  50.0, 500.0
PHI_WYC_MIN, PHI_WYC_MAX =  12.0,  25.0

FS_TARGET    = 1.0
MAX_OPT_ITER = 80

# Displacement threshold for shear zone detection (inches)
DISP_THRESH  = 0.005

# Dual objective weights
# Both terms are dimensionless. Increase W_DEPTH to pull slip surface
# toward inclinometer depth; decrease to prioritise FS=1.0 more strongly.
W_FS    = 1.0
W_DEPTH = 0.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_gsi():
    if not _GSI_OK:
        raise RuntimeError(f"gsi not available: {_GSI_ERROR}")


def _set_strength(project, analysis: str, c_awyc: float, phi_awyc: float,
                  c_wyc: float, phi_wyc: float):
    updates = [
        ("Weathered Yazoo Clay in Active Zone", "CohesionPrime", c_awyc),
        ("Weathered Yazoo Clay in Active Zone", "PhiPrime",      phi_awyc),
        ("Weathered Yazoo Clay",                "CohesionPrime", c_wyc),
        ("Weathered Yazoo Clay",                "PhiPrime",      phi_wyc),
    ]
    for mat, prop, val in updates:
        project.Set(gsi.SetRequest(
            analysis=analysis,
            object=f'Materials["{mat}"].{prop}',
            data=gsi.Value(number_value=float(val)),
        ))


def _get_last_step(project, analysis: str) -> int:
    """Return the last available result step index for an analysis."""
    try:
        avail = project.QueryResultsAvailability(
            gsi.QueryResultsAvailabilityRequest(analysis=analysis)
        )
        steps = list(avail.available_steps)
        return max(steps) if steps else FS_STEP
    except Exception:
        return FS_STEP


def _get_critical_fs(project) -> float | None:
    step = _get_last_step(project, SLOPE_FS)
    project.LoadResults(gsi.LoadResultsRequest(analysis=SLOPE_FS))
    resp = project.QueryResults(gsi.QueryResultsRequest(
        analysis=SLOPE_FS, step=step,
        table=gsi.ResultType.CriticalSlip,
        dataparams=[gsi.DataParamType.eSlipFOSMin],
    ))
    entry = resp.results.get(gsi.DataParamType.eSlipFOSMin)
    if entry and entry.values:
        vals = [v for v in entry.values if 0.1 < v < 100]
        return min(vals) if vals else None
    return None


def _get_slip_geometry(project) -> tuple[float | None, float | None, float | None]:
    """
    Returns (cx, cy, radius) of the critical slip circle.
    cx/cy are the slip circle centre. Radius inferred from column base points.
    """
    step = _get_last_step(project, SLOPE_FS)
    resp = project.QueryResults(gsi.QueryResultsRequest(
        analysis=SLOPE_FS, step=step,
        table=gsi.ResultType.CriticalSlip,
        dataparams=[
            gsi.DataParamType.eSlipFOSMin,
            gsi.DataParamType.eXCoord,
            gsi.DataParamType.eYCoord,
        ],
    ))
    fos_e = resp.results.get(gsi.DataParamType.eSlipFOSMin)
    cx_e  = resp.results.get(gsi.DataParamType.eXCoord)
    cy_e  = resp.results.get(gsi.DataParamType.eYCoord)

    if not all([fos_e, cx_e, cy_e]):
        return None, None, None

    fos_v = list(fos_e.values)
    cx_v  = list(cx_e.values)
    cy_v  = list(cy_e.values)

    valid = [(i, f) for i, f in enumerate(fos_v) if 0.1 < f < 100]
    if not valid:
        return None, None, None
    best_i = min(valid, key=lambda x: x[1])[0]
    cx = cx_v[best_i] if best_i < len(cx_v) else None
    cy = cy_v[best_i] if best_i < len(cy_v) else None
    if cx is None or cy is None:
        return None, None, None

    # Infer radius from Column table (base of each slice = point on arc)
    try:
        col_resp = project.QueryResults(gsi.QueryResultsRequest(
            analysis=SLOPE_FS, step=step,
            table=gsi.ResultType.Column,
            dataparams=[gsi.DataParamType.eXCoord, gsi.DataParamType.eYCoord],
        ))
        col_x_entry = col_resp.results.get(gsi.DataParamType.eXCoord)
        col_y_entry = col_resp.results.get(gsi.DataParamType.eYCoord)
        if col_x_entry and col_y_entry:
            col_x = list(col_x_entry.values)
            col_y = list(col_y_entry.values)
            if col_x and col_y:
                dists = [np.sqrt((x - cx)**2 + (y - cy)**2)
                         for x, y in zip(col_x, col_y)]
                return cx, cy, float(np.median(dists))
    except Exception:
        pass
    return cx, cy, None


def _slip_depth_at_borehole(cx, cy, radius) -> float | None:
    """
    Compute the slip surface depth (ft below crest) at BOREHOLE_X.
    Returns None if the slip circle does not reach the borehole.
    """
    if None in (cx, cy, radius):
        return None
    dx = BOREHOLE_X - cx
    if abs(dx) > radius:
        return None  # slip circle does not pass through borehole X
    slip_y = cy - np.sqrt(max(0.0, radius**2 - dx**2))
    return CREST_Y - slip_y


# ---------------------------------------------------------------------------
# Inclinometer parsing - new @ delimited format
# ---------------------------------------------------------------------------
def _parse_incl_file(filepath: str) -> pd.DataFrame | None:
    """
    Parse a single inclinometer CSV with @ as A/B section delimiter.
    Returns A-direction DataFrame with columns: depth_ft, <date>, <date>, ...
    Skips the blank depth=1 spacer row, targets dates parseable by pandas.
    """
    rows   = []
    dates  = None

    try:
        with open(filepath, encoding="utf-8-sig") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"  WARNING: could not read {filepath}: {e}")
        return None

    for i, line in enumerate(lines):
        # Left of @ = A-direction
        left = line.rstrip("\n").split("@")[0].split(",")

        if i == 2:  # date header row
            dates = []
            for c in left[1:]:
                c = c.strip()
                if not c:
                    continue
                try:
                    pd.to_datetime(c)
                    dates.append(c)
                except Exception:
                    pass
            continue

        if i < 3 or dates is None:
            continue

        # Parse depth - skip blank spacer rows
        try:
            depth = float(left[0].strip())
        except (ValueError, IndexError):
            continue

        vals = []
        for v in left[1: len(dates) + 1]:
            try:
                vals.append(float(v.strip()))
            except (ValueError, AttributeError):
                vals.append(np.nan)

        # Pad if row is shorter than header
        while len(vals) < len(dates):
            vals.append(np.nan)

        rows.append([depth] + vals)

    if not rows or dates is None:
        return None

    df = pd.DataFrame(rows, columns=["depth_ft"] + dates)

    # Drop rows where ALL displacement values are NaN (blank spacer rows)
    disp_cols = [c for c in df.columns if c != "depth_ft"]
    df = df.dropna(subset=disp_cols, how="all").reset_index(drop=True)

    return df


def _shear_depth_from_profile(df: pd.DataFrame, target_date: str) -> float | None:
    """
    Find the target_date column (or nearest available date) and return the
    deepest contiguous shear zone depth (ft).

    Method: walk shallow-to-deep; track the deepest point in the first
    continuous band where abs(displacement) > DISP_THRESH.
    """
    # Find best matching date column
    date_cols = [c for c in df.columns if c != "depth_ft"]
    if not date_cols:
        return None

    try:
        target_dt = pd.to_datetime(target_date)
        col_dts   = pd.to_datetime(date_cols)
        idx       = (col_dts - target_dt).abs().argmin()
        best_col  = date_cols[idx]
        delta_days = abs((col_dts[idx] - target_dt).days)
        if delta_days > 0:
            print(f"    Note: no exact match for {target_date}, "
                  f"using '{best_col}' ({delta_days} days away)")
    except Exception:
        best_col = date_cols[-1]
        print(f"    WARNING: could not match date, using last column '{best_col}'")

    profile = []
    for _, row in df.iterrows():
        try:
            d    = float(row["depth_ft"])
            disp = abs(float(row[best_col]))
            if np.isfinite(disp):
                profile.append((d, disp))
        except (ValueError, TypeError):
            continue

    if not profile:
        return None

    profile.sort(key=lambda x: x[0])  # shallow -> deep

    in_shear    = False
    shear_depth = None
    for d_ft, disp in profile:
        if disp > DISP_THRESH:
            in_shear    = True
            shear_depth = d_ft      # keeps updating = deepest point in zone
        elif in_shear:
            break                   # left the shear zone

    return shear_depth


def load_inclinometer() -> float | None:
    """
    Load both inclinometer files and return the observed shear zone depth (ft)
    at the calibration failure event date (CALIB_DATE).

    Y1 = cross-slope direction (primary for slip surface depth).
    Y2 = slope-parallel (secondary confirmation).
    Uses the deeper of the two readings as the conservative target.
    """
    shear_depths: list[float] = []

    for fpath, label in [(INCL_Y1, "Y1"), (INCL_Y2, "Y2")]:
        if not os.path.exists(fpath):
            print(f"  WARNING: {label} not found: {fpath}")
            continue

        df = _parse_incl_file(fpath)
        if df is None:
            print(f"  WARNING: could not parse {label}")
            continue

        depth = _shear_depth_from_profile(df, CALIB_DATE)
        if depth is None:
            print(f"  WARNING: no shear zone detected in {label}")
            continue

        shear_y = CREST_Y - depth
        print(f"  {label}: shear depth = {depth:.1f} ft  "
              f"(model Y = {shear_y:.1f} ft)")
        shear_depths.append(depth)

    if not shear_depths:
        return None

    # Use the deeper (more conservative) reading as the calibration target
    obs_depth = max(shear_depths)
    print(f"  -> Calibration target depth : {obs_depth:.1f} ft  "
          f"(model Y = {CREST_Y - obs_depth:.1f} ft)")
    return obs_depth


# ---------------------------------------------------------------------------
# Objective function - dual cost: FS residual + depth residual
# ---------------------------------------------------------------------------
_iter_count = [0]
_trial_log:  list[dict] = []


def objective(params: np.ndarray, obs_depth: float | None) -> float:
    c_awyc, phi_awyc, c_wyc, phi_wyc = (
        float(np.clip(params[0], C_AWYC_MIN,   C_AWYC_MAX)),
        float(np.clip(params[1], PHI_AWYC_MIN, PHI_AWYC_MAX)),
        float(np.clip(params[2], C_WYC_MIN,    C_WYC_MAX)),
        float(np.clip(params[3], PHI_WYC_MIN,  PHI_WYC_MAX)),
    )

    _iter_count[0] += 1
    idx = _iter_count[0]
    print(f"  Trial {idx:3d}: "
          f"c_AWYC={c_awyc:6.1f}  phi_AWYC={phi_awyc:5.2f}  "
          f"c_WYC={c_wyc:6.1f}  phi_WYC={phi_wyc:5.2f}", end="", flush=True)

    project = None
    try:
        shutil.copy2(BASE_GSZ, WORK_GSZ)
        project = gsi.OpenProject(WORK_GSZ)

        for analysis in ANALYSES_ALL:
            try:
                _set_strength(project, analysis, c_awyc, phi_awyc, c_wyc, phi_wyc)
            except Exception:
                pass

        solve_resp = project.SolveAnalyses(gsi.SolveAnalysesRequest(
            analyses=[SLOPE_FS], solve_dependencies=True,
        ))
        if not solve_resp.all_succeeded:
            print("  -> FAILED")
            return 9999.0

        fs = _get_critical_fs(project)
        if fs is None:
            print("  -> FS unreadable")
            return 9999.0

        # --- FS cost term ---
        cost_fs = W_FS * (fs - FS_TARGET) ** 2

        # --- Depth cost term (only if inclinometer data available) ---
        cost_depth = 0.0
        model_depth_str = "N/A"
        if obs_depth is not None:
            cx, cy, radius = _get_slip_geometry(project)
            model_depth    = _slip_depth_at_borehole(cx, cy, radius)
            if model_depth is not None:
                # Normalised by obs_depth so the term is dimensionless
                cost_depth      = W_DEPTH * ((model_depth - obs_depth) / obs_depth) ** 2
                model_depth_str = f"{model_depth:.1f}ft"
            else:
                # Slip circle doesn't reach borehole - penalise
                cost_depth      = W_DEPTH * 1.0
                model_depth_str = "no_reach"

        cost = cost_fs + cost_depth

        print(f"  -> FS={fs:.4f}  depth={model_depth_str}  "
              f"cost_fs={cost_fs:.5f}  cost_d={cost_depth:.5f}  total={cost:.5f}")

        _trial_log.append({
            "trial":        idx,
            "c_awyc":       round(c_awyc, 2),
            "phi_awyc":     round(phi_awyc, 3),
            "c_wyc":        round(c_wyc, 2),
            "phi_wyc":      round(phi_wyc, 3),
            "fs":           round(fs, 5),
            "model_depth":  model_depth_str,
            "cost_fs":      round(cost_fs, 6),
            "cost_depth":   round(cost_depth, 6),
            "cost_total":   round(cost, 6),
        })
        return cost

    except Exception as e:
        print(f"  -> EXCEPTION: {e}")
        _trial_log.append({
            "trial": idx, "c_awyc": c_awyc, "phi_awyc": phi_awyc,
            "c_wyc": c_wyc, "phi_wyc": phi_wyc,
            "fs": 9999, "model_depth": "err",
            "cost_fs": 9999, "cost_depth": 9999, "cost_total": 9999,
        })
        return 9999.0

    finally:
        if project is not None:
            project.Close()
        if os.path.exists(WORK_GSZ):
            try:
                os.remove(WORK_GSZ)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("SLOPE/W Dual-Objective Calibration (FS + Inclinometer Depth)")
    print(f"  Base GSZ     : {os.path.basename(BASE_GSZ)}")
    print(f"  Calib date   : {CALIB_DATE}")
    print(f"  FS target    : {FS_TARGET}")
    print(f"  W_FS         : {W_FS}   W_DEPTH : {W_DEPTH}")
    print(f"  Max trials   : {MAX_OPT_ITER}")
    print("=" * 70)

    _require_gsi()

    if not os.path.exists(BASE_GSZ):
        print(f"\nERROR: Base GSZ not found: {BASE_GSZ}")
        print("Run calibrate_seep.py first to produce Metro-Center-seep-final.gsz")
        sys.exit(1)

    # Load inclinometer - shear depth at calibration event
    print(f"\nLoading inclinometer data (target: {CALIB_DATE})...")
    obs_depth = load_inclinometer()
    if obs_depth is None:
        print("  WARNING: no inclinometer data - running FS-only optimisation.")
    else:
        print(f"  Observed shear depth used in optimisation: {obs_depth:.1f} ft")

    # Initial parameters (forensic study baseline)
    x0 = np.array([C_AWYC_BASE, PHI_AWYC_BASE, C_WYC_BASE, PHI_WYC_BASE])
    print(f"\nStarting parameters:")
    print(f"  c_AWYC   = {x0[0]:.1f} psf    bounds [{C_AWYC_MIN},  {C_AWYC_MAX}]")
    print(f"  phi_AWYC = {x0[1]:.2f} deg   bounds [{PHI_AWYC_MIN}, {PHI_AWYC_MAX}]")
    print(f"  c_WYC    = {x0[2]:.1f} psf   bounds [{C_WYC_MIN},  {C_WYC_MAX}]")
    print(f"  phi_WYC  = {x0[3]:.2f} deg   bounds [{PHI_WYC_MIN}, {PHI_WYC_MAX}]")
    print(f"\nRunning optimisation (max {MAX_OPT_ITER} trials)...\n")

    result = minimize(
        objective, x0,
        args=(obs_depth,),
        method="Nelder-Mead",
        options={
            "maxiter": MAX_OPT_ITER,
            "xatol":   0.5,
            "fatol":   1e-4,
            "disp":    True,
        },
    )

    # Best trial from log - lowest total cost with valid FS
    valid = [t for t in _trial_log
             if isinstance(t["fs"], float) and t["fs"] < 900]
    if valid:
        best = min(valid, key=lambda t: t["cost_total"])
    else:
        best = {
            "c_awyc":     result.x[0], "phi_awyc": result.x[1],
            "c_wyc":      result.x[2], "phi_wyc":  result.x[3],
            "fs":         None,        "model_depth": None,
            "cost_fs":    None,        "cost_depth":  None,
        }

    # Print summary table
    print(f"\n{'=' * 70}")
    print("Optimisation complete")
    print(f"  Status : {result.message}   Trials: {result.nit}")
    if best["fs"] is not None:
        print(f"  Best FS        : {best['fs']:.4f}  |FS-1.0| = {abs(best['fs'] - FS_TARGET):.4f}")
    if obs_depth is not None:
        print(f"  Observed depth : {obs_depth:.1f} ft")
        md = best.get("model_depth")
        if md not in (None, "N/A", "err", "no_reach"):
            try:
                print(f"  Model depth    : {float(md):.1f} ft  "
                      f"(error = {float(md) - obs_depth:+.1f} ft)")
            except ValueError:
                print(f"  Model depth    : {md}")
    print()
    print(f"  {'Parameter':<22} {'Baseline':>12}  {'Calibrated':>12}  {'Change':>10}")
    print(f"  {'-' * 62}")
    for lbl, base, cal in [
        ("c_AWYC (psf)",   C_AWYC_BASE,  best["c_awyc"]),
        ("phi_AWYC (deg)", PHI_AWYC_BASE, best["phi_awyc"]),
        ("c_WYC (psf)",    C_WYC_BASE,   best["c_wyc"]),
        ("phi_WYC (deg)",  PHI_WYC_BASE,  best["phi_wyc"]),
    ]:
        print(f"  {lbl:<22} {base:>12.2f}  {cal:>12.2f}  {cal - base:>+10.2f}")

    # Save trial log
    if _trial_log:
        pd.DataFrame(_trial_log).to_csv(LOG_CSV, index=False)
        print(f"\nTrial log -> {LOG_CSV}")

    # Final solve with best params -> OUT_GSZ
    print(f"\nRunning final solve -> {os.path.basename(OUT_GSZ)} ...")
    shutil.copy2(BASE_GSZ, OUT_GSZ)
    project = gsi.OpenProject(OUT_GSZ)
    try:
        for analysis in ANALYSES_ALL:
            try:
                _set_strength(project, analysis,
                              best["c_awyc"], best["phi_awyc"],
                              best["c_wyc"],  best["phi_wyc"])
            except Exception:
                pass

        project.SolveAnalyses(gsi.SolveAnalysesRequest(
            analyses=[SLOPE_FS], solve_dependencies=True,
        ))

        final_fs = _get_critical_fs(project)
        cx, cy, radius = _get_slip_geometry(project)
        final_depth    = _slip_depth_at_borehole(cx, cy, radius)

        print(f"  Final FS      : {final_fs:.4f}" if final_fs else "  FS unreadable")
        if final_depth is not None and obs_depth is not None:
            print(f"  Final depth   : {final_depth:.1f} ft  "
                  f"(inclinometer = {obs_depth:.1f} ft, "
                  f"error = {final_depth - obs_depth:+.1f} ft)")
        elif final_depth is not None:
            print(f"  Final depth   : {final_depth:.1f} ft")

    finally:
        project.Close()

    print(f"\nDone.  Final GSZ -> {OUT_GSZ}")


if __name__ == "__main__":
    main()