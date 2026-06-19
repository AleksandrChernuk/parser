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
THICK_MIN, THICK_MAX = 0.3, 25.0      # розумний діапазон товщини листа, мм
HOLE_MIN = 1.0                        # отвір менший за це — підозра (важко різати)
SHEET_MAX = 4000.0                    # габарит понад це — підозра на одиниці/масштаб

# Товщина: число перед "мм" у будь-якому місці імені ("1,5мм", "_1,5 мм", "3мм")
DXF_THK_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*мм", re.IGNORECASE)
# Марка сталі/матеріалу: типові позначення в будь-якому місці імені
GRADE_RE = re.compile(
    r"(Ст\.?\s?\d+[а-яёіїє]*"          # Ст3, Ст.3, Ст 3
    r"|\d{2}[ГгҐ]\d[СсC]\w*"           # 09Г2С
    r"|\d{2}[кК][пП]|\d{2}[пП][сС]"    # 08кп, 08пс
    r"|AISI\s?\d+|\d{2}[хХ]\d+[нНтТ]\d+\w*)",  # AISI304, 12Х18Н10Т
    re.IGNORECASE)


QTY_RE = re.compile(r"(\d+)\s*шт", re.IGNORECASE)


def dxf_thickness_material(fname):
    """Повертає (товщина, матеріал, кількість) з імені DXF. Будь-що -> None, якщо немає."""
    th = mat = qty = None
    m = DXF_THK_RE.search(fname)
    if m:
        th = float(m.group(1).replace(",", "."))
    g = GRADE_RE.search(fname)
    if g:
        mat = re.sub(r"\s+", "", g.group(1))   # нормалізуємо: "Ст 3" -> "Ст3"
    q = QTY_RE.search(fname)
    if q:
        qty = int(q.group(1))
    return th, mat, qty


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
    holes, open_polys, closed_polys = [], 0, 0
    text_n = dim_n = spline_n = 0
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
            if int(c.get(70, 0)) & 1: closed_polys += 1
            else: open_polys += 1
        elif t in ("TEXT", "MTEXT", "ATTDEF"):
            text_n += 1
        elif t in ("DIMENSION", "LEADER", "MLEADER"):
            dim_n += 1
        elif t == "SPLINE":
            spline_n += 1
    res["holes"] = sorted(holes)
    res["open_polys"] = open_polys
    res["closed_polys"] = closed_polys
    res["text_n"] = text_n
    res["dim_n"] = dim_n
    res["spline_n"] = spline_n
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


# Артикул деталі: код виду RDN.1000.01.001 / ABC.2000 / XYZ.3000-12 тощо
ARTICLE_RE = re.compile(r"[A-Za-zА-Яа-яЇІЄҐієїґ]*\.?\d+(?:[.\-]\d+)+")


def article(name):
    """Найдовший код-артикул в імені (або None)."""
    cands = [c for c in articles_in(name)]
    return max(cands, key=len) if cands else None


def articles_in(name):
    """Усі коди-артикули в імені (для розпізнавання неста з кількома деталями)."""
    return {m.group(0) for m in ARTICLE_RE.finditer(name) if len(m.group(0)) >= 5}


