#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Передполітна сверка DXF перед порізкою.
Без зовнішніх залежностей — лише стандартний Python (працює в .exe без інтернету).

Кладеться поруч із папками-комплектами (у назві має бути "Корпус").
Подвійний клік -> звіт на екрані + report.txt поруч.
"""
import os, re, sys, glob, io

# Працюємо поруч з .exe / .py
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))

INSUNITS = {0: "не задано", 1: "дюйми", 4: "мм", 5: "см", 6: "м"}
THICK_MIN, THICK_MAX = 0.3, 20.0   # розумні межі товщини листа, мм

NAME_RE = re.compile(
    r"^\s*(?P<thick>[\d.,]+)\s*мм\s+"
    r"(?P<mat>\S+)\s+"
    r"(?P<qty>\d+)\s*шт\.?\s+"
    r"(?P<part>RDN\.[\d.]+)\s+"
    r"(?P<title>.+?)\.dxf$",
    re.IGNORECASE,
)


def parse_name(fname):
    m = NAME_RE.match(fname)
    if not m:
        return None
    d = m.groupdict()
    d["thick"] = float(d["thick"].replace(",", "."))
    d["qty"] = int(d["qty"])
    return d


def read_pairs(path):
    """Розбір DXF на пари (код, значення). DXF — це чергування рядків код/значення."""
    with io.open(path, "r", encoding="latin-1", errors="ignore") as f:
        lines = f.read().splitlines()
    pairs = []
    i = 0
    while i + 1 < len(lines):
        code = lines[i].strip()
        val = lines[i + 1]
        i += 2
        try:
            pairs.append((int(code), val))
        except ValueError:
            continue
    return pairs


def analyze_dxf(path):
    res = {"err": None}
    try:
        pairs = read_pairs(path)
    except Exception as e:
        res["err"] = f"не читається: {e}"
        return res

    units = 0
    ext = {"$EXTMIN": None, "$EXTMAX": None}
    # --- HEADER: одиниці й габарити ---
    for idx, (code, val) in enumerate(pairs):
        if code == 9 and val.strip() == "$INSUNITS":
            for c2, v2 in pairs[idx + 1: idx + 4]:
                if c2 == 70:
                    units = int(float(v2))
                    break
        if code == 9 and val.strip() in ext:
            name = val.strip()
            x = y = 0.0
            for c2, v2 in pairs[idx + 1: idx + 8]:
                if c2 == 10:
                    x = float(v2)
                elif c2 == 20:
                    y = float(v2)
                    break
            ext[name] = (x, y)
    res["units"] = INSUNITS.get(units, "?")
    emin = ext["$EXTMIN"] or (0.0, 0.0)
    emax = ext["$EXTMAX"] or (0.0, 0.0)
    res["w"] = round(abs(emax[0] - emin[0]), 2)
    res["h"] = round(abs(emax[1] - emin[1]), 2)

    # --- ENTITIES: отвори (CIRCLE) та відкриті контури (LWPOLYLINE/POLYLINE) ---
    holes, open_polys, poly_total = [], 0, 0
    cur = None          # поточний тип entity
    cur_codes = {}      # зібрані коди поточного entity

    def flush(t, codes):
        nonlocal open_polys, poly_total
        if t == "CIRCLE" and 40 in codes:
            holes.append(round(codes[40] * 2, 2))
        elif t in ("LWPOLYLINE", "POLYLINE"):
            poly_total_local = True
            flags = int(codes.get(70, 0))
            return ("poly", flags & 1)   # bit1 = closed
        return None

    in_entities = False
    pending = []  # список (type, codes)
    for code, val in pairs:
        if code == 2 and val.strip() == "ENTITIES":
            in_entities = True
            continue
        if not in_entities:
            continue
        if code == 0:
            if val.strip() == "ENDSEC":
                if cur is not None:
                    pending.append((cur, cur_codes))
                break
            if cur is not None:
                pending.append((cur, cur_codes))
            cur = val.strip()
            cur_codes = {}
        else:
            if code not in cur_codes:  # перший із кодів (напр. radius=40)
                try:
                    cur_codes[code] = float(val)
                except ValueError:
                    cur_codes[code] = val

    for t, codes in pending:
        if t == "CIRCLE" and 40 in codes:
            holes.append(round(codes[40] * 2, 2))
        elif t in ("LWPOLYLINE", "POLYLINE"):
            poly_total += 1
            if not (int(codes.get(70, 0)) & 1):
                open_polys += 1

    res["holes"] = sorted(holes)
    res["open_polys"] = open_polys
    res["poly_total"] = poly_total
    return res


def collect(folder):
    out = {}
    for path in sorted(glob.glob(os.path.join(folder, "**", "*.dxf"), recursive=True)):
        fname = os.path.basename(path)
        out[fname] = {"path": path, "meta": parse_name(fname), "geom": analyze_dxf(path)}
    return out


def warnings_for(rec):
    w = []
    meta, geom = rec["meta"], rec["geom"]
    if geom.get("err"):
        return [geom["err"]]
    if meta is None:
        w.append("ім'я не за шаблоном (товщина/матеріал/к-сть не розпізнані)")
    elif not (THICK_MIN <= meta["thick"] <= THICK_MAX):
        w.append(f"підозріла товщина {meta['thick']} мм (поза {THICK_MIN}-{THICK_MAX})")
    if geom.get("units") != "мм":
        w.append(f"одиниці DXF = {geom.get('units')} (очікується мм)")
    if geom.get("open_polys", 0) > 0:
        w.append(f"ВІДКРИТИХ контурів: {geom['open_polys']} (брак при порізці!)")
    if geom.get("w", 0) <= 0 or geom.get("h", 0) <= 0:
        w.append("нульові габарити в заголовку DXF")
    return w


def key_part(fname):
    m = re.search(r"(RDN\.[\d.]+)", fname)
    return m.group(1) if m else fname


def run(out):
    p = out.write
    folders = [d for d in glob.glob(os.path.join(ROOT, "*"))
               if os.path.isdir(d) and "Корпус" in os.path.basename(d)]
    if not folders:
        p("Поруч не знайдено жодної папки з 'Корпус' у назві.\n")
        return
    sets = {os.path.basename(f): collect(f) for f in folders}

    total_warn = 0
    for sname, recs in sets.items():
        p("=" * 70 + "\n")
        p(f"ПАПКА: {sname}\n")
        p("=" * 70 + "\n")
        for fname, rec in recs.items():
            m, g = rec["meta"], rec["geom"]
            ws = warnings_for(rec)
            total_warn += len(ws)
            mark = "[X]" if ws else "[OK]"
            if g.get("err"):
                p(f"{mark} {fname}\n     {g['err']}\n")
                continue
            mat = m["mat"] if m else "?"
            th = f"{m['thick']}мм" if m else "?"
            qty = f"{m['qty']}шт" if m else "?"
            p(f"{mark} {fname}\n")
            p(f"     габарит {g['w']}x{g['h']} мм | {th} | {mat} | {qty} | "
              f"отворів {len(g['holes'])} | одиниці {g['units']}\n")
            for warn in ws:
                p(f"     !! {warn}\n")
        p("\n")

    if len(sets) == 2:
        (n1, s1), (n2, s2) = list(sets.items())
        p("=" * 70 + "\n")
        p(f"ЗВІРКА МІЖ ПАПКАМИ: «{n1}»  vs  «{n2}»\n")
        p("=" * 70 + "\n")
        by1 = {key_part(f): r for f, r in s1.items()}
        by2 = {key_part(f): r for f, r in s2.items()}
        diff_count = 0
        for part in sorted(set(by1) | set(by2)):
            if part not in by1:
                p(f"[!] {part}: є тільки в «{n2}»\n"); diff_count += 1; continue
            if part not in by2:
                p(f"[!] {part}: є тільки в «{n1}»\n"); diff_count += 1; continue
            r1, r2 = by1[part], by2[part]
            m1, m2, g1, g2 = r1["meta"], r2["meta"], r1["geom"], r2["geom"]
            diffs = []
            if m1 and m2:
                if abs(m1["thick"] - m2["thick"]) > 1e-6:
                    diffs.append(f"товщина {m1['thick']} vs {m2['thick']} мм")
                if m1["mat"] != m2["mat"]:
                    diffs.append(f"матеріал {m1['mat']} vs {m2['mat']}")
                if m1["qty"] != m2["qty"]:
                    diffs.append(f"к-сть {m1['qty']} vs {m2['qty']} шт")
            if abs((g1.get("w") or 0) - (g2.get("w") or 0)) > 0.5 or \
               abs((g1.get("h") or 0) - (g2.get("h") or 0)) > 0.5:
                diffs.append(f"габарит {g1.get('w')}x{g1.get('h')} vs {g2.get('w')}x{g2.get('h')}")
            if len(g1.get("holes", [])) != len(g2.get("holes", [])):
                diffs.append(f"отворів {len(g1['holes'])} vs {len(g2['holes'])}")
            if diffs:
                p(f"[X] {part}: " + "; ".join(diffs) + "\n"); diff_count += 1
            else:
                p(f"[OK] {part}: збігається\n")
        p("\n" + "-" * 70 + "\n")
        p(f"ПІДСУМОК: розбіжностей між папками — {diff_count}\n")


def main():
    buf = io.StringIO()
    run(buf)
    text = buf.getvalue()
    # друк на екран
    try:
        print(text)
    except Exception:
        sys.stdout.write(text.encode("utf-8", "replace").decode("ascii", "replace"))
    # збереження поруч
    try:
        with io.open(os.path.join(ROOT, "report.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\nЗвіт збережено: {os.path.join(ROOT, 'report.txt')}")
    except Exception as e:
        print("Не вдалося зберегти report.txt:", e)
    # пауза, щоб вікно не закрилось після подвійного кліку
    try:
        input("\nНатисніть Enter, щоб закрити...")
    except Exception:
        pass


if __name__ == "__main__":
    main()
