"""
SLOPE/W Strength Calibration with Inclinometer Validation
==========================================================
Calibrates c' and phi' for AWYC and WYC against the slope failure,
using pore pressures from the calibrated SEEP/W model (BASE_GSZ).

Objective: minimise (FS - 1.0)^2 at the end of the rainfall event.

After calibration, the script validates the calibrated slip surface
against the inclinometer-observed shear zone depth. This validation
is separate from the optimisation — it checks whether the geometry
of the best-fit slip surface is physically consistent with field data.

Requires
--------
    pip install openpyxl scipy numpy pandas grpc gsi
    GeoStudio 2025.2 background service running

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
BASE_GSZ    = os.path.join(CALIB_DIR, "Metro-Center-seep-final.gsz")   # output of calibrate_seep.py
WORK_GSZ    = os.path.join(CALIB_DIR, "_slope_work.gsz")
OUT_GSZ     = os.path.join(CALIB_DIR, "Metro-Center-slope-final.gsz")
LOG_CSV     = os.path.join(CALIB_DIR, "slope_calibration_trial_log.csv")
SENSOR_CSV  = os.path.join(CALIB_DIR, "S2.csv")

INCL_Y1     = os.path.join(CALIB_DIR, "S2_Inclinometer_Y1.csv")
INCL_Y2     = os.path.join(CALIB_DIR, "S2_Inclinometer_Y2.csv")

SEEP_INITIAL   = "Initial Condition"
SEEP_TRANSIENT = "Rainfall Simulation"
SLOPE_FS       = "FS"
ANALYSES_ALL   = [SEEP_INITIAL, SEEP_TRANSIENT, "Slope Stability", SLOPE_FS]

# Use the LAST available step (peak pore pressure = worst case).
# FS_STEP is a placeholder; overridden by _get_last_step() at runtime.
FS_STEP = 1  # fallback if QueryResultsAvailability is not supported

BOREHOLE_X  = 195.0    # ft — mid-crest platform (inclinometer location)
CREST_Y     = 83.0     # ft — crest surface elevation

# Baseline parameters (forensic study values)
C_AWYC_BASE,  PHI_AWYC_BASE = 79.3,  19.0
C_WYC_BASE,   PHI_WYC_BASE  = 248.5, 19.0

# Bounds
C_AWYC_MIN,  C_AWYC_MAX  =   0.0, 250.0
PHI_AWYC_MIN,PHI_AWYC_MAX=  10.0,  25.0
C_WYC_MIN,   C_WYC_MAX   =  50.0, 500.0
PHI_WYC_MIN, PHI_WYC_MAX =  12.0,  25.0

FS_TARGET    = 1.0
MAX_OPT_ITER = 60

DISP_THRESH  = 0.005   # inches — below this = no movement

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_gsi():
    if not _GSI_OK:
        raise RuntimeError(f"gsi not available: {_GSI_ERROR}")


def _set_strength(project, analysis: str, c_awyc:float, phi_awyc:float,
                  c_wyc:float, phi_wyc:float):
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
    Returns (cx, cy, radius) of the critical slip circle, or (None, None, None).
    cx/cy are the slip circle CENTRE, not the arc midpoint.
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


# ---------------------------------------------------------------------------
# Inclinometer parsing (from CSV files)
# ---------------------------------------------------------------------------
def load_inclinometer() -> float | None:
    """
    Parse the inclinometer CSV files and return the estimated shear zone
    depth (ft below the crest surface) using the reading closest to the
    calibration failure event.

    The CSV files are expected to have columns:
        Depth_ft, Date_1, Date_2, ...
    where each Date column contains cumulative A-direction displacement (inches).
    """
    shear_depths: list[float] = []

    for fpath, label in [(INCL_Y1, "Y1"), (INCL_Y2, "Y2")]:
        if not os.path.exists(fpath):
            print(f"  WARNING: {label} not found: {fpath}")
            continue

        try:
            df = pd.read_csv(fpath)
            depth_col = df.columns[0]
            date_cols = df.columns[1:]

            # Parse dates from column headers
            dates = {}
            for col in date_cols:
                try:
                    dt = pd.to_datetime(col)
                    dates[col] = dt
                except Exception:
                    pass

            if not dates:
                print(f"  WARNING: no date columns parseable in {label}")
                continue

            # Use most recent reading as representative (or pick by date)
            best_col = max(dates, key=lambda c: dates[c])
            print(f"  {label}: using column '{best_col}'")

            profile = []
            for _, row in df.iterrows():
                try:
                    d_ft  = float(row[depth_col])
                    disp  = abs(float(row[best_col]))
                    profile.append((d_ft, disp))
                except Exception:
                    continue

            if not profile:
                continue

            # Shear depth = the SHALLOWEST depth at which significant movement
            # first appears from the surface downward, then tracks the continuous
            # shear zone to its DEEPEST point.
            # Step 1: sort by increasing depth (shallow→deep)
            profile_sorted_asc = sorted(profile, key=lambda x: x[0])
            # Step 2: find the deepest contiguous moving zone
            in_shear = False
            shear_depth = None
            for d_ft, disp in profile_sorted_asc:
                if disp > DISP_THRESH:
                    in_shear = True
                    shear_depth = d_ft  # keeps updating = deepest point in zone
                elif in_shear:
                    break              # left the shear zone

            if shear_depth is None:
                shear_depth = max(d for d, _ in profile)

            print(f"  {label}: shear depth = {shear_depth:.1f} ft  "
                  f"(SHEAR_Y ≈ {CREST_Y - shear_depth:.1f} ft model)")
            shear_depths.append(shear_depth)

        except Exception as e:
            print(f"  WARNING: could not parse {label}: {e}")

    if not shear_depths:
        return None
    obs_depth = max(shear_depths)
    print(f"  Observed shear zone depth : {obs_depth:.1f} ft  "
          f"(SHEAR_Y = {CREST_Y - obs_depth:.1f} ft)")
    return obs_depth


def validate_slip_vs_inclinometer(cx, cy, radius, obs_depth):
    """
    Print comparison between the calibrated slip circle depth at the
    borehole X position and the inclinometer-measured shear depth.
    """
    if None in (cx, cy, radius, obs_depth):
        print("  [Validation] Insufficient geometry or inclinometer data.")
        return

    dx = BOREHOLE_X - cx
    if abs(dx) > radius:
        gap = abs(dx) - radius
        print(f"  [Validation] Slip circle does NOT reach borehole X={BOREHOLE_X}. "
              f"Lateral gap = {gap:.1f} ft. Consider widening search bounds.")
        return

    slip_y_at_borehole = cy - np.sqrt(max(0, radius**2 - dx**2))
    model_depth = CREST_Y - slip_y_at_borehole
    error_ft    = model_depth - obs_depth
    print(f"\n  === Inclinometer Validation ===")
    print(f"  Slip circle  : cx={cx:.1f} ft, cy={cy:.1f} ft, r={radius:.1f} ft")
    print(f"  Slip depth at X={BOREHOLE_X} : {model_depth:.1f} ft")
    print(f"  Inclinometer : {obs_depth:.1f} ft")
    print(f"  Error        : {error_ft:+.1f} ft  ({error_ft/obs_depth*100:+.0f}%)")
    if abs(error_ft) <= 2.0:
        print("  → GOOD: within ±2 ft of inclinometer")
    elif abs(error_ft) <= 5.0:
        print("  → ACCEPTABLE: within ±5 ft")
    else:
        print("  → POOR: >5 ft discrepancy — consider adjusting bounds or model geometry")


# ---------------------------------------------------------------------------
# Objective function
# ---------------------------------------------------------------------------
_iter_count = [0]
_trial_log:  list[dict] = []


def objective(params: np.ndarray) -> float:
    c_awyc, phi_awyc, c_wyc, phi_wyc = (
        float(np.clip(params[0], C_AWYC_MIN, C_AWYC_MAX)),
        float(np.clip(params[1], PHI_AWYC_MIN, PHI_AWYC_MAX)),
        float(np.clip(params[2], C_WYC_MIN, C_WYC_MAX)),
        float(np.clip(params[3], PHI_WYC_MIN, PHI_WYC_MAX)),
    )

    _iter_count[0] += 1
    idx = _iter_count[0]
    print(f"  Trial {idx:3d}: "
          f"c_AWYC={c_awyc:6.1f}  φ_AWYC={phi_awyc:5.2f}°  "
          f"c_WYC={c_wyc:6.1f}  φ_WYC={phi_wyc:5.2f}°", end="", flush=True)

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
            print("  →  FAILED")
            return 9999.0

        fs = _get_critical_fs(project)
        cost = (fs - FS_TARGET)**2 if fs is not None else 9999.0

        fs_str = f"{fs:.4f}" if fs is not None else "N/A"
        print(f"  →  FS={fs_str}  cost={cost:.5f}")

        _trial_log.append({
            "trial":    idx,
            "c_awyc":   round(c_awyc, 2),  "phi_awyc": round(phi_awyc, 3),
            "c_wyc":    round(c_wyc, 2),   "phi_wyc":  round(phi_wyc, 3),
            "fs":       round(fs, 5) if fs is not None else 9999,
            "cost":     round(cost, 6),
        })
        return cost

    except Exception as e:
        print(f"  →  EXCEPTION: {e}")
        _trial_log.append({
            "trial": idx, "c_awyc": c_awyc, "phi_awyc": phi_awyc,
            "c_wyc": c_wyc, "phi_wyc": phi_wyc, "fs": 9999, "cost": 9999,
        })
        return 9999.0

    finally:
        if project is not None:
            project.Close()
        if os.path.exists(WORK_GSZ):
            try: os.remove(WORK_GSZ)
            except: pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("SLOPE/W Strength Calibration with Inclinometer Validation (GSI API)")
    print(f"  Base GSZ    : {os.path.basename(BASE_GSZ)}")
    print(f"  FS target   : {FS_TARGET}")
    print(f"  Max trials  : {MAX_OPT_ITER}")
    print("=" * 70)

    _require_gsi()

    if not os.path.exists(BASE_GSZ):
        print(f"\nERROR: Base GSZ not found: {BASE_GSZ}")
        print("Run calibrate_seep.py first to produce Metro-Center-seep-final.gsz")
        sys.exit(1)

    # Load inclinometer data for post-calibration validation
    print("\nLoading inclinometer data for post-calibration validation...")
    obs_depth = load_inclinometer()
    if obs_depth is None:
        print("  WARNING: No inclinometer data — skipping geometric validation.")

    # Initial parameters
    x0 = np.array([C_AWYC_BASE, PHI_AWYC_BASE, C_WYC_BASE, PHI_WYC_BASE])
    print(f"\nStarting parameters (forensic study baseline):")
    print(f"  c_AWYC   = {x0[0]:.1f} psf   bounds [{C_AWYC_MIN}, {C_AWYC_MAX}]")
    print(f"  φ_AWYC   = {x0[1]:.2f}°     bounds [{PHI_AWYC_MIN}, {PHI_AWYC_MAX}]")
    print(f"  c_WYC    = {x0[2]:.1f} psf   bounds [{C_WYC_MIN}, {C_WYC_MAX}]")
    print(f"  φ_WYC    = {x0[3]:.2f}°     bounds [{PHI_WYC_MIN}, {PHI_WYC_MAX}]")
    print(f"\nRunning optimisation (max {MAX_OPT_ITER} trials)...\n")

    result = minimize(
        objective, x0, method="Nelder-Mead",
        options={"maxiter": MAX_OPT_ITER, "xatol": 0.5, "fatol": 1e-4, "disp": True},
    )

    # Best from log
    valid = [t for t in _trial_log if isinstance(t["fs"], float) and t["fs"] < 900]
    if valid:
        best = min(valid, key=lambda t: abs(t["fs"] - FS_TARGET))
    else:
        best = {
            "c_awyc": result.x[0], "phi_awyc": result.x[1],
            "c_wyc":  result.x[2], "phi_wyc":  result.x[3], "fs": None,
        }

    print(f"\n{'=' * 70}")
    print("Optimisation complete")
    print(f"  Status : {result.message}   Trials: {result.nit}")
    if best["fs"] is not None:
        print(f"  Best FS: {best['fs']:.4f}  |FS-1.0| = {abs(best['fs'] - FS_TARGET):.4f}")
    print()
    print(f"  {'Parameter':<22} {'Baseline':>12}  {'Calibrated':>12}  {'Change':>10}")
    print(f"  {'-'*62}")
    for lbl, base, cal in [
        ("c_AWYC (psf)",   C_AWYC_BASE,  best["c_awyc"]),
        ("φ_AWYC (°)",    PHI_AWYC_BASE, best["phi_awyc"]),
        ("c_WYC (psf)",    C_WYC_BASE,   best["c_wyc"]),
        ("φ_WYC (°)",     PHI_WYC_BASE,  best["phi_wyc"]),
    ]:
        print(f"  {lbl:<22} {base:>12.2f}  {cal:>12.2f}  {cal - base:>+10.2f}")

    # Save trial log
    if _trial_log:
        pd.DataFrame(_trial_log).to_csv(LOG_CSV, index=False)
        print(f"\nTrial log → {LOG_CSV}")

    # Final solve with best params → OUT_GSZ
    print(f"\nRunning final solve → {os.path.basename(OUT_GSZ)} ...")
    shutil.copy2(BASE_GSZ, OUT_GSZ)
    project = gsi.OpenProject(OUT_GSZ)
    cx = cy = radius = None
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
        print(f"  Final FS = {final_fs:.4f}" if final_fs else "  FS unreadable")
        cx, cy, radius = _get_slip_geometry(project)
    finally:
        project.Close()

    # Post-calibration inclinometer validation
    if obs_depth is not None:
        validate_slip_vs_inclinometer(cx, cy, radius, obs_depth)

    print(f"\nDone.  Final GSZ → {OUT_GSZ}")


if __name__ == "__main__":
    main()
