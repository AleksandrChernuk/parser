#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сверка 3D-моделі Inventor (.ipt) з файлом порізки (.dxf) — передполітний контроль.

Еталон = .ipt (з нього читається ТОВЩИНА і МАТЕРІАЛ деталі, поле "Лист 1,5 ст 08кп").
Перевіряється:
  - товщина в імені DXF  ==  товщина з 3D-моделі (.ipt)
  - матеріал в імені DXF узгоджений (виняток із загальної марки -> підозра)
  - геометрія DXF справна (замкнутий контур, одиниці мм, габарити)
  - комплектність (у кожної .ipt є свій .dxf і навпаки)

Залежність: olefile (чистий Python, входить у .exe).
Кладеться поруч з папкою(ами), у назві яких є "Корпус". Подвійний клік -> звіт + report.txt.
"""
import os, re, sys, glob, io
import olefile

if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))

INSUNITS = {0: "не задано", 1: "дюйми", 4: "мм", 5: "см", 6: "м"}
THICK_MIN, THICK_MAX = 0.3, 20.0

# ---------- DXF: ім'я файлу ----------
DXF_NAME_RE = re.compile(
    r"^\s*(?P<thick>[\d.,]+)\s*мм\s+(?P<mat>\S+)\s+(?P<qty>\d+)\s*шт\.?\s+"
    r"(?P<part>RDN\.[\d.]+)\s+(?P<title>.+?)\.dxf$", re.IGNORECASE)


def parse_dxf_name(fname):
    m = DXF_NAME_RE.match(fname)
    if not m:
        return None
    d = m.groupdict()
    d["thick"] = float(d["thick"].replace(",", "."))
    d["qty"] = int(d["qty"])
    return d


# ---------- DXF: геометрія ----------
def read_pairs(path):
    with io.open(path, "r", encoding="latin-1", errors="ignore") as f:
        lines = f.read().splitlines()
    pairs = []
    i = 0
    while i + 1 < len(lines):
        try:
            pairs.append((int(lines[i].strip()), lines[i + 1]))
        except ValueError:
            pass
        i += 2
    return pairs


def analyze_dxf(path):
    res = {"err": None}
    try:
        pairs = read_pairs(path)
    except Exception as e:
        res["err"] = f"не читається: {e}"; return res
    units = 0
    ext = {"$EXTMIN": None, "$EXTMAX": None}
    for idx, (code, val) in enumerate(pairs):
        v = val.strip()
        if code == 9 and v == "$INSUNITS":
            for c2, v2 in pairs[idx + 1:idx + 4]:
                if c2 == 70:
                    units = int(float(v2)); break
        if code == 9 and v in ext:
            x = y = 0.0
            for c2, v2 in pairs[idx + 1:idx + 8]:
                if c2 == 10: x = float(v2)
                elif c2 == 20: y = float(v2); break
            ext[v] = (x, y)
    res["units"] = INSUNITS.get(units, "?")
    emin = ext["$EXTMIN"] or (0.0, 0.0)
    emax = ext["$EXTMAX"] or (0.0, 0.0)
    res["w"] = round(abs(emax[0] - emin[0]), 2)
    res["h"] = round(abs(emax[1] - emin[1]), 2)
    holes, open_polys = [], 0
    cur, codes, in_ent, pend = None, {}, False, []
    for code, val in pairs:
        if code == 2 and val.strip() == "ENTITIES":
            in_ent = True; continue
        if not in_ent:
            continue
        if code == 0:
            if cur is not None: pend.append((cur, codes))
            if val.strip() == "ENDSEC": break
            cur, codes = val.strip(), {}
        elif code not in codes:
            try: codes[code] = float(val)
            except ValueError: codes[code] = val
    for t, c in pend:
        if t == "CIRCLE" and 40 in c:
            holes.append(round(c[40] * 2, 2))
        elif t in ("LWPOLYLINE", "POLYLINE"):
            if not (int(c.get(70, 0)) & 1): open_polys += 1
    res["holes"] = sorted(holes)
    res["open_polys"] = open_polys
    return res


# ---------- .ipt: товщина + матеріал ----------
IPT_MAT_RE = re.compile(r"Лист\s*([\d.,]+)\s*([^/\r\n]*)")


def read_ipt(path):
    """Повертає (thickness, material_text, part_no) або (None, None, part)."""
    thick = mat = part = None
    try:
        ole = olefile.OleFileIO(path)
    except Exception as e:
        return None, f"не читається: {e}", None
    for s in ole.listdir():
        try:
            data = ole.openstream(s).read().decode("utf-16-le", "ignore")
        except Exception:
            continue
        if part is None:
            m = re.search(r"RDN\.[\d.]+", data)
            if m: part = m.group(0)
        if thick is None:
            m = IPT_MAT_RE.search(data)
            if m:
                thick = float(m.group(1).replace(",", "."))
                mat = ("Лист " + m.group(1) + " " + m.group(2)).strip()
    ole.close()
    return thick, mat, part


# ---------- збір по папці ----------
def part_key(name):
    m = re.search(r"RDN\.[\d.]+", name)
    return m.group(0) if m else name


def collect(folder):
    parts = {}
    for p in glob.glob(os.path.join(folder, "**", "RDN*.ipt"), recursive=True):
        k = part_key(os.path.basename(p))
        th, mat, _ = read_ipt(p)
        parts.setdefault(k, {})["ipt"] = {"thick": th, "mat": mat, "file": os.path.basename(p)}
    for p in glob.glob(os.path.join(folder, "**", "*.dxf"), recursive=True):
        fn = os.path.basename(p)
        k = part_key(fn)
        parts.setdefault(k, {})["dxf"] = {"meta": parse_dxf_name(fn),
                                          "geom": analyze_dxf(p), "file": fn}
    return parts


def run(out):
    p = out.write
    folders = [d for d in glob.glob(os.path.join(ROOT, "*"))
               if os.path.isdir(d) and "Корпус" in os.path.basename(d)]
    if not folders:
        p("Поруч не знайдено папки з 'Корпус' у назві. Поклади файл у папку проєкту.\n")
        return
    for folder in folders:
        parts = collect(folder)
        # домінуюча марка матеріалу серед DXF (для виявлення винятків)
        mats = [v["dxf"]["meta"]["mat"] for v in parts.values()
                if v.get("dxf") and v["dxf"]["meta"]]
        dom_mat = max(set(mats), key=mats.count) if mats else None

        p("=" * 72 + "\n")
        p(f"ПАПКА: {os.path.basename(folder)}\n")
        p("=" * 72 + "\n")
        bad = 0
        for k in sorted(parts):
            rec = parts[k]
            ipt, dxf = rec.get("ipt"), rec.get("dxf")
            problems = []

            if ipt is None:
                problems.append("немає 3D-моделі .ipt для цього DXF")
            if dxf is None:
                problems.append("немає DXF для цієї 3D-моделі")

            ipt_th = ipt["thick"] if ipt else None
            dxf_meta = dxf["meta"] if dxf else None
            dxf_th = dxf_meta["thick"] if dxf_meta else None
            geom = dxf["geom"] if dxf else None

            # головна сверка: товщина 3D vs DXF
            if ipt_th is not None and dxf_th is not None:
                if abs(ipt_th - dxf_th) > 1e-6:
                    problems.append(f"ТОВЩИНА не збігається: модель {ipt_th} мм / DXF '{dxf_th}мм'")
            elif dxf_meta is None and dxf is not None:
                problems.append("ім'я DXF не за шаблоном (товщину не зчитати)")

            # матеріал: виняток із загальної марки
            if dxf_meta and dom_mat and dxf_meta["mat"] != dom_mat:
                problems.append(f"матеріал DXF '{dxf_meta['mat']}' відрізняється від основного '{dom_mat}'")

            # геометрія DXF
            if geom and not geom.get("err"):
                if geom["units"] != "мм":
                    problems.append(f"одиниці DXF = {geom['units']} (очікується мм)")
                if geom["open_polys"] > 0:
                    problems.append(f"ВІДКРИТИХ контурів {geom['open_polys']} (брак при порізці!)")
            elif geom and geom.get("err"):
                problems.append("DXF " + geom["err"])

            mark = "[X]" if problems else "[OK]"
            mdl = f"модель: {ipt['mat']}" if ipt and ipt.get("mat") else "модель: —"
            dxf_info = ""
            if dxf_meta:
                g = geom or {}
                dxf_info = (f"DXF: {dxf_meta['thick']}мм {dxf_meta['mat']} {dxf_meta['qty']}шт "
                            f"| {g.get('w','?')}x{g.get('h','?')}мм | отв.{len(g.get('holes',[]))}")
            elif dxf:
                dxf_info = f"DXF: {dxf['file']}"
            p(f"{mark} {k}\n     {mdl}\n     {dxf_info}\n")
            for pr in problems:
                p(f"     !! {pr}\n");
            if problems: bad += 1
        p("\n" + "-" * 72 + "\n")
        p(f"ПІДСУМОК по «{os.path.basename(folder)}»: проблемних деталей — {bad} із {len(parts)}\n")
        p("(кількість шт. з .ipt не перевіряється — береться зі специфікації/BOM)\n\n")


def main():
    buf = io.StringIO(); run(buf); text = buf.getvalue()
    try: print(text)
    except Exception: sys.stdout.write(text.encode("utf-8","replace").decode("ascii","replace"))
    try:
        with io.open(os.path.join(ROOT, "report.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        print("Звіт збережено:", os.path.join(ROOT, "report.txt"))
    except Exception as e:
        print("Не вдалося зберегти report.txt:", e)
    try: input("\nНатисніть Enter, щоб закрити...")
    except Exception: pass


if __name__ == "__main__":
    main()
