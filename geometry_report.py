#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
АГЕНТ ГЕОМЕТРІЇ: повний звіт звірки 3D (STEP) <-> DXF.

Для кожної папки зі STEP-складанням (.stp/.step) і DXF:
  - читає КОЖНЕ тіло STEP: реальна товщина листа (2*V/площа) + площа розгортки (V/t);
  - читає КОЖЕН DXF: товщина/матеріал/кількість з імені + площа контуру + габарит + отвори;
  - зіставляє тіло <-> DXF за товщиною+площею;
  - друкує повний звіт з назвами і площами та зберігає geometry_report.txt.

Залежності: cadquery (OpenCascade) + стандартний Python. Запуск:
    .venv312/bin/python geometry_report.py
"""
import io, os, glob, sys
from cadquery import importers
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.BRepTools import BRepTools

ROOT = os.path.dirname(os.path.abspath(__file__))


# ---------- DXF: площа контуру (POLYLINE/VERTEX + LWPOLYLINE + CIRCLE) ----------
def _entities(path):
    lines = io.open(path, encoding="latin-1", errors="ignore").read().splitlines()
    pairs, i = [], 0
    while i + 1 < len(lines):
        try: pairs.append((int(lines[i].strip()), lines[i + 1]))
        except ValueError: pass
        i += 2
    inent, ents, cur = False, [], None
    for c, v in pairs:
        if c == 2 and v.strip() == "ENTITIES": inent = True; continue
        if not inent: continue
        if c == 0:
            if cur: ents.append(cur)
            if v.strip() == "ENDSEC": break
            cur = (v.strip(), [])
        elif cur: cur[1].append((c, v))
    return ents


def _poly_area(pts):
    if len(pts) < 3: return 0.0
    a = sum(pts[k][0] * pts[(k + 1) % len(pts)][1] - pts[(k + 1) % len(pts)][0] * pts[k][1]
            for k in range(len(pts)))
    return abs(a) / 2


def dxf_info(path):
    ents = _entities(path)
    polys, circ, cp = [], [], None
    bbx, bby = [], []
    for name, codes in ents:
        xs = [float(v) for c, v in codes if c == 10]
        ys = [float(v) for c, v in codes if c == 20]
        bbx += xs; bby += ys
        if name == "POLYLINE": cp = []
        elif name == "VERTEX" and cp is not None:
            if xs and ys: cp.append((xs[0], ys[0]))
        elif name == "SEQEND" and cp is not None:
            polys.append(_poly_area(cp)); cp = None
        elif name == "LWPOLYLINE":
            polys.append(_poly_area(list(zip(xs, ys))))
        elif name == "CIRCLE":
            r = [float(v) for c, v in codes if c == 40]
            if r: circ.append(3.14159 * r[0] ** 2)
    area = None; inner = 0
    if polys:
        o = max(polys); inner = len([p for p in polys if p < o])
        area = o - (sum(p for p in polys if p < o) + sum(circ))
    w = (max(bbx) - min(bbx)) if bbx else 0
    h = (max(bby) - min(bby)) if bby else 0
    return {"area": area, "w": round(w, 1), "h": round(h, 1), "holes": len(circ) + inner}


def dxf_meta(name):
    import re
    th = None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*мм", name, re.I)
    if m: th = float(m.group(1).replace(",", "."))
    mat = None
    g = re.search(r"(Ст\.?\s?\d+|\d{2}[ГгҐ]\d[СсC]\w*|\d{2}[кК][пП])", name, re.I)
    if g: mat = re.sub(r"\s+", "", g.group(1))
    q = re.search(r"(\d+)\s*шт", name, re.I)
    qty = int(q.group(1)) if q else None
    return th, mat, qty


# ---------- STEP: тіла ----------
def solid_holes(s, t):
    """Справжні отвори: циліндр зі стінкою ~товщини листа і майже повним колом."""
    holes = {}
    for f in s.Faces():
        ad = BRepAdaptor_Surface(f.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            continue
        umin, umax, vmin, vmax = BRepTools.UVBounds_s(f.wrapped)
        if abs(vmax - vmin) <= t * 1.6 and abs(umax - umin) > 3.0:
            cyl = ad.Cylinder(); ax = cyl.Axis().Location()
            holes[(round(ax.X(), 1), round(ax.Y(), 1), round(ax.Z(), 1))] = round(cyl.Radius() * 2, 1)
    return sorted(holes.values())


def step_solids(path):
    r = importers.importStep(path)
    out = []
    for s in r.solids().vals():
        V, A = s.Volume(), s.Area()
        t = 2 * V / A
        bb = s.BoundingBox()
        out.append({"t": t, "area": V / t, "holes": solid_holes(s, t),
                    "bbox": tuple(sorted([round(bb.xlen, 1), round(bb.ylen, 1), round(bb.zlen, 1)]))})
    return out


def run(p):
    folders = [d for d in glob.glob(os.path.join(ROOT, "*")) if os.path.isdir(d)]
    any_step = False
    for folder in sorted(folders):
        SKIP = ("novatsia", "oldversions", "old versions", "backup", "_out_")
        steps = glob.glob(os.path.join(folder, "**", "*.stp"), recursive=True) + \
                glob.glob(os.path.join(folder, "**", "*.step"), recursive=True)
        steps = [s for s in steps if not any(k in s.lower() for k in SKIP)]
        dxfs = glob.glob(os.path.join(folder, "**", "*.dxf"), recursive=True)
        if not steps or not dxfs:
            continue
        any_step = True
        p("=" * 78 + "\n")
        p(f"ПАПКА: {os.path.basename(folder)}\n")
        p("=" * 78 + "\n")

        # DXF таблиця
        dlist = []
        for f in sorted(dxfs):
            n = os.path.basename(f); gi = dxf_info(f); th, mat, qty = dxf_meta(n)
            dlist.append({"name": n, "th": th, "mat": mat, "qty": qty, **gi})
        p("DXF-деталі:\n")
        p(f"  {'площа,мм²':>10} {'товщ':>5} {'мат':>6} {'к-сть':>5}  {'габарит':>14}  отв  назва\n")
        for d in dlist:
            p(f"  {('%.0f'%d['area']) if d['area'] else '?':>10} {str(d['th']):>5} "
              f"{str(d['mat']):>6} {str(d['qty']):>5}  {d['w']}x{d['h']:>0}  {d['holes']:>3}  {d['name']}\n")

        # STEP тіла
        for sp in steps:
            sols = step_solids(sp)
            p(f"\n3D-складання: {os.path.basename(sp)} — тіл: {len(sols)}\n")
            p(f"  {'тіло':>5} {'товщ':>5} {'площа,мм²':>10}  {'3D-габарит':>16}  ->  DXF (відхил площі)\n")
            for i, s in enumerate(sols):
                # найкращий DXF за товщиною+площею
                best, bd = None, 1e9
                for d in dlist:
                    if d["area"] is None: continue
                    if d["th"] and abs(d["th"] - round(s["t"])) > 1.0: continue
                    dd = abs(d["area"] - s["area"]) / s["area"]
                    if dd < bd: bd, best = dd, d
                bx = "x".join(str(x) for x in s["bbox"])
                mh = len(s["holes"])
                if best and bd <= 0.20:
                    flag = "OK" if bd < 0.05 else ("~ перевір" if bd < 0.10 else "!!РІЗНІ")
                    dh = best["holes"]
                    hole_note = "" if mh == dh else f"  !! ОТВОРИ: модель {mh} / DXF {dh}"
                    tgt = f"{best['name'][:34]} ({bd*100:.1f}% {flag}){hole_note}"
                else:
                    tgt = "— немає відповідного DXF (стороння деталь?)"
                p(f"  {i+1:>5} {s['t']:>5.2f} {s['area']:>10.0f} отв.{mh:>2}  {bx:>14}  ->  {tgt}\n")
        p("\n")
    if not any_step:
        p("Не знайдено папки, де поруч є і STEP (.stp/.step), і DXF.\n")


def main():
    buf = io.StringIO(); run(buf.write); text = buf.getvalue()
    try: print(text)
    except Exception: sys.stdout.write(text.encode("utf-8", "replace").decode("ascii", "replace"))
    with io.open(os.path.join(ROOT, "geometry_report.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print("Звіт збережено:", os.path.join(ROOT, "geometry_report.txt"))


if __name__ == "__main__":
    main()
