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
        p("\n" + "=" * 78 + "\n")
        p(f"  ПАПКА: {os.path.basename(folder)}\n")
        p("=" * 78 + "\n")

        dlist = []
        for f in sorted(dxfs):
            n = os.path.basename(f); gi = dxf_info(f); th, mat, qty = dxf_meta(n)
            dlist.append({"name": n, "th": th, "mat": mat, "qty": qty, **gi})

        problems = []      # рядки для підсумку
        matched_dxf = set()
        ok_n = 0; part_n = 0
        for sp in steps:
            for s in step_solids(sp):
                # найкращий DXF за товщиною+площею
                best, bd = None, 1e9
                for d in dlist:
                    if d["area"] is None: continue
                    if d["th"] and abs(d["th"] - round(s["t"])) > 1.0: continue
                    dd = abs(d["area"] - s["area"]) / s["area"]
                    if dd < bd: bd, best = dd, d
                if not best or bd > 0.20:
                    continue  # стороння деталь складання — пропускаємо
                part_n += 1
                matched_dxf.add(best["name"])
                mh, dh = len(s["holes"]), best["holes"]
                short = best["name"]
                # вердикти
                area_ok = bd < 0.05
                hole_ok = (mh == dh)
                th_dxf = best["th"]
                th_ok = (th_dxf is None) or abs(round(s["t"]) - th_dxf) < 0.6
                issues = []
                if not th_ok: issues.append(f"товщина: модель {s['t']:.1f} / DXF {th_dxf}")
                if bd >= 0.10: issues.append(f"площа розходиться на {bd*100:.0f}%")
                if not hole_ok: issues.append(f"отвори: модель {mh} / DXF {dh}")

                if issues:
                    mark = "[!!]"
                elif bd >= 0.05:
                    mark = "[~]"; ok_n += 1     # погранично, але не помилка
                else:
                    mark = "[OK]"; ok_n += 1
                p(f"\n{mark} {short}\n")
                p(f"      товщина : модель {s['t']:.2f} мм | DXF {th_dxf} мм\n")
                p(f"      площа   : модель {s['area']:.0f} | DXF {best['area']:.0f} мм²  (відхил {bd*100:.1f}%)\n")
                p(f"      отвори  : модель {mh} | DXF {dh}\n")
                if issues:
                    problems.append((short, issues))

        # DXF без 3D-тіла у складанні
        for d in dlist:
            if d["name"] not in matched_dxf:
                problems.append((d["name"], ["немає 3D-тіла у складанні (перевір комплектність)"]))

        # підсумок по папці
        p("\n" + "-" * 78 + "\n")
        p(f"  ПІДСУМОК: деталей звірено {part_n}, без проблем {ok_n}, з проблемами {part_n - ok_n}\n")
        if problems:
            p("\n  ПРОБЛЕМИ ДЛЯ ВИПРАВЛЕННЯ:\n")
            for name, iss in problems:
                p(f"   • {name}\n")
                for it in iss:
                    p(f"       - {it}\n")
        else:
            p("  Розбіжностей не знайдено.\n")
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
