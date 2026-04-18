import argparse
import csv
import io
import json
import math
import os
import re
import struct
import zipfile
from collections import Counter, defaultdict

import pandas as pd
import xml.etree.ElementTree as ET


NOT_STORED = "not stored in GSZ"


def _to_float(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _parse_fn_arg(expr, key):
    if not expr:
        return None
    m = re.search(rf"{re.escape(key)}=([^,\)]+)", expr)
    return m.group(1) if m else None


def _read_root_xml(z):
    roots = [n for n in z.namelist() if n.endswith(".xml") and "/" not in n]
    if not roots:
        raise RuntimeError("No root XML in archive")
    root_key = roots[0]
    root = ET.fromstring(z.read(root_key).decode("utf-8", errors="ignore"))
    return root_key, root


def _derive_geometry_id(gsz_path, names, root):
    m = re.search(r"H(\d+)", os.path.basename(gsz_path), re.IGNORECASE)
    if m:
        return f"H{m.group(1)}"
    for n in names:
        m = re.search(r"H(\d+)", n, re.IGNORECASE)
        if m:
            return f"H{m.group(1)}"
    txt = ET.tostring(root, encoding="unicode")
    m = re.search(r"H(\d+)", txt, re.IGNORECASE)
    if m:
        return f"H{m.group(1)}"
    return "UNKNOWN"


def _derive_run_id(gsz_path, explicit=None):
    if explicit:
        return explicit
    b = os.path.basename(gsz_path)
    m = re.search(r"(rain[_-]?\d+)", b, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    m = re.search(r"(\d+)", b)
    return f"run_{m.group(1)}" if m else os.path.splitext(b)[0]


def _parse_mesh_ply(raw):
    m = re.search(br"end_header\r?\n", raw)
    if not m:
        raise RuntimeError("PLY header end not found")
    header = raw[:m.end()].decode("utf-8", errors="ignore").splitlines()
    counts = {}
    for ln in header:
        if ln.startswith("element "):
            _, nm, ct = ln.split()
            counts[nm] = int(ct)

    off = m.end()
    # version: ushort major/minor
    off += 4

    nodes = []
    for i in range(counts.get("node", 0)):
        x, y, z = struct.unpack_from("<ddd", raw, off)
        off += 24
        nodes.append((i + 1, x, y, z))

    elements = []
    for i in range(counts.get("element", 0)):
        shape_kind, integration_order, category = struct.unpack_from("<BBB", raw, off)
        off += 3
        owner = struct.unpack_from("<I", raw, off)[0]
        off += 4
        n = struct.unpack_from("<B", raw, off)[0]
        off += 1
        ids = list(struct.unpack_from("<" + "I" * n, raw, off))
        off += 4 * n
        elements.append(
            {
                "element_id_raw": i + 1,
                "shape_kind": int(shape_kind),
                "integration_order": int(integration_order),
                "category": int(category),
                "owner": int(owner),
                "node_ids": ids,
            }
        )
    return nodes, elements


def _element_type(node_ids):
    n = len(node_ids)
    if n == 3:
        return "triangle_3"
    if n == 4:
        return "quad_4"
    if n == 6:
        return "triangle_6"
    if n == 8:
        return "quad_8"
    return f"poly_{n}"


def _parse_contexts(root):
    contexts = {}
    for ctx in root.findall(".//Context"):
        aid = ctx.findtext("AnalysisID")
        if not aid:
            continue
        info = {"region_to_material": {}, "line_to_bc": {}}
        gm = ctx.find("GeometryUsesMaterials")
        if gm is not None:
            for x in gm.findall("GeometryUsesMaterial"):
                rid = x.attrib.get("ID", "")
                rid = int(rid.split("-")[-1]) if "-" in rid else None
                mid = _to_float(x.attrib.get("Entry"))
                if rid is not None and mid is not None:
                    info["region_to_material"][rid] = int(mid)
        gh = ctx.find("GeometryUsesHydraulicBCs")
        if gh is not None:
            for x in gh.findall("GeometryUsesHydraulicBC"):
                lid = x.attrib.get("ID", "")
                lid = int(lid.split("-")[-1]) if "-" in lid else None
                bid = _to_float(x.attrib.get("Entry"))
                if lid is not None and bid is not None:
                    info["line_to_bc"][lid] = int(bid)
        contexts[str(aid)] = info
    return contexts


def _parse_materials(root):
    mats = {}
    for m in root.findall(".//Materials/Material"):
        mid = int(m.findtext("ID"))
        mats[mid] = m
    return mats


def _parse_functions(root):
    kfns = {}
    for k in root.findall(".//KFn"):
        kid = int(k.findtext("ID"))
        pts = []
        for p in k.findall("./Points/Point"):
            pts.append((_to_float(p.attrib.get("X")), _to_float(p.attrib.get("Y"))))
        kfns[kid] = {
            "id": kid,
            "name": k.findtext("Name"),
            "function": k.findtext("Function"),
            "estimate": k.findtext("Estimate"),
            "points": pts,
        }

    vw = {}
    for v in root.findall(".//VolWCFn"):
        vid = int(v.findtext("ID"))
        pts = []
        for p in v.findall("./Points/Point"):
            pts.append((_to_float(p.attrib.get("X")), _to_float(p.attrib.get("Y"))))
        vw[vid] = {
            "id": vid,
            "name": v.findtext("Name"),
            "function": v.findtext("Function"),
            "estimate": v.findtext("Estimate"),
            "points": pts,
        }
    return kfns, vw


def _parse_analyses(root):
    rows = []
    by_id = {}
    for a in root.findall(".//Analysis"):
        aid = str(a.findtext("ID", ""))
        if not aid:
            continue
        ti = a.find("TimeIncrements")
        ts = []
        if ti is not None:
            for t in ti.findall("./TimeSteps/TimeStep"):
                ts.append(
                    {
                        "step": _to_float(t.attrib.get("Step")),
                        "elapsed": _to_float(t.attrib.get("ElapsedTime")),
                        "save": t.attrib.get("Save"),
                    }
                )
        row = {
            "analysis_id": aid,
            "analysis_name": a.findtext("Name"),
            "analysis_type": a.findtext("Kind"),
            "method": a.findtext("Method"),
            "parent_analysis_id": a.findtext("ParentID"),
            "start_time": ti.findtext("Start") if ti is not None else None,
            "end_time": None,
            "time_step_scheme": ti.findtext("IncrementOption") if ti is not None else None,
            "num_timesteps": len(ts),
            "metadata_json": json.dumps(
                {
                    "duration": ti.findtext("Duration") if ti is not None else None,
                    "increment_count": ti.findtext("IncrementCount") if ti is not None else None,
                    "time_steps": ts,
                }
            ),
        }
        dur = _to_float(ti.findtext("Duration") if ti is not None else None)
        st = _to_float(row["start_time"])
        if dur is not None:
            row["end_time"] = (st or 0.0) + dur
        rows.append(row)
        by_id[aid] = row
    return rows, by_id


def _parse_stability_items(root):
    out = {}
    for it in root.findall(".//StabilityItem"):
        aid = it.findtext("AnalysisID")
        if not aid:
            continue
        obj = {}
        ss = it.find("./Entry/SlipSurface")
        if ss is not None:
            grid = ss.find("Grid")
            ee = ss.find("EntryExit")
            rad = ss.find("Radius")
            if grid is not None:
                obj["grid"] = dict(grid.attrib)
            if ee is not None:
                obj["entry_exit"] = dict(ee.attrib)
            if rad is not None:
                obj["radius"] = dict(rad.attrib)
        out[aid] = obj
    return out


def _read_csv_in_zip(z, path):
    txt = z.read(path).decode("utf-8", errors="ignore")
    return list(csv.DictReader(io.StringIO(txt)))


def extract_one(gsz_path, out_dir, geometry_id_override=None, run_id_override=None):
    os.makedirs(out_dir, exist_ok=True)
    audit = {"archive_path": gsz_path, "n_analyses": 0, "analyses": {}, "materials_with_missing_fields": {}}

    with zipfile.ZipFile(gsz_path, "r") as z:
        names = z.namelist()
        root_xml_key, root = _read_root_xml(z)
        geometry_id = geometry_id_override or _derive_geometry_id(gsz_path, names, root)
        run_id = _derive_run_id(gsz_path, explicit=run_id_override)

        # Mesh
        mesh_key = "mesh_3.ply" if "mesh_3.ply" in names else next((n for n in names if n.endswith("Mesh.ply")), None)
        if mesh_key is None:
            raise RuntimeError("No mesh ply in archive")
        nodes_xyz, raw_elements = _parse_mesh_ply(z.read(mesh_key))

        fe_elements = [e for e in raw_elements if e["category"] == 0 and len(e["node_ids"]) >= 3]
        node_regions = defaultdict(list)
        for e in fe_elements:
            for nid in e["node_ids"]:
                node_regions[nid].append(e["owner"])

        nodes_rows = []
        for nid, x, y, _z in nodes_xyz:
            rs = node_regions.get(nid, [])
            rid = Counter(rs).most_common(1)[0][0] if rs else None
            nodes_rows.append(
                {
                    "geometry_id": geometry_id,
                    "run_id": run_id,
                    "node_id": nid,
                    "x_ft": x,
                    "y_ft": y,
                    "region_id": rid,
                }
            )

        elem_rows = []
        for e in fe_elements:
            elem_rows.append(
                {
                    "geometry_id": geometry_id,
                    "run_id": run_id,
                    "element_id": e["element_id_raw"],
                    "node_ids": json.dumps(e["node_ids"]),
                    "region_id": e["owner"],
                    "element_type": _element_type(e["node_ids"]),
                }
            )

        # Geometry polygons
        pts = {}
        for p in root.findall(".//Geometry/Points/Point"):
            pid = int(p.attrib.get("ID"))
            pts[pid] = (_to_float(p.attrib.get("X")), _to_float(p.attrib.get("Y")))

        region_rows = []
        for r in root.findall(".//Geometry/Regions/Region"):
            rid = int(r.findtext("ID"))
            pids = [int(x.strip()) for x in r.findtext("PointIDs", "").split(",") if x.strip()]
            poly = [pts.get(pid) for pid in pids if pid in pts]
            region_rows.append(
                {
                    "geometry_id": geometry_id,
                    "run_id": run_id,
                    "region_id": rid,
                    "region_name": f"Region-{rid}",
                    "polygon_vertices_json": json.dumps(poly),
                }
            )

        # Analyses + metadata
        analyses_rows, analyses_by_id = _parse_analyses(root)
        stability_by_aid = _parse_stability_items(root)
        for r in analyses_rows:
            r["geometry_id"] = geometry_id
            r["run_id"] = run_id
            meta = json.loads(r["metadata_json"])
            if r["analysis_id"] in stability_by_aid:
                meta["search_settings"] = stability_by_aid[r["analysis_id"]]
            r["metadata_json"] = json.dumps(meta)
        audit["n_analyses"] = len(analyses_rows)

        # Context / materials / BCs
        contexts = _parse_contexts(root)
        mats = _parse_materials(root)
        kfns, vw_fns = _parse_functions(root)

        # BC definitions + climate fn names
        bc_defs = {}
        for bc in root.findall(".//BCs/BC"):
            bid = int(bc.attrib.get("ID"))
            bc_defs[bid] = dict(bc.attrib)
        clim_name = {}
        for cf in root.findall(".//Boundary/ClimateFns/ClimateFn"):
            cid = int(cf.findtext("ID"))
            clim_name[cid] = cf.findtext("Name")

        bc_rows = []
        for aid, info in contexts.items():
            for line_id, bc_id in info["line_to_bc"].items():
                b = bc_defs.get(bc_id, {})
                hyd = b.get("Hydraulic")
                bc_type = hyd.split("(")[0] if hyd else "unknown"
                val = _parse_fn_arg(hyd, "Value")
                fn_ref = None
                if hyd and "FnNum" in hyd:
                    refs = {}
                    for key in ["AirTemperatureFnNum", "PrecipitationFnNum", "RelHumidityFnNum", "WindSpeedFnNum", "NetRadiationFnNum"]:
                        v = _parse_fn_arg(hyd, key)
                        if v is not None:
                            refs[key] = {"id": int(float(v)), "name": clim_name.get(int(float(v)))}
                    fn_ref = json.dumps(refs) if refs else None
                bc_rows.append(
                    {
                        "geometry_id": geometry_id,
                        "run_id": run_id,
                        "analysis_id": aid,
                        "target_type": "line",
                        "target_id": line_id,
                        "bc_type": bc_type,
                        "value": val,
                        "function_reference": fn_ref,
                    }
                )

        # Material extraction for SEEP contexts (prefer transient analysis ID 3)
        seep_ctx = contexts.get("3") or next(
            (contexts.get(aid) for aid, a in analyses_by_id.items() if a["analysis_type"] == "SEEP/W" and contexts.get(aid)),
            None,
        )
        slope_ctx = contexts.get("1") or contexts.get("4")

        mat_rows = []
        swcc_rows = []
        if seep_ctx:
            for rid, mid in sorted(seep_ctx["region_to_material"].items()):
                m = mats.get(mid)
                if m is None:
                    continue
                mname = m.findtext("Name")
                hyd = m.find("Hydraulic")
                hyd_attr = dict(hyd.attrib) if hyd is not None else {}

                k_fn_num = int(hyd_attr["KFnNum"]) if "KFnNum" in hyd_attr else None
                vol_fn_num = int(hyd_attr["VolWCFnNum"]) if "VolWCFnNum" in hyd_attr else None
                ksat = _to_float(hyd_attr.get("KSat"))
                if ksat is None and k_fn_num in kfns:
                    est = kfns[k_fn_num]["estimate"] or ""
                    ksat = _to_float(_parse_fn_arg(est, "HydKSat"))
                    if ksat is None and kfns[k_fn_num]["points"]:
                        ksat = kfns[k_fn_num]["points"][0][1]

                k_ratio = _to_float(hyd_attr.get("KYXRatio"))
                if k_ratio is None:
                    k_ratio = 1.0
                k_angle = _to_float(hyd_attr.get("KAngle"))
                kx = ksat
                ky = (ksat * k_ratio) if (ksat is not None and k_ratio is not None) else None

                theta_s = None
                theta_r = None
                swcc_type = "data_points" if vol_fn_num in vw_fns else "not_stored"
                if vol_fn_num in vw_fns:
                    est = vw_fns[vol_fn_num]["estimate"] or ""
                    theta_s = _to_float(_parse_fn_arg(est, "SatWC"))
                    if theta_s is None and vw_fns[vol_fn_num]["points"]:
                        theta_s = vw_fns[vol_fn_num]["points"][0][1]
                    for x, y in vw_fns[vol_fn_num]["points"]:
                        swcc_rows.append(
                            {
                                "geometry_id": geometry_id,
                                "run_id": run_id,
                                "region_id": rid,
                                "psi_kpa": x,
                                "theta": y,
                            }
                        )
                if k_fn_num in kfns:
                    est = kfns[k_fn_num]["estimate"] or ""
                    theta_r = _to_float(_parse_fn_arg(est, "ResWC"))

                unit_weight = m.findtext(".//UnitWeight")
                source_uw = "seep_material"
                if unit_weight is None and slope_ctx and rid in slope_ctx["region_to_material"]:
                    smid = slope_ctx["region_to_material"][rid]
                    smat = mats.get(smid)
                    if smat is not None:
                        unit_weight = smat.findtext(".//UnitWeight")
                        source_uw = "slope_material_same_region"
                phi_b = m.findtext(".//PhiB")

                params = [
                    ("material_id", mid, "", "material"),
                    ("material_name", mname, "", "material"),
                    ("swcc_type", swcc_type, "", "material"),
                    ("k_sat", ksat if ksat is not None else NOT_STORED, "m/s", "hydraulic"),
                    ("kx", kx if kx is not None else NOT_STORED, "m/s", "hydraulic"),
                    ("ky", ky if ky is not None else NOT_STORED, "m/s", "hydraulic"),
                    ("k_ratio", k_ratio if k_ratio is not None else NOT_STORED, "-", "hydraulic"),
                    ("k_angle", k_angle if k_angle is not None else NOT_STORED, "deg", "hydraulic"),
                    ("theta_s", theta_s if theta_s is not None else NOT_STORED, "-", "swcc"),
                    ("theta_r", theta_r if theta_r is not None else NOT_STORED, "-", "swcc"),
                    ("alpha", NOT_STORED, "1/kPa", "swcc"),
                    ("n", NOT_STORED, "-", "swcc"),
                    ("unit_weight", unit_weight if unit_weight is not None else NOT_STORED, "pcf", source_uw),
                    ("phi_b", phi_b if phi_b is not None else NOT_STORED, "deg", "seep_material"),
                    (
                        "k_function",
                        (kfns.get(k_fn_num, {}).get("function") if k_fn_num in kfns else NOT_STORED),
                        "",
                        "k_psi_function",
                    ),
                ]
                miss = [p for p, v, _u, _s in params if v == NOT_STORED]
                if miss:
                    audit["materials_with_missing_fields"][f"region_{rid}"] = miss
                for pname, pval, units, source in params:
                    mat_rows.append(
                        {
                            "geometry_id": geometry_id,
                            "run_id": run_id,
                            "region_id": rid,
                            "param_name": pname,
                            "value": pval,
                            "units": units,
                            "source": source,
                            "swcc_type": swcc_type,
                        }
                    )

        # Slip surfaces + slices
        slip_rows = []
        slice_rows = []
        fs_analysis = next((a for a in analyses_rows if a["analysis_name"] == "FS"), None)
        fs_method = fs_analysis["method"] if fs_analysis else "unknown"
        fs_aid = fs_analysis["analysis_id"] if fs_analysis else "4"

        fs_steps = sorted(
            {
                int(n.split("/")[1])
                for n in names
                if n.startswith("FS/") and n.endswith("/slip_surface.csv")
            }
        )

        for step in fs_steps:
            step_key = f"{step:03d}"
            slip_file = f"FS/{step_key}/slip_surface.csv"
            if slip_file not in names:
                continue
            rows = _read_csv_in_zip(z, slip_file)
            vals = []
            for r in rows:
                sf = _to_float(r.get("SlipFOS"))
                if sf is not None and sf > 0 and sf < 100:
                    vals.append((sf, r))
            if vals:
                _, crit = min(vals, key=lambda x: x[0])
                fos = _to_float(crit.get("SlipFOS"))
                cx = _to_float(crit.get("SlipCenterX"))
                cy = _to_float(crit.get("SlipCenterY"))
                rad = _to_float(crit.get("SlipRadiusX"))
            else:
                crit = None
                fos = cx = cy = rad = None

            # Optional lambda-based fallback/validation
            lmb_file = next((n for n in names if n.startswith(f"FS/{step_key}/lambdafos_") and n.endswith(".csv")), None)
            if lmb_file:
                lrows = _read_csv_in_zip(z, lmb_file)
                best = None
                for lr in lrows:
                    ff = _to_float(lr.get("FOSByForce"))
                    fm = _to_float(lr.get("FOSByMoment"))
                    if ff is None or fm is None:
                        continue
                    d = abs(ff - fm)
                    if best is None or d < best[0]:
                        best = (d, (ff + fm) / 2.0)
                if fos is None and best is not None:
                    fos = best[1]

            col_file = next((n for n in names if n.startswith(f"FS/{step_key}/column_") and n.endswith(".csv")), None)
            polyline = []
            ex = ey = xx = xy = None
            if col_file:
                crows = _read_csv_in_zip(z, col_file)
                if crows:
                    ex = _to_float(crows[0].get("XLeft"))
                    ey = _to_float(crows[0].get("YBackBotLeft"))
                    xx = _to_float(crows[-1].get("XRight"))
                    xy = _to_float(crows[-1].get("YBackBotRight"))
                for c in crows:
                    xl = _to_float(c.get("XLeft"))
                    yl = _to_float(c.get("YBackBotLeft"))
                    if xl is not None and yl is not None:
                        polyline.append([xl, yl])
                if crows:
                    xr = _to_float(crows[-1].get("XRight"))
                    yr = _to_float(crows[-1].get("YBackBotRight"))
                    if xr is not None and yr is not None:
                        polyline.append([xr, yr])

                for c in crows:
                    xleft = _to_float(c.get("XLeft"))
                    xright = _to_float(c.get("XRight"))
                    ybl = _to_float(c.get("YBackBotLeft"))
                    ybr = _to_float(c.get("YBackBotRight"))
                    base_len = None
                    if None not in (xleft, xright, ybl, ybr):
                        base_len = math.hypot(xright - xleft, ybr - ybl)
                    bot_alpha = _to_float(c.get("BotAlphaX"))
                    slice_rows.append(
                        {
                            "geometry_id": geometry_id,
                            "run_id": run_id,
                            "analysis_id": fs_aid,
                            "method": fs_method,
                            "step_idx": step,
                            "slice_id": int(float(c["ColumnNum"])),
                            "x_centroid": _to_float(c.get("X")),
                            "base_y": _to_float(c.get("Y")),
                            "width": _to_float(c.get("ColumnWidth")),
                            "base_length": base_len,
                            "weight": _to_float(c.get("ColumnWeight")),
                            "normal_force": _to_float(c.get("NormalEffectiveStress")),
                            "shear_force": _to_float(c.get("ActivatingForceX")),
                            "pore_pressure": _to_float(c.get("PoreWaterPressure")),
                            "base_angle_deg": (math.degrees(bot_alpha) if bot_alpha is not None else None),
                        }
                    )

            slip_rows.append(
                {
                    "geometry_id": geometry_id,
                    "run_id": run_id,
                    "analysis_id": fs_aid,
                    "step_idx": step,
                    "method": fs_method,
                    "fos": fos,
                    "center_x": cx,
                    "center_y": cy,
                    "radius": rad,
                    "entry_x": ex if ex is not None else NOT_STORED,
                    "entry_y": ey if ey is not None else NOT_STORED,
                    "exit_x": xx if xx is not None else NOT_STORED,
                    "exit_y": xy if xy is not None else NOT_STORED,
                    "polyline_json": json.dumps(polyline) if polyline else NOT_STORED,
                }
            )

        # Initial conditions (cheap add-on)
        ic_rows = []
        for key in ["Initial Condition/000/node.csv", "Initial Condition/001/node.csv"]:
            if key in names:
                for r in _read_csv_in_zip(z, key):
                    ic_rows.append(
                        {
                            "geometry_id": geometry_id,
                            "run_id": run_id,
                            "analysis_id": "2",
                            "initial_state": key.split("/")[-2],
                            "node_id": int(float(r["Node"])),
                            "pore_water_pressure": _to_float(r.get("PoreWaterPressure")),
                        }
                    )

        # Sigma/W constitutive: none in this file
        sigma_rows = []

        # Audit per-analysis
        for a in analyses_rows:
            aid = a["analysis_id"]
            ex = []
            missing = []
            errs = []
            if aid in ("2", "3"):
                ex.extend(["nodes", "elements", "materials"])
                if contexts.get(aid, {}).get("line_to_bc"):
                    ex.append("bcs")
                else:
                    missing.append("bcs")
            if aid in ("1", "4"):
                if slip_rows:
                    ex.extend(["slip_surfaces", "slip_slices"])
                else:
                    missing.extend(["slip_surfaces", "slip_slices"])
                if aid not in stability_by_aid:
                    missing.append("search_grid")
            audit["analyses"][aid] = {"analysis_name": a["analysis_name"], "extracted": ex, "missing": missing, "errors": errs}

    # Write parquet set
    frames = {
        "nodes.parquet": pd.DataFrame(nodes_rows),
        "elements.parquet": pd.DataFrame(elem_rows),
        "materials.parquet": pd.DataFrame(mat_rows),
        "swcc_points.parquet": pd.DataFrame(swcc_rows),
        "region_polygons.parquet": pd.DataFrame(region_rows),
        "analyses.parquet": pd.DataFrame(analyses_rows),
        "boundary_conditions.parquet": pd.DataFrame(bc_rows),
        "slip_surfaces.parquet": pd.DataFrame(slip_rows),
        "slip_slices.parquet": pd.DataFrame(slice_rows),
        "sigma_w_constitutive.parquet": pd.DataFrame(sigma_rows),
        "initial_conditions.parquet": pd.DataFrame(ic_rows),
    }
    for fname, df in frames.items():
        if not df.empty:
            for col in df.columns:
                if df[col].dtype == "object":
                    df[col] = df[col].where(df[col].isna(), df[col].astype(str))
        out_path = os.path.join(out_dir, fname)
        df.to_parquet(out_path, index=False)

    with open(os.path.join(out_dir, "audit.json"), "w", encoding="utf-8") as f:
        json.dump({geometry_id: audit}, f, indent=2)

    return geometry_id, run_id, {k: len(v) for k, v in frames.items()}


def main():
    ap = argparse.ArgumentParser(description="One-sweep GSZ extractor for one archive")
    ap.add_argument("--gsz", required=True, help="Path to .gsz")
    ap.add_argument("--out_dir", default="data/extracted", help="Output directory")
    ap.add_argument("--geometry_id", default=None, help="Optional geometry ID override (e.g., H15)")
    ap.add_argument("--run_id", default=None, help="Optional run ID override (e.g., rain_0075)")
    args = ap.parse_args()

    geom, run_id, counts = extract_one(
        args.gsz,
        args.out_dir,
        geometry_id_override=args.geometry_id,
        run_id_override=args.run_id,
    )
    print(f"Extracted geometry: {geom}")
    print(f"Run ID: {run_id}")
    for k, v in counts.items():
        print(f"{k}: {v} rows")
    print(f"audit: {os.path.join(args.out_dir, 'audit.json')}")


if __name__ == "__main__":
    main()
