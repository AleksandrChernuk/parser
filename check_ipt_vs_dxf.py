#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
МАСОВА сверка 3D-моделей Inventor (.ipt) з файлами порізки (.dxf).

Кладеться у будь-яку папку — скрипт РЕКУРСИВНО проходить усі підпапки під собою,
знаходить усі .ipt і .dxf, сам зіставляє їх у пари за іменем деталі та звіряє:

  - товщина з 3D-моделі (.ipt, поле "Лист 1,5 ...")  ==  товщина в імені DXF
  - матеріал в імені DXF узгоджений (виняток із загальної марки -> підозра)
  - геометрія DXF справна (замкнутий контур, одиниці мм, габарити, отвори)
  - комплектність (у кожної .ipt є свій .dxf і навпаки)

Пара визначається так: ім'я .ipt (без розширення) повністю міститься в імені .dxf.
Назви папок і деталей можуть бути будь-які — прив'язки до "Корпус"/"RDN" немає.

Залежність: olefile (чистий Python, входить у .exe). Подвійний клік -> звіт + report.txt.
"""
import os, re, sys, glob, io
import olefile

if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))

INSUNITS = {0: "не задано", 1: "дюйми", 4: "мм", 5: "см", 6: "м"}

# Товщина (+ матеріал) на початку імені DXF: "1,5мм Ст.3 ..." / "4 мм 09г2с ..."
DXF_THK_RE = re.compile(r"^\s*([\d.,]+)\s*мм\s+(\S+)", re.IGNORECASE)


def dxf_thickness_material(fname):
    m = DXF_THK_RE.match(fname)
    if not m:
        return None, None
    return float(m.group(1).replace(",", ".")), m.group(2)


# ---------- DXF: геометрія ----------
def read_pairs(path):
    with io.open(path, "r", encoding="latin-1", errors="ignore") as f:
        lines = f.read().splitlines()
    pairs, i = [], 0
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
    thick = mat = None
    try:
        ole = olefile.OleFileIO(path)
    except Exception as e:
        return None, f"не читається: {e}"
    for s in ole.listdir():
        try:
            data = ole.openstream(s).read().decode("utf-16-le", "ignore")
        except Exception:
            continue
        if thick is None:
            m = IPT_MAT_RE.search(data)
            if m:
                thick = float(m.group(1).replace(",", "."))
                mat = ("Лист " + m.group(1) + " " + m.group(2)).strip()
                break
    ole.close()
    return thick, mat


# ---------- збір усіх файлів під ROOT ----------
def project_of(path):
    """Назва проєкту = перша папка під ROOT (для групування у звіті)."""
    rel = os.path.relpath(path, ROOT)
    parts = rel.split(os.sep)
    return parts[0] if len(parts) > 1 else "."


SKIP_DIRS = ("oldversions", "old versions", "backup", "архив", "архів")


def _skip(path):
    low = path.lower()
    return any(os.sep + d in low or low.endswith(os.sep + d) for d in SKIP_DIRS) \
        or any(("/" + d + "/") in low.replace(os.sep, "/") for d in SKIP_DIRS)


def collect_all():
    ipts, dxfs = [], []
    for p in glob.glob(os.path.join(ROOT, "**", "*.ipt"), recursive=True):
        if not _skip(p): ipts.append(p)
    for p in glob.glob(os.path.join(ROOT, "**", "*.dxf"), recursive=True):
        if not _skip(p): dxfs.append(p)
    return ipts, dxfs


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def run(out):
    p = out.write
    ipts, dxfs = collect_all()
    if not ipts and not dxfs:
        p("Поруч (і у підпапках) не знайдено жодного .ipt чи .dxf.\n")
        p(f"Поточна папка: {ROOT}\n")
        return

    # зіставлення пар: ім'я .ipt має входити в ім'я .dxf; за збігу беремо найдовше
    used_dxf = set()
    pairs = []        # (ipt_path, dxf_path | None)
    for ip in ipts:
        s = stem(ip)
        proj = project_of(ip)
        cand = [d for d in dxfs
                if len(s) >= 3 and stem(d).endswith(s) and project_of(d) == proj]
        cand.sort(key=lambda d: len(stem(d)))
        match = None
        for d in cand:
            if d not in used_dxf:
                match = d; used_dxf.add(d); break
        pairs.append((ip, match))
    orphan_dxf = [d for d in dxfs if d not in used_dxf]

    # групуємо за проєктом
    groups = {}
    for ip, dx in pairs:
        groups.setdefault(project_of(ip), []).append((ip, dx))
    for d in orphan_dxf:
        groups.setdefault(project_of(d), []).append((None, d))

    total_bad = total_shown = 0
    for proj in sorted(groups):
        rows = groups[proj]
        # домінуюча марка матеріалу серед DXF цього проєкту
        mats = []
        for ip, dx in rows:
            if dx:
                _, mt = dxf_thickness_material(os.path.basename(dx))
                if mt: mats.append(mt)
        dom_mat = max(set(mats), key=mats.count) if mats else None

        p("=" * 72 + "\n")
        p(f"ПРОЄКТ: {proj}\n")
        p("=" * 72 + "\n")
        bad = shown = 0
        for ip, dx in sorted(rows, key=lambda r: stem(r[0] or r[1])):
            name = stem(ip) if ip else stem(dx)
            problems = []
            ipt_th = ipt_mat = None
            if ip:
                ipt_th, ipt_mat = read_ipt(ip)
            dxf_th = dxf_mat = None
            geom = None
            if dx:
                dxf_th, dxf_mat = dxf_thickness_material(os.path.basename(dx))
                geom = analyze_dxf(dx)

            # покупні/стандартні деталі (без DXF і без листового матеріалу) — пропускаємо
            if dx is None and ipt_th is None:
                continue

            if ip is None:
                problems.append("немає 3D-моделі .ipt для цього DXF")
            if dx is None:
                problems.append("немає DXF для цієї 3D-моделі")

            if ipt_th is not None and dxf_th is not None and abs(ipt_th - dxf_th) > 1e-6:
                problems.append(f"ТОВЩИНА не збігається: модель {ipt_th} мм / DXF {dxf_th} мм")
            if ip and ipt_th is None:
                problems.append("у .ipt не знайдено поле товщини (Лист ...)")
            if dx and dxf_th is None:
                problems.append("в імені DXF не зчитати товщину (немає 'X мм')")

            if dxf_mat and dom_mat and dxf_mat != dom_mat:
                problems.append(f"матеріал DXF '{dxf_mat}' відрізняється від основного '{dom_mat}'")

            if geom and not geom.get("err"):
                if geom["units"] != "мм":
                    problems.append(f"одиниці DXF = {geom['units']} (очікується мм)")
                if geom["open_polys"] > 0:
                    problems.append(f"ВІДКРИТИХ контурів {geom['open_polys']} (брак при порізці!)")
            elif geom and geom.get("err"):
                problems.append("DXF " + geom["err"])

            mark = "[X]" if problems else "[OK]"
            mdl = f"модель: {ipt_mat}" if ipt_mat else ("модель: —" if ip else "модель: (нема .ipt)")
            if dx:
                g = geom or {}
                dxf_line = (f"DXF: {dxf_th if dxf_th is not None else '?'}мм "
                            f"{dxf_mat or ''} | {g.get('w','?')}x{g.get('h','?')}мм | отв.{len(g.get('holes',[]))}")
            else:
                dxf_line = "DXF: (нема)"
            p(f"{mark} {name}\n     {mdl}\n     {dxf_line}\n")
            for pr in problems:
                p(f"     !! {pr}\n")
            shown += 1
            if problems: bad += 1
        p("\n" + "-" * 72 + "\n")
        p(f"ПІДСУМОК «{proj}»: проблемних — {bad} із {shown}\n\n")
        total_bad += bad
        total_shown += shown

    p("=" * 72 + "\n")
    p(f"РАЗОМ перевірено деталей: {total_shown} | проблемних: {total_bad}\n")
    p("(кількість шт. не перевіряється — береться зі специфікації/BOM)\n")


def main():
    buf = io.StringIO(); run(buf); text = buf.getvalue()
    try: print(text)
    except Exception: sys.stdout.write(text.encode("utf-8", "replace").decode("ascii", "replace"))
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
