#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Creo/ProE CL (.ncl / .ncl.1) → Heidenhain Klartext (.h)
Верстат: DMU 80P · Стійка: TNC 426/430

    python convert.py                      # усі .ncl.1 у ncl\\ і поточній папці
    python convert.py fr3chern.ncl.1
    python convert.py file.ncl.1 -o out.h [--name PGM8] [--q339 3]
    python convert.py --dir .\\ncl
    python convert.py --check h\\out.h
    python convert.py --numbers …          # з номерами кадрів (за замовч. без)
    python convert.py --arcs cr …          # дуги CR (за замовч. CC/CP)
    python convert.py --cr …               # те саме, що --arcs cr
    python convert.py ncl\\2yst3.ncl.1 --cr -o h\\2yst3.h

Цех: M54/M53, M3 M8 / M5 M9, Q339=3, футер WORKING PLANE, ім'я ≤8 латиниця.
k≈-1: ручний переворот деталі → A0 C0 + XYZ (x,-y,-z), без A+180.
Вихід: h\\
"""

from __future__ import annotations

import hashlib
import locale
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from machines.dmu80p import MACHINE_DMU80P

EPS = 1e-4
ANGLE_EPS = 1e-3
COORD_DECIMALS = 3

_SCRIPT_DIR = Path(__file__).resolve().parent
CONVERTED_DIR = _SCRIPT_DIR / "h"
LST_DIR = _SCRIPT_DIR / "ncl"

KNOWN_NCL_CMDS = [
    "PARTNO",
    "UNITS",
    "PPRINT",
    "MACHIN",
    "MULTAX",
    "LOADTL",
    "SPINDL",
    "RAPID",
    "FEDRAT",
    "CUTCOM",
    "GOTO",
    "CIRCLE",
    "FINI",
    "FROM",
    "COOLNT",
    "PPRINT",
]


def ensure_converted_dir() -> Path:
    CONVERTED_DIR.mkdir(parents=True, exist_ok=True)
    return CONVERTED_DIR


def default_out_path(input_path: str | Path) -> Path:
    base = re.sub(r"\.(ncl)(\.\d+)?$", "", Path(input_path).name, flags=re.I) + ".h"
    return ensure_converted_dir() / base


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def warn(msg: str, warnings: list | None = None) -> None:
    line = f"WARN: {msg}"
    print(line, file=sys.stderr)
    if warnings is not None:
        warnings.append(msg)


def resolve_ncl_input(name: str) -> Path | None:
    candidates = [
        Path(name).resolve(),
        LST_DIR / Path(name).name,
        Path.cwd() / Path(name).name,
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def list_ncl_files(dir_path: str | Path) -> list[Path]:
    abs_p = Path(dir_path).resolve()
    if not abs_p.is_dir():
        return []
    files = [
        abs_p / f.name
        for f in abs_p.iterdir()
        if f.is_file() and re.search(r"\.ncl(\.\d+)?$", f.name, re.I)
    ]
    return sorted(files, key=lambda x: x.name)


def collect_default_ncl_files() -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for d in (LST_DIR, Path.cwd()):
        if not d.is_dir():
            continue
        for f in list_ncl_files(d):
            key = f.name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(f)

    collate = locale.getlocale(locale.LC_COLLATE)[0]
    if collate:

        def sort_key(p: Path) -> str:
            try:
                return locale.strxfrm(p.name)
            except ValueError:
                return p.name.lower()

        out.sort(key=sort_key)
    else:
        for loc in ("Ukrainian_Ukraine.1251", "uk_UA.UTF-8", "Ukrainian_Ukraine.utf8"):
            try:
                locale.setlocale(locale.LC_COLLATE, loc)
                out.sort(key=lambda p: locale.strxfrm(p.name))
                break
            except locale.Error:
                continue
        else:
            out.sort(key=lambda p: p.name.lower())
    return out


def nearly(a: float, b: float, eps: float = EPS) -> bool:
    return abs(a - b) <= eps


def fmt_num(
    v: float,
    *,
    plus: bool = True,
    bare_positive: bool = False,
    decimals: int = COORD_DECIMALS,
    keep_leading_zero: bool = False,
) -> str:
    if nearly(v, 0):
        v = 0.0
    neg = v < 0
    abs_v = abs(v)
    s = f"{abs_v:.{decimals}f}"
    s = re.sub(r"\.?0+$", "", s)
    if s == "":
        s = "0"
    if not keep_leading_zero and s.startswith("0.") and len(s) > 2:
        s = s[1:]
    if neg:
        return f"-{s}"
    if bare_positive:
        return s
    if plus:
        return f"+{s}"
    return s


def fmt_axis(axis: str, v: float, opts: dict | None = None) -> str:
    opts = opts or {}
    return f"{axis}{fmt_num(v, **opts)}"


def fmt_feed(feed: float) -> str:
    n = round(feed)
    if nearly(feed, n):
        return str(n)
    return fmt_num(feed, plus=False)


def fmt_angle(v: float) -> str:
    if nearly(v, 0, ANGLE_EPS):
        return "+0."
    s = fmt_num(v, plus=True, decimals=3)
    return s if "." in s else f"{s}."


def fmt_iz(iz: float) -> str:
    if nearly(iz, 0):
        return "IZ+0"
    if iz > 0:
        a = fmt_num(iz, plus=False)
        if a.startswith("0."):
            a = a[1:]
        return f"IZ{a}"
    return f"IZ{fmt_num(iz, plus=False)}"


_CYR_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "є": "ye",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "yi", "й": "y", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ь": "", "ю": "yu",
    "я": "ya", "ы": "y", "э": "e", "ё": "e", "ъ": "",
}


def sanitize_pgm_name(name: str | None, max_len: int | None = None) -> str:
    if max_len is None:
        max_len = MACHINE_DMU80P["pgmMaxLen"]
    s = str(name or "PGM")
    s = re.sub(r"\.(ncl)(\.\d+)?$", "", s, flags=re.I).lower()
    out_chars = []
    for ch in s:
        if ch in _CYR_MAP:
            out_chars.append(_CYR_MAP[ch])
            continue
        low = ch.lower()
        if low in _CYR_MAP:
            out_chars.append(_CYR_MAP[low])
        elif re.match(r"[a-z0-9]", ch, re.I):
            out_chars.append(ch)
        else:
            out_chars.append("_")
    s = "".join(out_chars)
    s = re.sub(r"_+", "_", s).strip("_").upper()
    if not s:
        s = "PGM"
    if len(s) > max_len:
        s = s[:max_len]
    return s


def default_pgm_name(input_path: str | Path, meta: dict) -> str:
    stem = re.sub(r"\.(ncl)(\.\d+)?$", "", Path(input_path).name, flags=re.I)
    if stem and not re.match(r"^mfg\d+", stem, re.I):
        return sanitize_pgm_name(stem)
    if meta.get("seqType"):
        return sanitize_pgm_name(meta["seqType"].replace(" ", "_"))
    return sanitize_pgm_name(meta.get("partno") or "PGM")


def join_continuations(raw: str) -> list[str]:
    lines = raw.replace("\ufeff", "").splitlines()
    out: list[str] = []
    buf = ""
    for line in lines:
        trimmed_right = re.sub(r"\s+$", "", line)
        if buf:
            buf += " " + trimmed_right.strip()
        else:
            buf = trimmed_right
        if buf.endswith("$"):
            buf = buf[:-1].rstrip()
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out


def js_number(s: str):
    """Match JavaScript Number() for NCL token parsing."""
    s = s.strip()
    if s == "":
        return 0
    try:
        v = float(s)
    except ValueError:
        return float("nan")
    if not math.isnan(v) and v == int(v):
        return int(v)
    return v


def js_str(v) -> str:
    """Match JavaScript String(v) for tool numbers etc."""
    if isinstance(v, float) and math.isnan(v):
        return "NaN"
    return str(v)


def parse_number_list(s: str) -> list[float]:
    return [js_number(x) for x in s.split(",") if x.strip()]


def tool_axis_to_ac(i: float, j: float, k: float) -> dict[str, float]:
    n = math.hypot(i, j, k) or 1.0
    i, j, k = i / n, j / n, k / n
    k = max(-1.0, min(1.0, k))
    a = math.acos(k) * 180.0 / math.pi
    c = 0.0
    if ANGLE_EPS < a < 180.0 - ANGLE_EPS:
        c = math.atan2(i, -j) * 180.0 / math.pi
    if nearly(a, 0, ANGLE_EPS):
        return {"A": 0.0, "C": 0.0}
    if nearly(c, 0, ANGLE_EPS):
        c = 0.0
    if nearly(c, 180, ANGLE_EPS) or nearly(c, -180, ANGLE_EPS):
        c = 180.0 if c > 0 else -180.0
    return {"A": a, "C": c}


def is_manual_flip_axis(i: float, j: float, k: float) -> bool:
    n = math.hypot(i, j, k) or 1.0
    return nearly(k / n, -1, ANGLE_EPS)


def manual_flip_to_plane(x: float, y: float, z: float) -> dict[str, float]:
    return {"x": x, "y": -y, "z": -z}


def horizontal_plane_ac(i: float, j: float, k: float) -> dict[str, float]:
    n = math.hypot(i, j, k) or 1.0
    i, j = i / n, j / n
    c = math.atan2(-i, j) * 180.0 / math.pi
    if nearly(c, 0, ANGLE_EPS):
        c = 0.0
    if nearly(c, 180, ANGLE_EPS) or nearly(c, -180, ANGLE_EPS):
        c = 180.0 if c > 0 else -180.0
    return {"A": 0.0, "C": c}


def machine_to_plane(x: float, y: float, z: float, a_deg: float, c_deg: float) -> dict[str, float]:
    a = a_deg * math.pi / 180.0
    c = c_deg * math.pi / 180.0
    cos_c = math.cos(-c)
    sin_c = math.sin(-c)
    x1 = x * cos_c - y * sin_c
    y1 = x * sin_c + y * cos_c
    z1 = z
    cos_a = math.cos(a)
    sin_a = math.sin(a)
    return {
        "x": x1,
        "y": y1 * cos_a + z1 * sin_a,
        "z": -y1 * sin_a + z1 * cos_a,
    }


def round_coord(v: float) -> float:
    f = 10**COORD_DECIMALS
    return round(v * f) / f


def parse_ncl(text: str) -> dict:
    lines = join_continuations(text)
    meta = {
        "partno": None,
        "date": None,
        "opName": None,
        "seqName": None,
        "seqType": None,
        "toolName": None,
        "toolType": None,
        "cutterDiam": None,
        "toolLength": None,
        "fluteLength": None,
        "cornerRadius": None,
        "units": "MM",
    }
    cmds: list[dict] = []
    unknown: list[str] = []
    tool_acc = empty_tool_fields()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("$$"):
            continue
        up = line.upper()
        verb = re.split(r"[\s/]", up)[0]

        if up.startswith("PARTNO"):
            parts = line.split("/")
            if len(parts) > 1:
                meta["partno"] = parts[1].strip() or meta["partno"]
            continue
        if up.startswith("UNITS"):
            parts = line.split("/")
            if len(parts) > 1:
                meta["units"] = (parts[1].strip().upper() or "MM")
            continue
        if up.startswith("PPRINT"):
            msg = "/".join(line.split("/")[1:]).strip()

            def take(pat: str) -> str | None:
                m = re.search(pat, msg, re.I)
                return m.group(1).strip() if m else None

            meta["date"] = take(r"DATE\s*TIME\s*:\s*(.+)") or meta["date"]
            meta["opName"] = take(r"OPERATION\s+NAME\s*:\s*(.+)") or meta["opName"]
            meta["seqName"] = take(r"NC\s+SEQUENCE\s+NAME\s*:\s*(.+)") or meta["seqName"]
            meta["seqType"] = take(r"SEQUENCE\s+TYPE\s*:\s*(.+)") or meta["seqType"]
            tool_name = take(r"TOOL\s+NAME\s*:\s*(.+)")
            if tool_name:
                tool_acc = empty_tool_fields()
                tool_acc["toolName"] = tool_name
                meta["toolName"] = tool_name
            tool_type = take(r"TOOL_TYPE\s*:\s*(.+)")
            if tool_type:
                tool_acc["toolType"] = tool_type
                meta["toolType"] = tool_type
            diam = take(r"^CUTTER_DIAM\s*:\s*([\d.+-]+)")
            length = take(r"^LENGTH\s*:\s*([\d.+-]+)")
            flute = take(r"^FLUTE_LENGTH\s*:\s*([\d.+-]+)")
            rad = take(r"^CORNER_RADIUS\s*:\s*([\d.+-]+)")
            if diam:
                tool_acc["cutterDiam"] = float(diam)
                meta["cutterDiam"] = float(diam)
            if length:
                tool_acc["toolLength"] = float(length)
                meta["toolLength"] = float(length)
            if flute:
                tool_acc["fluteLength"] = float(flute)
                meta["fluteLength"] = float(flute)
            if rad:
                tool_acc["cornerRadius"] = float(rad)
                meta["cornerRadius"] = float(rad)
            continue
        if up.startswith("MACHIN"):
            continue
        if up.startswith("MULTAX"):
            parts = line.split("/")
            on = bool(re.search(r"ON", parts[1] if len(parts) > 1 else "", re.I))
            cmds.append({"type": "MULTAX", "on": on})
            continue
        if up.startswith("LOADTL"):
            parts = line.split("/")
            tool_s = parts[1].split("$$")[0].split(",")[0].strip() if len(parts) > 1 else ""
            num = re.match(r"[+-]?\d+", tool_s)
            tool_meta = dict(tool_acc)
            tool_meta["seqName"] = meta.get("seqName")
            tool_meta["seqType"] = meta.get("seqType")
            cmds.append({
                "type": "LOADTL",
                "tool": js_number(num.group(0) if num else tool_s),
                "toolMeta": tool_meta,
            })
            continue
        if up.startswith("SPINDL"):
            body = line.split("/")[1] if "/" in line else ""
            if re.search(r"OFF", body, re.I):
                cmds.append({"type": "SPINDL_OFF"})
            else:
                m = re.search(r"([\d.]+)", body)
                rpm = js_number(m.group(1)) if m else 0.0
                clw = bool(re.search(r"CLW", body, re.I)) and not re.search(r"CCLW", body, re.I)
                cmds.append({"type": "SPINDL", "rpm": rpm, "clw": clw})
            continue
        if up.startswith("RAPID"):
            cmds.append({"type": "RAPID"})
            continue
        if up.startswith("FEDRAT"):
            parts = line.split("/")
            feed_s = parts[1].split(",")[0].strip() if len(parts) > 1 else ""
            cmds.append({"type": "FEDRAT", "feed": js_number(feed_s)})
            continue
        if up.startswith("CUTCOM"):
            body = (line.split("/")[1] if "/" in line else "").strip().upper()
            mode = "OFF"
            if body.startswith("LEFT"):
                mode = "LEFT"
            elif body.startswith("RIGHT"):
                mode = "RIGHT"
            cmds.append({"type": "CUTCOM", "mode": mode})
            continue
        if up.startswith("GOTO"):
            nums = parse_number_list(line.split("/")[1] if "/" in line else "")
            if len(nums) < 3 or any(math.isnan(n) for n in nums):  # noqa: JS Number.isNaN
                die(f"Bad GOTO: {line}")
            cmd: dict = {"type": "GOTO", "x": nums[0], "y": nums[1], "z": nums[2]}
            if len(nums) >= 6:
                cmd["i"], cmd["j"], cmd["k"] = nums[3], nums[4], nums[5]
            cmds.append(cmd)
            continue
        if up.startswith("CIRCLE"):
            nums = parse_number_list(line.split("/")[1] if "/" in line else "")
            if len(nums) < 7 or any(math.isnan(n) for n in nums):
                die(f"Bad CIRCLE: {line}")
            cmds.append({
                "type": "CIRCLE",
                "x": nums[0], "y": nums[1], "z": nums[2],
                "i": nums[3], "j": nums[4], "k": nums[5], "r": nums[6],
            })
            continue
        if up.startswith("FINI"):
            cmds.append({"type": "FINI"})
            continue
        if up.startswith("FROM") or up.startswith("COOLNT"):
            cmds.append({"type": "IGNORE", "raw": line})
            unknown.append(line)
            continue
        if re.match(r"^[A-Z]", up) and verb not in KNOWN_NCL_CMDS:
            unknown.append(line)

    return {"meta": meta, "cmds": cmds, "unknown": unknown}


def collect_warnings(meta: dict, cmds: list, unknown: list | None) -> list[str]:
    warnings: list[str] = []
    loadtls = [c for c in cmds if c["type"] == "LOADTL"]
    if len(loadtls) > 1:
        warnings.append(
            f"Multiple LOADTL ({len(loadtls)}) — basic multi-tool output; check TOOL CALL blocks"
        )

    first_ac = None
    axis_changes = 0
    for c in cmds:
        if c["type"] == "GOTO" and "i" in c:
            ac = tool_axis_to_ac(c["i"], c["j"], c["k"])
            if first_ac is None:
                first_ac = ac
            elif not nearly(ac["A"], first_ac["A"], ANGLE_EPS) or not nearly(ac["C"], first_ac["C"], ANGLE_EPS):
                axis_changes += 1
    if axis_changes > 0:
        warnings.append(
            f"Tool axis A/C changes mid-program ({axis_changes}) — single plane only; not full multi-orientation"
        )

    for u in unknown or []:
        warnings.append(f"Unsupported/ignored NCL: {u[:80]}")

    return warnings


def empty_tool_fields() -> dict:
    return {
        "toolName": None,
        "toolType": None,
        "cutterDiam": None,
        "toolLength": None,
        "fluteLength": None,
        "cornerRadius": None,
    }


def meta_for_tool(base: dict, tool_fields: dict | None) -> dict:
    merged = dict(base)
    fields = tool_fields or {}
    for key in empty_tool_fields():
        merged[key] = fields.get(key)
    for key, value in fields.items():
        if key not in merged or key not in empty_tool_fields():
            if value is not None:
                merged[key] = value
    return merged


def tool_kind(meta: dict) -> str:
    tt = meta.get("toolType") or ""
    if re.search(r"THREAD", tt, re.I):
        return "THREAD MILL"
    if re.search(r"END\s*MILL", tt, re.I):
        return "FLAT"
    if re.search(r"BALL", tt, re.I):
        return "BALL"
    if re.search(r"BULL", tt, re.I):
        return "BULL"
    return tt or "TOOL"


def tool_comment(meta: dict, tool_no) -> str:
    t = js_str(tool_no).zfill(3) if js_str(tool_no) != "NaN" else "NaN"
    kind = tool_kind(meta)
    tn = {"plus": False, "keep_leading_zero": True}
    d = fmt_num(meta["cutterDiam"], **tn) if meta.get("cutterDiam") is not None else "?"
    length = fmt_num(meta["toolLength"], **tn) if meta.get("toolLength") is not None else "?"
    radius = ""
    if kind == "BALL" and meta.get("cutterDiam") is not None:
        radius = f" R={fmt_num(meta['cutterDiam'] / 2, **tn)}"
    elif kind == "BULL" and meta.get("cornerRadius") is not None:
        radius = f" RAD={fmt_num(meta['cornerRadius'], **tn)}"
    flute = ""
    if meta.get("fluteLength") is not None and not nearly(meta["fluteLength"], 0):
        flute = f" FLUTE={fmt_num(meta['fluteLength'], **tn)}"
    name = ""
    if meta.get("toolName"):
        name = f"  ({meta['toolName']})" if kind == "BALL" else f" ({meta['toolName']})"
    return f";T{t}  : {kind} D={d}{radius} L={length}{flute}{name}"


def head_m_code(horizontal: bool, machine: dict | None = None) -> str:
    machine = machine or MACHINE_DMU80P
    return machine["horizontalHeadM"] if horizontal else machine["verticalHeadM"]


def q339_line(q339, machine: dict | None = None) -> str:
    machine = machine or MACHINE_DMU80P
    q = q339 if q339 is not None else machine["q339"]
    if q is None or q == "":
        return " Q339=    ;DATUM NUMBER"
    return f"    Q339={q}   ;DATUM NUMBER"


def after_tool_call_lines(horizontal: bool, machine: dict | None = None) -> list[str]:
    machine = machine or MACHINE_DMU80P
    m91 = machine["machineCoordM"]
    return [
        f"L Z-1 F MAX {m91}",
        machine["coolantOn"],
        machine["spindleOrientM"],
        head_m_code(horizontal, machine),
    ]


def tool_call_axis(horizontal: bool) -> str:
    return "Y" if horizontal else "Z"


def format_tool_call(tool_no, axis: str, rpm: float, machine: dict | None = None) -> str:
    machine = machine or MACHINE_DMU80P
    dr = machine.get("toolDr", "+0.0")
    dl = machine.get("toolDl", "+0.0")
    return f"TOOL CALL {js_str(tool_no)} {axis} S{round(rpm or 0)} DR{dr} DL{dl}"


def lbl_line(n: int, tool_name: str) -> str:
    return f"LBL {n} ; {tool_name}"


def zero_datum_shift() -> list[str]:
    return [
        "CYCL DEF 7.0 DATUM SHIFT",
        "CYCL DEF 7.1 X+0",
        "CYCL DEF 7.2 Y+0",
        "CYCL DEF 7.3 Z+0",
    ]


def lbl_setup_lines(machine: dict | None = None, q339=None) -> list[str]:
    """Шапка після кожного LBL: нуль, база, площина A0 C0, відвід."""
    machine = machine or MACHINE_DMU80P
    m91 = machine["machineCoordM"]
    return [
        *zero_datum_shift(),
        "CYCL DEF 247 DATUM SETTING ~",
        q339_line(q339, machine),
        f"CYCL DEF 19.0 {machine['planeComment']}",
        "CYCL DEF 19.1 A+0. C+0. F AUTO SET UP 10",
        f"L Z-1 F MAX {m91}",
        "L A+Q120 C+Q122 F MAX",
    ]


def tool_list_comments(cmds: list) -> list[str]:
    """One header comment per tool number, in the order Creo loads them."""
    lines: list[str] = []
    seen: set[str] = set()
    for c in cmds:
        if c.get("type") != "LOADTL":
            continue
        key = js_str(c["tool"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(tool_comment(c.get("toolMeta") or {}, c["tool"]))
    return lines


def build_header(
    pgm: str,
    meta: dict,
    tool_no: int,
    rpm: float,
    machine: dict | None = None,
    *,
    horizontal: bool = False,
    q339=None,
    lbl_no: int = 1,
    tool_comments: list[str] | None = None,
) -> list[str]:
    machine = machine or MACHINE_DMU80P
    date = meta.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    op = meta.get("opName") or "OP010"
    seq_label = meta.get("seqType") or meta.get("seqName") or "SEQ"
    tool_name = meta.get("toolName") or f"T{tool_no}"
    axis = tool_call_axis(horizontal)
    after_head = [
        format_tool_call(tool_no, axis, rpm, machine),
        *after_tool_call_lines(horizontal, machine),
    ]
    comments = tool_comments if tool_comments else [tool_comment(meta, tool_no)]
    return [
        f"BEGIN PGM {pgm} {meta.get('units') or 'MM'}",
        f";DATE : {date}",
        f";MACHINE : {machine['name']}",
        f";{op}",
        *comments,
        lbl_line(lbl_no, tool_name),
        *lbl_setup_lines(machine, q339),
        f";SEQ {pgm} ({seq_label})",
        f";{tool_name}",
        *after_head,
    ]


def build_tilt_plane_block(a: float, c: float, machine: dict | None = None) -> list[str]:
    machine = machine or MACHINE_DMU80P
    m91 = machine["machineCoordM"]
    return [
        f"L Z-1 F MAX {m91}",
        f"CYCL DEF 19.0 {machine['planeComment']}",
        f"CYCL DEF 19.1 A{fmt_angle(a)} C{fmt_angle(c)} F AUTO SET UP 10",
        "L A+Q120 C+Q122 F MAX",
    ]


def build_m53_datum_shift(machine: dict | None = None) -> list[str]:
    machine = machine or MACHINE_DMU80P
    y = machine["m53DatumY"]
    z = machine["m53DatumZ"]
    return [
        "CYCL DEF 7.0 DATUM SHIFT",
        f"CYCL DEF 7.1 Y{fmt_num(y, plus=True)}",
        f"CYCL DEF 7.2 Z{fmt_num(z, plus=True)}",
    ]


def build_tool_end_block(
    machine: dict | None = None,
    *,
    horizontal: bool = False,
    end_m: str = "M1",
) -> list[str]:
    machine = machine or MACHINE_DMU80P
    m91 = machine["machineCoordM"]
    if horizontal:
        return [
            f"L Y-1 F MAX {m91}",
            "CYCL DEF 7.0 DATUM SHIFT",
            "CYCL DEF 7.1 Y+0",
            "CYCL DEF 7.2 Z+0",
            machine["coolantOff"],
            "M54",
            machine["endToolCall"],
            f"L X+1 Y-1 F MAX {m91}",
            "CYCL DEF 19.0 WORKING PLANE",
            "CYCL DEF 19.1 A+0 C+0 F AUTO SET UP 10",
            "L A+Q120 C+Q122 F MAX",
            "CYCL DEF 19.0 WORKING PLANE",
            "CYCL DEF 19.1",
            end_m,
        ]
    return [
        machine["coolantOff"],
        f"L Z-1 F MAX {m91}",
        "CYCL DEF 19.0 WORKING PLANE",
        "CYCL DEF 19.1 A+0 C+0 F AUTO SET UP 10",
        "L A+Q120 C+Q122 F MAX",
        "CYCL DEF 19.0 WORKING PLANE",
        "CYCL DEF 19.1",
        machine["endToolCall"],
        f"L X+1 Y-1 F MAX {m91}",
        end_m,
    ]


def build_tool_change_block(
    tool_no: int,
    rpm: float,
    meta: dict,
    machine: dict | None = None,
    *,
    horizontal: bool = False,
    lbl_no: int = 2,
    q339=None,
) -> list[str]:
    machine = machine or MACHINE_DMU80P
    tool_name = meta.get("toolName") or f"T{tool_no}"
    axis = tool_call_axis(horizontal)
    return [
        *build_tool_end_block(machine, horizontal=horizontal, end_m=machine.get("endM") or "M1"),
        lbl_line(lbl_no, tool_name),
        *lbl_setup_lines(machine, q339),
        f";{tool_name}",
        tool_comment(meta, tool_no),
        format_tool_call(tool_no, axis, rpm, machine),
        *after_tool_call_lines(horizontal, machine),
    ]


def build_footer(
    pgm: str,
    units: str,
    machine: dict | None = None,
    *,
    horizontal: bool = False,
    end_m: str | None = None,
) -> list[str]:
    machine = machine or MACHINE_DMU80P
    m = end_m if end_m is not None else machine.get("lastEndM") or "M30"
    return [
        *build_tool_end_block(machine, horizontal=horizontal, end_m=m),
        f"END PGM {pgm} {units or 'MM'}",
    ]


def detect_horizontal_head(plane_a: float, cmds: list) -> bool:
    if abs(abs(plane_a) - 90) < 5:
        return True
    for c in cmds:
        if c["type"] == "GOTO" and "i" in c:
            n = math.hypot(c["i"], c["j"], c["k"]) or 1.0
            return abs(c["k"] / n) < 0.15
    return False


def radius_word(cutcom: str) -> str:
    if cutcom == "LEFT":
        return "RL"
    if cutcom == "RIGHT":
        return "RR"
    return "R0"


def angle_delta_deg(cx: float, cy: float, x0: float, y0: float, x1: float, y1: float, k: float) -> float:
    if nearly(x0, x1) and nearly(y0, y1):
        return 360.0 if k >= 0 else -360.0
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    d = (a1 - a0) * 180.0 / math.pi
    if k >= 0:
        while d <= 0:
            d += 360.0
        while d > 360:
            d -= 360.0
    else:
        while d >= 0:
            d -= 360.0
        while d < -360:
            d += 360.0
    return d


def detect_plane_angles(cmds: list) -> dict[str, float]:
    for c in cmds:
        if c["type"] == "GOTO" and "i" in c:
            return tool_axis_to_ac(c["i"], c["j"], c["k"])
    return {"A": 0.0, "C": 0.0}


def convert(meta: dict, cmds: list, pgm_name: str, options: dict | None = None) -> dict:
    options = options or {}
    warnings = options.get("warnings") or []
    q339 = options.get("q339") if options.get("q339") is not None else MACHINE_DMU80P["q339"]
    tool_no = 1
    rpm = 0.0
    spindle_clw = True
    rapid = False
    feed = None
    cutcom = "OFF"
    pos: dict = {"x": None, "y": None, "z": None}
    pending_circle = None
    spindle_on_emitted = False
    multax = False
    plane_ready = False
    arc_style = "cc" if options.get("arcs") == "cc" else "cr"
    modal_feed = None
    modal_r = None
    motion_started = False
    active_tool = None
    pending_tool = None
    pending_tool_meta = None

    header_tool = 1
    header_rpm = 0.0
    header_tool_meta = None
    saw_load = False
    for c in cmds:
        if c["type"] == "LOADTL":
            if not saw_load:
                header_tool = c["tool"]
                header_tool_meta = c.get("toolMeta")
                saw_load = True
            tool_no = c["tool"]
        if c["type"] == "SPINDL":
            if not header_rpm and c["rpm"]:
                header_rpm = c["rpm"]
            rpm = c["rpm"]
            spindle_clw = c["clw"]
        if c["type"] == "MULTAX":
            multax = c["on"]

    rpm = header_rpm or rpm
    tool_no = header_tool

    sample_ijk = None
    for c in cmds:
        if c["type"] == "GOTO" and "i" in c:
            sample_ijk = c
            break

    tool_ac = (
        tool_axis_to_ac(sample_ijk["i"], sample_ijk["j"], sample_ijk["k"])
        if sample_ijk
        else {"A": 0.0, "C": 0.0}
    )
    manual_flip = (
        is_manual_flip_axis(sample_ijk["i"], sample_ijk["j"], sample_ijk["k"])
        if sample_ijk
        else False
    )
    horizontal = detect_horizontal_head(tool_ac["A"], cmds)
    if horizontal and sample_ijk:
        plane_ac = horizontal_plane_ac(sample_ijk["i"], sample_ijk["j"], sample_ijk["k"])
    elif manual_flip:
        plane_ac = {"A": 0.0, "C": 0.0}
    else:
        plane_ac = tool_ac
    plane_a = plane_ac["A"]
    plane_c = plane_ac["C"]
    use_tilt = not manual_flip and (
        abs(plane_a) > ANGLE_EPS or abs(plane_c) > ANGLE_EPS
    )
    spindle_on_emitted = True

    body: list[str] = []

    def emit(s: str) -> None:
        body.append(s)

    seq_label = meta.get("seqType") or meta.get("seqName") or "SEQ"
    clearance_z = None
    pending_seq_after_clearance = False
    m53_datum_ready = False
    lbl_no = 1

    def to_out(x: float, y: float, z: float) -> dict[str, float]:
        if manual_flip:
            p = manual_flip_to_plane(x, y, z)
            return {"x": round_coord(p["x"]), "y": round_coord(p["y"]), "z": round_coord(p["z"])}
        if not use_tilt:
            return {"x": round_coord(x), "y": round_coord(y), "z": round_coord(z)}
        p = machine_to_plane(x, y, z, plane_a, plane_c)
        return {"x": round_coord(p["x"]), "y": round_coord(p["y"]), "z": round_coord(p["z"])}

    def ensure_tilt_plane() -> None:
        nonlocal plane_ready, modal_feed, modal_r
        if not use_tilt or plane_ready:
            return
        for line in build_tilt_plane_block(plane_a, plane_c):
            emit(line)
        plane_ready = True
        modal_feed = None
        modal_r = None

    def ensure_m53_datum() -> None:
        nonlocal m53_datum_ready, modal_feed, modal_r
        if not horizontal or m53_datum_ready:
            return
        for line in build_m53_datum_shift():
            emit(line)
        m53_datum_ready = True
        modal_feed = None
        modal_r = None

    def ensure_pre_machining() -> None:
        ensure_tilt_plane()
        ensure_m53_datum()

    def maybe_emit_seq_comment() -> None:
        nonlocal pending_seq_after_clearance, modal_feed
        if not pending_seq_after_clearance:
            return
        emit(f";SEQ {pgm_name} ({seq_label})")
        pending_seq_after_clearance = False
        modal_feed = None

    def flush_tool_change() -> None:
        nonlocal lbl_no, spindle_on_emitted, modal_feed, modal_r, plane_ready, m53_datum_ready
        nonlocal active_tool, tool_no, pending_tool, pending_tool_meta
        if pending_tool is None:
            return
        if active_tool is not None and pending_tool != active_tool and motion_started:
            lbl_no += 1
            for line in build_tool_change_block(
                pending_tool,
                rpm,
                meta_for_tool(meta, pending_tool_meta),
                horizontal=horizontal,
                lbl_no=lbl_no,
                q339=q339,
            ):
                emit(line)
            spindle_on_emitted = True
            modal_feed = None
            modal_r = None
            if use_tilt:
                plane_ready = False
            if horizontal:
                m53_datum_ready = False
        active_tool = pending_tool
        tool_no = pending_tool
        pending_tool = None
        pending_tool_meta = None

    def mark_clearance_retract(same_x: bool, same_y: bool, same_z: bool, approach: float) -> None:
        nonlocal pending_seq_after_clearance
        if not use_tilt or clearance_z is None:
            return
        if horizontal:
            if same_x and same_z and not same_y and nearly(approach, clearance_z):
                pending_seq_after_clearance = True
        elif same_x and same_y and not same_z and nearly(approach, clearance_z):
            pending_seq_after_clearance = True

    def emit_linear(x: float, y: float, z: float, *, force_r: bool = False) -> None:
        nonlocal pos, clearance_z, modal_feed, modal_r, motion_started, spindle_on_emitted
        flush_tool_change()
        ensure_pre_machining()
        maybe_emit_seq_comment()
        first = pos["x"] is None
        same_x = not first and nearly(x, pos["x"])
        same_y = not first and nearly(y, pos["y"])
        same_z = not first and nearly(z, pos["z"])

        m_codes: list[str] = []
        if not spindle_on_emitted and rpm > 0:
            m_codes.append("M3" if spindle_clw else "M4")
            spindle_on_emitted = True

        def want_r_val():
            if cutcom in ("LEFT", "RIGHT"):
                return radius_word(cutcom)
            if force_r or first or m_codes:
                return "R0"
            return None

        want_r = want_r_val()
        want_feed = "MAX" if rapid else feed

        if rapid:
            if horizontal:
                xz_change = first or not same_x or not same_z
                y_change = first or not same_y
                if xz_change and y_change:
                    y_parts = ["L", fmt_axis("Y", y)]
                    if want_r and want_r != modal_r:
                        y_parts.append(want_r)
                        modal_r = want_r
                    elif m_codes and not modal_r:
                        y_parts.append("R0")
                        modal_r = "R0"
                    y_parts.append("F MAX")
                    modal_feed = "MAX"
                    y_parts.extend(m_codes)
                    emit(" ".join(y_parts))
                    xz_parts = ["L"]
                    if first or not same_x:
                        xz_parts.append(fmt_axis("X", x))
                    if first or not same_z:
                        xz_parts.append(fmt_axis("Z", z))
                    xz_parts.append("F MAX")
                    emit(" ".join(xz_parts))
                    if clearance_z is None:
                        clearance_z = y
                    pos = {"x": x, "y": y, "z": z}
                    motion_started = True
                    return
            else:
                xy_change = first or not same_x or not same_y
                z_change = first or not same_z
                if xy_change and z_change:
                    parts = ["L"]
                    if first or not same_x:
                        parts.append(fmt_axis("X", x))
                    if first or not same_y:
                        parts.append(fmt_axis("Y", y))
                    if want_r and want_r != modal_r:
                        parts.append(want_r)
                        modal_r = want_r
                    elif m_codes and not modal_r:
                        parts.append("R0")
                        modal_r = "R0"
                    parts.append("F MAX")
                    modal_feed = "MAX"
                    parts.extend(m_codes)
                    emit(" ".join(parts))
                    emit(f"L {fmt_axis('Z', z)} F MAX")
                    if clearance_z is None:
                        clearance_z = z
                    pos = {"x": x, "y": y, "z": z}
                    motion_started = True
                    return
            parts = ["L"]
            if not same_x:
                parts.append(fmt_axis("X", x))
            if not same_y:
                parts.append(fmt_axis("Y", y))
            if not same_z:
                parts.append(fmt_axis("Z", z))
            if len(parts) == 1 and not m_codes:
                pos = {"x": x, "y": y, "z": z}
                return
            if m_codes:
                parts.append("R0")
                modal_r = "R0"
            parts.append("F MAX")
            modal_feed = "MAX"
            parts.extend(m_codes)
            emit(" ".join(parts))
            mark_clearance_retract(same_x, same_y, same_z, y if horizontal else z)
            if clearance_z is None:
                clearance_z = y if horizontal else z
            pos = {"x": x, "y": y, "z": z}
            motion_started = True
            return

        parts = ["L"]
        if not same_x:
            parts.append(fmt_axis("X", x))
        if not same_y:
            parts.append(fmt_axis("Y", y))
        if not same_z:
            parts.append(fmt_axis("Z", z))
        if len(parts) == 1 and not m_codes:
            pos = {"x": x, "y": y, "z": z}
            return

        if cutcom in ("LEFT", "RIGHT"):
            rw = radius_word(cutcom)
            parts.append(rw)
            modal_r = rw
            if feed is not None:
                parts.append(f"F{fmt_feed(feed)}")
                modal_feed = feed
        elif cutcom == "OFF" and force_r:
            parts.append("R0")
            modal_r = "R0"
        else:
            if want_feed is not None and (
                modal_feed is None
                or modal_feed == "MAX"
                or not nearly(modal_feed, want_feed)
            ):
                parts.append(f"F{fmt_feed(want_feed)}")
                modal_feed = want_feed
        parts.extend(m_codes)
        emit(" ".join(parts))
        mark_clearance_retract(same_x, same_y, same_z, y if horizontal else z)
        if clearance_z is None:
            clearance_z = y if horizontal else z
        pos = {"x": x, "y": y, "z": z}
        motion_started = True

    def emit_goto(mx: float, my: float, mz: float) -> None:
        nonlocal pending_circle, pos, modal_feed, motion_started
        p = to_out(mx, my, mz)

        if pending_circle:
            flush_tool_change()
            ensure_pre_machining()
            c = pending_circle
            pending_circle = None
            cc = to_out(c["x"], c["y"], c["z"])
            if horizontal:
                ipa = angle_delta_deg(cc["x"], cc["z"], pos["x"], pos["z"], p["x"], p["z"], c.get("j", 1))
            else:
                ipa = angle_delta_deg(cc["x"], cc["y"], pos["x"], pos["y"], p["x"], p["y"], c["k"])
            dr = "DR+" if ipa >= 0 else "DR-"
            if feed is not None and modal_feed != feed:
                emit(f"F{fmt_feed(feed)}")
                modal_feed = feed
            bp = {"bare_positive": True, "plus": False}
            if arc_style == "cc":
                iz = (p["y"] - (pos["y"] if pos["y"] is not None else cc["y"])) if horizontal else (
                    p["z"] - (pos["z"] if pos["z"] is not None else cc["z"])
                )
                ipa_str = fmt_num(abs(ipa), plus=False)
                if horizontal:
                    emit(
                        f"CC {fmt_axis('X', cc['x'], bp)} {fmt_axis('Z', cc['z'], bp)}"
                    )
                else:
                    emit(
                        f"CC {fmt_axis('X', cc['x'], bp)} {fmt_axis('Y', cc['y'], bp)}"
                    )
                emit(f"CP IPA{'+' if ipa >= 0 else '-'}{ipa_str} {fmt_iz(iz)} {dr}")
            elif horizontal:
                r_abs = abs(c["r"] if c.get("r") is not None else 0)
                emit(
                    f"CR  {fmt_axis('X', p['x'])} {fmt_axis('Z', p['z'])} R{fmt_num(r_abs, plus=True)} {dr}"
                )
            else:
                r_abs = abs(c["r"] if c.get("r") is not None else 0)
                emit(
                    f"CR  {fmt_axis('X', p['x'])} {fmt_axis('Y', p['y'])} R{fmt_num(r_abs, plus=True)} {dr}"
                )
            pos = {"x": p["x"], "y": p["y"], "z": p["z"]}
            motion_started = True
            return

        emit_linear(p["x"], p["y"], p["z"])

    active_tool = tool_no

    for c in cmds:
        t = c["type"]
        if t == "LOADTL":
            if c["tool"] != tool_no and motion_started:
                pending_tool = c["tool"]
                pending_tool_meta = c.get("toolMeta")
            else:
                tool_no = c["tool"]
                active_tool = c["tool"]
        elif t == "SPINDL":
            rpm = c["rpm"]
            spindle_clw = c["clw"]
            if pending_tool is not None:
                flush_tool_change()
        elif t == "SPINDL_OFF":
            pass
        elif t == "MULTAX":
            multax = c["on"]
        elif t == "RAPID":
            rapid = True
        elif t == "FEDRAT":
            feed = c["feed"]
            rapid = False
        elif t == "CUTCOM":
            cutcom = c["mode"]
        elif t == "CIRCLE":
            pending_circle = c
        elif t == "GOTO":
            if "i" in c:
                ac = (
                    horizontal_plane_ac(c["i"], c["j"], c["k"])
                    if horizontal
                    else tool_axis_to_ac(c["i"], c["j"], c["k"])
                )
                _ = ac
            emit_goto(c["x"], c["y"], c["z"])

    return {
        "lines": [
            *build_header(
                pgm_name,
                meta_for_tool(meta, header_tool_meta),
                header_tool,
                header_rpm or rpm,
                horizontal=horizontal,
                q339=q339,
                lbl_no=1,
                tool_comments=tool_list_comments(cmds),
            ),
            *body,
            *build_footer(pgm_name, meta.get("units") or "MM", horizontal=horizontal, end_m=MACHINE_DMU80P.get("lastEndM") or "M30"),
        ],
        "useTilt": use_tilt,
        "manualFlip": manual_flip,
        "horizontal": horizontal,
        "planeA": plane_a,
        "planeC": plane_c,
        "warnings": warnings,
    }


def number_blocks(lines: list[str], enabled: bool | None = None) -> list[str]:
    if enabled is None:
        enabled = MACHINE_DMU80P["numberBlocks"]
    if not enabled:
        return list(lines)
    out: list[str] = []
    n = 0
    for line in lines:
        if re.match(r"^\s+Q\d+", line):
            out.append(line)
            continue
        out.append(f"{n}  {line}")
        n += 1
    return out


def encode_cp1251(s: str) -> bytes:
    out: list[int] = []
    for ch in s:
        code = ord(ch)
        if code < 128:
            out.append(code)
            continue
        if 0x0410 <= code <= 0x044F:
            out.append(code - 0x0410 + 0xC0)
            continue
        if code == 0x0401:
            out.append(0xA8)
            continue
        if code == 0x0451:
            out.append(0xB8)
            continue
        out.append(0x3F)
    return bytes(out)


def decode_cp1251(buf: bytes) -> str:
    m: dict[int, str] = {i: chr(i) for i in range(128)}
    for i in range(64):
        m[0xC0 + i] = chr(0x0410 + i)
    m[0xA8] = "\u0401"
    m[0xB8] = "\u0451"
    return "".join(m.get(b, "?") for b in buf)


def check_klartext(text: str, opts: dict | None = None) -> dict:
    opts = opts or {}
    errors: list[str] = []
    lines = [ln for ln in text.replace("\ufeff", "").splitlines() if len(ln)]
    bodies = [re.sub(r"^\d+\s\s", "", ln) for ln in lines]
    joined = "\n".join(bodies)

    if not any(ln.startswith("BEGIN PGM") for ln in bodies):
        errors.append("Missing BEGIN PGM")
    if not any(ln.startswith("END PGM") for ln in bodies):
        errors.append("Missing END PGM")
    has_end_m = any(ln == "M30" or ln.startswith("M30 ") for ln in bodies) or any(
        ln == "M1" or ln.startswith("M1 ") for ln in bodies
    )
    if not has_end_m:
        errors.append("Missing M1/M30")
    if not any(ln.startswith("TOOL CALL") for ln in bodies):
        errors.append("Missing TOOL CALL")

    has_block_numbers = any(re.match(r"^\d+\s\s", ln) for ln in lines)
    block_count = len(bodies)
    if has_block_numbers:
        expect = 0
        for line in lines:
            if re.match(r"^\s+Q\d+", line):
                continue
            m = re.match(r"^(\d+)\s\s", line)
            if not m:
                errors.append(f"Line without block number: {line[:60]}")
                break
            n = int(m.group(1))
            if n != expect:
                errors.append(f"Block number jump: expected {expect}, got {n}")
                break
            expect += 1
        block_count = expect

    if opts.get("expectTilt"):
        has_tilt = any(
            re.search(r"CYCL DEF 19\.1\s+A[+-]?\d", ln)
            and not re.search(r"A\+0\.\s+C\+0\.", ln)
            and not re.search(r"A\+0\s+C\+0\b", ln)
            for ln in bodies
        )
        if not has_tilt:
            errors.append("Expected tilted CYCL DEF 19.1 A/C (non-zero), not found")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "info": {
            "blocks": block_count,
            "hasMachine": "MACHINE" in joined,
            "toolCalls": sum(1 for ln in bodies if ln.startswith("TOOL CALL")),
        },
    }


def convert_file(
    input_path: str | Path,
    *,
    output=None,
    name=None,
    numbers=None,
    q339=None,
    arcs: str = "cc",
) -> dict:
    text = Path(input_path).read_text(encoding="utf-8", errors="replace")
    parsed = parse_ncl(text)
    meta, cmds, unknown = parsed["meta"], parsed["cmds"], parsed["unknown"]
    if not cmds:
        die(f"No motion commands found in {input_path}")

    warnings = collect_warnings(meta, cmds, unknown)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)

    pgm = sanitize_pgm_name(name or default_pgm_name(input_path, meta))
    result = convert(meta, cmds, pgm, {"warnings": warnings, "q339": q339, "arcs": arcs})
    with_numbers = numbers if numbers is not None else MACHINE_DMU80P["numberBlocks"]
    numbered = number_blocks(result["lines"], with_numbers)
    payload = "\r\n".join(numbered) + "\r\n"

    out_path = Path(output).resolve() if output else default_out_path(input_path)
    ensure_converted_dir()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(encode_cp1251(payload))

    check = check_klartext(payload, {"expectTilt": result["useTilt"]})
    if not check["ok"]:
        for e in check["errors"]:
            print(f"CHECK FAIL: {e}", file=sys.stderr)
    else:
        print(f"CHECK: OK ({check['info']['blocks']} lines)")

    print(f"OK: {out_path}")
    print(f"MACHINE: {MACHINE_DMU80P['name']} / {MACHINE_DMU80P['control']}")
    print(f"PGM: {pgm}")
    print(f"HEAD: {'M53 horizontal' if result['horizontal'] else 'M54 vertical'}")
    print(f"ARCS: {'CC/CP' if arcs == 'cc' else 'CR'}")
    if result["manualFlip"]:
        print("PLANE: A+0 C+0 (manual flip, XYZ mirrored)")
    elif result["useTilt"]:
        print(f"PLANE: A{fmt_angle(result['planeA'])} C{fmt_angle(result['planeC'])}")
    print(f"Lines: {len(numbered)}")
    if warnings:
        print(f"Warnings: {len(warnings)}")

    return {
        "outPath": out_path,
        "numbered": numbered,
        "result": result,
        "check": check,
        "warnings": warnings,
        "pgm": pgm,
    }


def snapshot_key_lines(numbered_lines: list[str]) -> list[str]:
    bodies = [re.sub(r"^\d+\s\s", "", ln) for ln in numbered_lines]
    return [
        ln
        for ln in bodies
        if ln.startswith("BEGIN PGM")
        or ln.startswith("TOOL CALL")
        or ln.startswith("CYCL DEF 19.1")
        or ln.startswith("END PGM")
        or ln == "M30"
        or re.match(r"^L .*M3$", ln)
        or ln.startswith("CC ")
        or ln.startswith("CP ")
    ]


def hash_payload(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def run_batch(files: list[Path], opts: dict | None = None) -> None:
    opts = opts or {}
    if not files:
        die(
            "Немає .ncl / .ncl.1.\n"
            "Usage:\n"
            "  python convert.py <file.ncl.1> [-o out.h] [--name PGM8] [--q339 3]\n"
            "  python convert.py --dir <folder>\n"
            "  python convert.py --numbers …\n"
            "  python convert.py --check <file.h>"
        )
    print(f"Batch: {len(files)} file(s)")
    failed = 0
    for f in files:
        print(f"--- {f.name}")
        try:
            r = convert_file(f, **opts)
            if not r["check"]["ok"]:
                failed += 1
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            failed += 1
    if failed:
        die(f"Batch finished with {failed} failure(s)")


def parse_args_js_style(argv: list[str]) -> dict:
    """Faithful port of parseArgs from convert.js"""
    args = {
        "input": None,
        "output": None,
        "name": None,
        "dir": None,
        "check": None,
        "numbers": False,
        "q339": None,
        "arcs": "cc",
    }
    rest = argv[1:]
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("-o", "--output"):
            i += 1
            args["output"] = rest[i]
        elif a == "--name":
            i += 1
            args["name"] = rest[i]
        elif a == "--dir":
            i += 1
            args["dir"] = rest[i] if i < len(rest) else "."
        elif a == "--check":
            i += 1
            args["check"] = rest[i]
        elif a == "--numbers":
            args["numbers"] = True
        elif a == "--q339":
            i += 1
            args["q339"] = float(rest[i])
        elif a == "--arcs":
            i += 1
            v = (rest[i] if i < len(rest) else "").lower()
            if v not in ("cr", "cc"):
                die("--arcs needs cr or cc")
            args["arcs"] = v
        elif a == "--cr":
            args["arcs"] = "cr"
        elif a == "--no-cr":
            args["arcs"] = "cc"
        elif a.startswith("-"):
            die(f"Unknown option: {a}")
        elif args["input"] is None:
            args["input"] = a
        else:
            die(f"Unexpected argument: {a}")
        i += 1
    return args


def main() -> None:
    args = parse_args_js_style(sys.argv)
    file_opts = {
        "numbers": True if args["numbers"] else None,
        "q339": args["q339"],
        "name": args["name"],
        "output": args["output"],
        "arcs": args["arcs"],
    }

    if args["check"]:
        p = Path(args["check"]).resolve()
        if not p.is_file():
            die(f"File not found: {p}")
        text = decode_cp1251(p.read_bytes())
        res = check_klartext(text)
        if res["ok"]:
            print(f"CHECK OK: {p}")
            print(f"Lines: {res['info']['blocks']}, TOOL CALL: {res['info']['toolCalls']}")
            sys.exit(0)
        print(f"CHECK FAIL: {p}", file=sys.stderr)
        for e in res["errors"]:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    if args["dir"]:
        run_batch(list_ncl_files(args["dir"]), file_opts)
        return

    if not args["input"]:
        run_batch(collect_default_ncl_files(), file_opts)
        return

    inp = resolve_ncl_input(args["input"])
    if not inp:
        die(f"File not found: {args['input']}")
    convert_file(inp, **file_opts)


if __name__ == "__main__":
    main()