def clean_desc(name):
    """Опис деталі = текст після артикула, без товщини/марки/кількості — для звірки назв."""
    art = article(name)
    s = name
    if art and art in s:
        s = s[s.index(art) + len(art):]
    s = DXF_THK_RE.sub(" ", s)
    s = GRADE_RE.sub(" ", s)
    s = QTY_RE.sub(" ", s)
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def run(out):
    p = out.write
    ipts, dxfs = collect_all()
    if not ipts and not dxfs:
        p("Поруч (і у підпапках) не знайдено жодного .ipt чи .dxf.\n")
        p(f"Поточна папка: {ROOT}\n")
        return

    # зіставлення: за артикулом (код) + за описом (текст після коду).
    # Це розрізняє деталі з однаковим артикулом ("Ребро" vs "Ребро 1") і терпить
    # будь-який зайвий текст/порядок у назві. Нест (кілька артикулів у 1 DXF) дозволено.
    dxf_arts = {d: articles_in(stem(d)) for d in dxfs}
    cands = []        # (score, len_desc, ip, d)
    for ip in ipts:
        s = stem(ip)
        proj = project_of(ip)
        a = article(s)
        idesc = clean_desc(s)
        for d in dxfs:
            if project_of(d) != proj:
                continue
            ok = (a in dxf_arts[d]) if a else stem(d).endswith(s)
            if not ok:
                continue
            ddesc = clean_desc(stem(d))
            if idesc and idesc == ddesc:
                sc = 3
            elif idesc and (idesc in ddesc or ddesc in idesc):
                sc = 2
            else:
                sc = 1
            cands.append((sc, len(idesc), ip, d))

    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    ipt_match, used = {}, set()
    for sc, _, ip, d in cands:
        if ip in ipt_match:
            continue
        nest = len(dxf_arts[d]) > 1            # справжній розкрій-нест -> можна повторно
        if not nest and d in used:
            continue
        ipt_match[ip] = d
        if not nest:
            used.add(d)

    pairs = [(ip, ipt_match.get(ip)) for ip in ipts]
    assigned = set(ipt_match.values())
    orphan_dxf = [d for d in dxfs if d not in assigned]

    # групуємо за проєктом
    groups = {}
    for ip, dx in pairs:
        groups.setdefault(project_of(ip), []).append((ip, dx))
    for d in orphan_dxf:
        groups.setdefault(project_of(d), []).append((None, d))

    total_bad = total_shown = 0
    errors_summary = []        # (proj, name, [problems]) для фінального переліку
    for proj in sorted(groups):
        rows = groups[proj]
        # домінуюча марка матеріалу серед DXF цього проєкту
        mats = []
        for ip, dx in rows:
            if dx:
                _, mt, _ = dxf_thickness_material(os.path.basename(dx))
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
            dxf_th = dxf_mat = dxf_qty = None
            geom = None
            if dx:
                dxf_th, dxf_mat, dxf_qty = dxf_thickness_material(os.path.basename(dx))
                geom = analyze_dxf(dx)

            # покупні/стандартні деталі (без DXF і без листового матеріалу) — пропускаємо
            if dx is None and ipt_th is None:
                continue

            if ip is None:
                problems.append("немає 3D-моделі .ipt для цього DXF")
            if dx is None:
                problems.append("немає DXF для цієї 3D-моделі")

            # --- обов'язкові поля в імені DXF: товщина + матеріал + кількість ---
            if dx:
                missing = []
                if dxf_th is None: missing.append("товщина")
                if dxf_mat is None: missing.append("матеріал")
                if dxf_qty is None: missing.append("кількість")
                if missing:
                    problems.append("в імені DXF немає: " + ", ".join(missing)
                                    + " (треба: товщина + матеріал + кількість)")

            # --- опис (назва) деталі: модель vs DXF ---
            if ip and dx:
                di, dd = clean_desc(stem(ip)), clean_desc(stem(dx))
                if di and dd and di != dd:
                    problems.append(f"ОПИС у назві відрізняється: модель '{di}' / DXF '{dd}'")

            # --- товщина: модель vs DXF ---
            if ipt_th is not None and dxf_th is not None and abs(ipt_th - dxf_th) > 1e-6:
                problems.append(f"ТОВЩИНА не збігається: модель {ipt_th} мм / DXF {dxf_th} мм")
            if ip and ipt_th is None:
                problems.append("у .ipt не знайдено поле товщини (Лист ...)")
            # кілька різних DXF на один артикул з різною товщиною
            if ip:
                art = article(name)
                if art:
                    same = [d for d in dxfs if art in stem(d) and project_of(d) == proj]
                    ths = {dxf_thickness_material(os.path.basename(d))[0] for d in same}  # товщина
                    ths = {t for t in ths if t is not None}
                    if len(same) > 1 and len(ths) > 1:
                        problems.append(f"кілька DXF на артикул {art} з різною товщиною: {sorted(ths)}")
            # товщина поза розумним діапазоном (помилка з обох боків)
            for src, val in (("модель", ipt_th), ("DXF", dxf_th)):
                if val is not None and not (THICK_MIN <= val <= THICK_MAX):
                    problems.append(f"товщина {src} {val} мм поза діапазоном {THICK_MIN}-{THICK_MAX}")

            # --- матеріал ---
            if dxf_mat and dom_mat and dxf_mat != dom_mat:
                problems.append(f"матеріал DXF '{dxf_mat}' відрізняється від основного '{dom_mat}'")

            # --- геометрія DXF ---
            if geom and not geom.get("err"):
                if geom["units"] != "мм":
                    problems.append(f"одиниці DXF = {geom['units']} (очікується мм)")
                if geom["open_polys"] > 0:
                    problems.append(f"ВІДКРИТИХ контурів {geom['open_polys']} (брак при порізці!)")
                if geom.get("w", 0) <= 0 or geom.get("h", 0) <= 0:
                    problems.append("нульові габарити (порожній/битий контур)")
                if geom.get("w", 0) > SHEET_MAX or geom.get("h", 0) > SHEET_MAX:
                    problems.append(f"габарит {geom['w']}x{geom['h']} мм завеликий (перевір одиниці/масштаб)")
                if geom.get("text_n"):
                    problems.append(f"у DXF є ТЕКСТ ({geom['text_n']}) — приберіть з шару різання")
                if geom.get("dim_n"):
                    problems.append(f"у DXF є РОЗМІРИ/виноски ({geom['dim_n']}) — приберіть з шару різання")
                if geom.get("spline_n"):
                    problems.append(f"у DXF є СПЛАЙНИ ({geom['spline_n']}) — деякі верстати ріжуть їх погано")
                tiny = [d for d in geom.get("holes", []) if d < HOLE_MIN]
                if tiny:
                    problems.append(f"дрібні отвори < {HOLE_MIN} мм: {tiny} (важко різати)")
                if geom.get("closed_polys", 0) == 0 and not geom.get("holes"):
                    problems.append("не знайдено замкнутого контуру деталі")
            elif geom and geom.get("err"):
                problems.append("DXF " + geom["err"])

            mark = "[X]" if problems else "[OK]"
            mdl = f"модель: {ipt_mat}" if ipt_mat else ("модель: —" if ip else "модель: (нема .ipt)")
            if dx:
                g = geom or {}
                dxf_line = (f"DXF: {dxf_th if dxf_th is not None else '?'}мм "
                            f"{dxf_mat or '?'} {dxf_qty if dxf_qty is not None else '?'}шт "
                            f"| {g.get('w','?')}x{g.get('h','?')}мм | отв.{len(g.get('holes',[]))}")
            else:
                dxf_line = "DXF: (нема)"
            p(f"{mark} {name}\n     {mdl}\n     {dxf_line}\n")
            for pr in problems:
                p(f"     !! {pr}\n")
            shown += 1
            if problems:
                bad += 1
                errors_summary.append((proj, name, problems))
        p("\n" + "-" * 72 + "\n")
        p(f"ПІДСУМОК «{proj}»: проблемних — {bad} із {shown}\n\n")
        total_bad += bad
        total_shown += shown

    p("=" * 72 + "\n")
    p(f"РАЗОМ перевірено деталей: {total_shown} | проблемних: {total_bad}\n")
    p("(кількість шт. не перевіряється — береться зі специфікації/BOM)\n")

    # --- фінальний короткий перелік помилок ---
    if errors_summary:
        p("\n" + "#" * 72 + "\n")
        p("СПИСОК ПОМИЛОК (для виправлення):\n")
        p("#" * 72 + "\n")
        for proj, name, problems in errors_summary:
            p(f"\n[{proj}] {name}\n")
            for pr in problems:
                p(f"   - {pr}\n")
    else:
        p("\nПомилок не знайдено — усе гаразд.\n")


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
