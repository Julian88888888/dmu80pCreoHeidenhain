#!/usr/bin/env node
/**
 * Creo/ProE CL (.ncl / .ncl.1) → Heidenhain Klartext (.h)
 * Верстат: DMU 80P · Стійка: TNC 426/430
 *
 *   node convert.js                      # усі .ncl.1 у lst\ і поточній папці
 *   node convert.js fr3chern.ncl.1
 *   node convert.js file.ncl.1 -o out.h [--name PGM8] [--q339 4]
 *   node convert.js --dir .\lst
 *   node convert.js --check converted\out.h
 *   node convert.js --numbers …          # з номерами кадрів (за замовч. без)
 *
 * Цех: M54/M53, M3 M8 / M5 M9, Q339, футер WORKING PLANE, ім’я ≤8 латиниця.
 * Вихід: converted\
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const EPS = 1e-4;
const ANGLE_EPS = 1e-3;
const COORD_DECIMALS = 3;

const CONVERTED_DIR = path.join(__dirname, "converted");
const LST_DIR = path.join(__dirname, "lst");

function ensureConvertedDir() {
  if (!fs.existsSync(CONVERTED_DIR)) {
    fs.mkdirSync(CONVERTED_DIR, { recursive: true });
  }
  return CONVERTED_DIR;
}

function defaultOutPath(inputPath) {
  const base =
    path.basename(inputPath).replace(/\.(ncl)(\.\d+)?$/i, "") + ".h";
  return path.join(ensureConvertedDir(), base);
}

/** Typical DMU 80P profile for TNC 426/430 (not an official DMG post). */
const MACHINE_DMU80P = {
  id: "DMU80P",
  name: "DMU 80P",
  control: "TNC 426/430",
  verticalHeadM: "M54 ;vertical head",
  horizontalHeadM: "M53 ;horizontal head",
  spindleOrientM: "M126",
  machineCoordM: "M91",
  planeComment: "PLOSKOST OBRABOTKI",
  /** Цехові замовчування (оператор: кишеню/DR інколи править руками). */
  q339: 4,
  coolantOn: "M3 M8",
  coolantOff: "M5 M9",
  endToolCall: "TOOL CALL Z S1000",
  endM: "M1",
  pgmMaxLen: 8,
  numberBlocks: false,
  /** Зміщення нуля при горизонтальній голові M53 (перед обробкою). */
  m53DatumY: 0.38,
  m53DatumZ: -0.385,
};

const KNOWN_NCL_CMDS = [
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
];

function die(msg) {
  console.error(msg);
  process.exit(1);
}

function warn(msg, warnings) {
  const line = `WARN: ${msg}`;
  console.error(line);
  if (warnings) warnings.push(msg);
}

function parseArgs(argv) {
  const args = {
    input: null,
    output: null,
    name: null,
    dir: null,
    check: null,
    numbers: false,
    q339: null,
  };
  const rest = argv.slice(2);
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a === "-o" || a === "--output") args.output = rest[++i];
    else if (a === "--name") args.name = rest[++i];
    else if (a === "--dir") args.dir = rest[++i] || ".";
    else if (a === "--check") args.check = rest[++i];
    else if (a === "--numbers") args.numbers = true;
    else if (a === "--q339") args.q339 = Number(rest[++i]);
    else if (a.startsWith("-")) die(`Unknown option: ${a}`);
    else if (!args.input) args.input = a;
    else die(`Unexpected argument: ${a}`);
  }
  return args;
}

function resolveNclInput(name) {
  const candidates = [
    path.resolve(name),
    path.join(LST_DIR, path.basename(name)),
    path.join(process.cwd(), path.basename(name)),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function collectDefaultNclFiles() {
  const seen = new Set();
  const out = [];
  for (const dir of [LST_DIR, process.cwd()]) {
    if (!fs.existsSync(dir)) continue;
    for (const f of listNclFiles(dir)) {
      const key = path.basename(f).toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(f);
    }
  }
  return out.sort((a, b) =>
    path.basename(a).localeCompare(path.basename(b), "uk")
  );
}

function nearly(a, b, eps = EPS) {
  return Math.abs(a - b) <= eps;
}

/** Heidenhain-style number (default 3 decimals like fr10.h; Y+.776 not Y+0.776) */
function fmtNum(
  v,
  {
    plus = true,
    barePositive = false,
    decimals = COORD_DECIMALS,
    keepLeadingZero = false,
  } = {}
) {
  if (nearly(v, 0)) v = 0;
  const neg = v < 0;
  const abs = Math.abs(v);
  let s = abs.toFixed(decimals).replace(/\.?0+$/, "");
  if (s === "") s = "0";
  if (!keepLeadingZero && s.startsWith("0.") && s.length > 2) s = s.slice(1);
  if (neg) return `-${s}`;
  if (barePositive) return s;
  if (plus) return `+${s}`;
  return s;
}

function fmtAxis(axis, v, opts) {
  return `${axis}${fmtNum(v, opts)}`;
}

function fmtFeed(feed) {
  const n = Math.round(feed);
  if (nearly(feed, n)) return String(n);
  return fmtNum(feed, { plus: false });
}

function fmtAngle(v) {
  if (nearly(v, 0, ANGLE_EPS)) return "+0.";
  const s = fmtNum(v, { plus: true, decimals: 3 });
  return s.includes(".") ? s : `${s}.`;
}

function fmtIZ(iz) {
  if (nearly(iz, 0)) return "IZ+0";
  if (iz > 0) {
    let a = fmtNum(iz, { plus: false });
    if (a.startsWith("0.")) a = a.slice(1);
    return `IZ${a}`;
  }
  return `IZ${fmtNum(iz, { plus: false })}`;
}

/** Кирилиця → латиниця (грубо), далі лише A–Z / 0–9 / _, max 8 (цех). */
function sanitizePgmName(name, maxLen = MACHINE_DMU80P.pgmMaxLen) {
  const map = {
    а: "a", б: "b", в: "v", г: "g", ґ: "g", д: "d", е: "e", є: "ye", ж: "zh",
    з: "z", и: "y", і: "i", ї: "yi", й: "y", к: "k", л: "l", м: "m", н: "n",
    о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "ts",
    ч: "ch", ш: "sh", щ: "sch", ь: "", ю: "yu", я: "ya", ы: "y", э: "e",
    ё: "e", ъ: "",
  };
  let s = String(name || "PGM")
    .replace(/\.(ncl)(\.\d+)?$/i, "")
    .toLowerCase();
  s = [...s]
    .map((ch) => {
      if (map[ch]) return map[ch];
      const up = ch.toUpperCase();
      const low = ch.toLowerCase();
      if (map[low]) return map[low];
      if (/[a-z0-9]/i.test(ch)) return ch;
      return "_";
    })
    .join("");
  s = s.replace(/_+/g, "_").replace(/^_|_$/g, "").toUpperCase();
  if (!s) s = "PGM";
  if (s.length > maxLen) s = s.slice(0, maxLen);
  return s;
}

function defaultPgmName(inputPath, meta) {
  const stem = path.basename(inputPath).replace(/\.(ncl)(\.\d+)?$/i, "");
  if (stem && !/^mfg\d+/i.test(stem)) return sanitizePgmName(stem);
  if (meta.seqType) return sanitizePgmName(meta.seqType.replace(/\s+/g, "_"));
  return sanitizePgmName(meta.partno || "PGM");
}

function joinContinuations(raw) {
  const lines = raw.replace(/^\uFEFF/, "").split(/\r?\n/);
  const out = [];
  let buf = "";
  for (const line of lines) {
    const trimmedRight = line.replace(/\s+$/, "");
    if (buf) buf += " " + trimmedRight.trim();
    else buf = trimmedRight;
    if (buf.endsWith("$")) {
      buf = buf.slice(0, -1).trimEnd();
      continue;
    }
    out.push(buf);
    buf = "";
  }
  if (buf) out.push(buf);
  return out;
}

function parseNumberList(str) {
  return str
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => Number(x));
}

/**
 * Tool axis (i,j,k) -> Heidenhain A/C for DMU vertical head (M54).
 * Tool = Rz(C) * Rx(A) * (0,0,1) = (sinC*sinA, -cosC*sinA, cosA)
 */
function toolAxisToAC(i, j, k) {
  const n = Math.hypot(i, j, k) || 1;
  i /= n;
  j /= n;
  k /= n;
  k = Math.max(-1, Math.min(1, k));
  const A = (Math.acos(k) * 180) / Math.PI;
  let C = 0;
  if (A > ANGLE_EPS && A < 180 - ANGLE_EPS) {
    C = (Math.atan2(i, -j) * 180) / Math.PI;
  }
  if (nearly(A, 0, ANGLE_EPS)) return { A: 0, C: 0 };
  if (nearly(C, 0, ANGLE_EPS)) C = 0;
  if (nearly(C, 180, ANGLE_EPS) || nearly(C, -180, ANGLE_EPS))
    C = C > 0 ? 180 : -180;
  return { A, C };
}

/**
 * M53 (горизонтальна голова): у CYCL 19 НЕ A+90/C+180 від вектора,
 * а кут стола A≈0, C = atan2(-i, j) — як у ICAM etalon (C+20 тощо).
 */
function horizontalPlaneAC(i, j, k) {
  const n = Math.hypot(i, j, k) || 1;
  i /= n;
  j /= n;
  let C = (Math.atan2(-i, j) * 180) / Math.PI;
  if (nearly(C, 0, ANGLE_EPS)) C = 0;
  if (nearly(C, 180, ANGLE_EPS) || nearly(C, -180, ANGLE_EPS))
    C = C > 0 ? 180 : -180;
  return { A: 0, C };
}

/** Machine XYZ -> working-plane XYZ for CYCL DEF 19 A/C */
function machineToPlane(x, y, z, Adeg, Cdeg) {
  const A = (Adeg * Math.PI) / 180;
  const C = (Cdeg * Math.PI) / 180;
  const cosC = Math.cos(-C);
  const sinC = Math.sin(-C);
  const x1 = x * cosC - y * sinC;
  const y1 = x * sinC + y * cosC;
  const z1 = z;
  const cosA = Math.cos(A);
  const sinA = Math.sin(A);
  return {
    x: x1,
    y: y1 * cosA + z1 * sinA,
    z: -y1 * sinA + z1 * cosA,
  };
}

function roundCoord(v) {
  const f = 10 ** COORD_DECIMALS;
  return Math.round(v * f) / f;
}

function parseNcl(text) {
  const lines = joinContinuations(text);
  const meta = {
    partno: null,
    date: null,
    opName: null,
    seqName: null,
    seqType: null,
    toolName: null,
    toolType: null,
    cutterDiam: null,
    toolLength: null,
    fluteLength: null,
    cornerRadius: null,
    units: "MM",
  };
  const cmds = [];
  const unknown = [];

  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("$$")) continue;
    const up = line.toUpperCase();
    const verb = up.split(/[\s/]/)[0];

    if (up.startsWith("PARTNO")) {
      meta.partno = line.split("/")[1]?.trim() || meta.partno;
      continue;
    }
    if (up.startsWith("UNITS")) {
      meta.units = line.split("/")[1]?.trim().toUpperCase() || "MM";
      continue;
    }
    if (up.startsWith("PPRINT")) {
      const msg = line.split("/").slice(1).join("/").trim();
      const take = (re) => msg.match(re)?.[1]?.trim();
      meta.date = take(/DATE\s*TIME\s*:\s*(.+)/i) || meta.date;
      meta.opName = take(/OPERATION\s+NAME\s*:\s*(.+)/i) || meta.opName;
      meta.seqName = take(/NC\s+SEQUENCE\s+NAME\s*:\s*(.+)/i) || meta.seqName;
      meta.seqType = take(/SEQUENCE\s+TYPE\s*:\s*(.+)/i) || meta.seqType;
      meta.toolName = take(/TOOL\s+NAME\s*:\s*(.+)/i) || meta.toolName;
      meta.toolType = take(/TOOL_TYPE\s*:\s*(.+)/i) || meta.toolType;
      const diam = take(/^CUTTER_DIAM\s*:\s*([\d.+-]+)/i);
      const len = take(/^LENGTH\s*:\s*([\d.+-]+)/i);
      const flute = take(/^FLUTE_LENGTH\s*:\s*([\d.+-]+)/i);
      const rad = take(/^CORNER_RADIUS\s*:\s*([\d.+-]+)/i);
      if (diam) meta.cutterDiam = Number(diam);
      if (len) meta.toolLength = Number(len);
      if (flute) meta.fluteLength = Number(flute);
      if (rad) meta.cornerRadius = Number(rad);
      continue;
    }
    if (up.startsWith("MACHIN")) continue;

    if (up.startsWith("MULTAX")) {
      const on = /ON/i.test(line.split("/")[1] || "");
      cmds.push({ type: "MULTAX", on });
      continue;
    }

    if (up.startsWith("LOADTL")) {
      cmds.push({
        type: "LOADTL",
        tool: Number(line.split("/")[1]?.split(",")[0].trim()),
      });
      continue;
    }
    if (up.startsWith("SPINDL")) {
      const body = line.split("/")[1] || "";
      if (/OFF/i.test(body)) cmds.push({ type: "SPINDL_OFF" });
      else {
        cmds.push({
          type: "SPINDL",
          rpm: Number(body.match(/([\d.]+)/)?.[1] || 0),
          clw: /CLW/i.test(body) && !/CCLW/i.test(body),
        });
      }
      continue;
    }
    if (up.startsWith("RAPID")) {
      cmds.push({ type: "RAPID" });
      continue;
    }
    if (up.startsWith("FEDRAT")) {
      cmds.push({
        type: "FEDRAT",
        feed: Number(line.split("/")[1]?.split(",")[0].trim()),
      });
      continue;
    }
    if (up.startsWith("CUTCOM")) {
      const body = (line.split("/")[1] || "").trim().toUpperCase();
      let mode = "OFF";
      if (body.startsWith("LEFT")) mode = "LEFT";
      else if (body.startsWith("RIGHT")) mode = "RIGHT";
      cmds.push({ type: "CUTCOM", mode });
      continue;
    }
    if (up.startsWith("GOTO")) {
      const nums = parseNumberList(line.split("/")[1] || "");
      if (nums.length < 3 || nums.some((n) => Number.isNaN(n))) {
        die(`Bad GOTO: ${line}`);
      }
      const cmd = { type: "GOTO", x: nums[0], y: nums[1], z: nums[2] };
      if (nums.length >= 6) {
        cmd.i = nums[3];
        cmd.j = nums[4];
        cmd.k = nums[5];
      }
      cmds.push(cmd);
      continue;
    }
    if (up.startsWith("CIRCLE")) {
      const nums = parseNumberList(line.split("/")[1] || "");
      if (nums.length < 7 || nums.some((n) => Number.isNaN(n))) {
        die(`Bad CIRCLE: ${line}`);
      }
      cmds.push({
        type: "CIRCLE",
        x: nums[0],
        y: nums[1],
        z: nums[2],
        i: nums[3],
        j: nums[4],
        k: nums[5],
        r: nums[6],
      });
      continue;
    }
    if (up.startsWith("FINI")) {
      cmds.push({ type: "FINI" });
      continue;
    }
    if (up.startsWith("FROM") || up.startsWith("COOLNT")) {
      cmds.push({ type: "IGNORE", raw: line });
      unknown.push(line);
      continue;
    }
    if (/^[A-Z]/.test(up) && !KNOWN_NCL_CMDS.includes(verb)) {
      unknown.push(line);
    }
  }

  return { meta, cmds, unknown };
}

function collectWarnings(meta, cmds, unknown) {
  const warnings = [];
  const loadtls = cmds.filter((c) => c.type === "LOADTL");
  if (loadtls.length > 1) {
    warnings.push(
      `Multiple LOADTL (${loadtls.length}) — basic multi-tool output; check TOOL CALL blocks`
    );
  }

  let firstAC = null;
  let axisChanges = 0;
  for (const c of cmds) {
    if (c.type === "GOTO" && c.i != null) {
      const ac = toolAxisToAC(c.i, c.j, c.k);
      if (!firstAC) firstAC = ac;
      else if (
        !nearly(ac.A, firstAC.A, ANGLE_EPS) ||
        !nearly(ac.C, firstAC.C, ANGLE_EPS)
      ) {
        axisChanges += 1;
      }
    }
  }
  if (axisChanges > 0) {
    warnings.push(
      `Tool axis A/C changes mid-program (${axisChanges}) — single plane only; not full multi-orientation`
    );
  }

  for (const u of unknown || []) {
    warnings.push(`Unsupported/ignored NCL: ${u.slice(0, 80)}`);
  }

  // COOLNT з NCL ігноруємо: цех завжди M3 M8 / M5 M9

  return warnings;
}

function toolKind(meta) {
  if (meta.toolType && /THREAD/i.test(meta.toolType)) return "THREAD MILL";
  if (meta.toolType && /END\s*MILL/i.test(meta.toolType)) return "FLAT";
  if (meta.toolType && /BALL/i.test(meta.toolType)) return "BALL";
  if (meta.toolType && /BULL/i.test(meta.toolType)) return "BULL";
  return meta.toolType || "TOOL";
}

function toolComment(meta, toolNo) {
  const t = String(toolNo).padStart(3, "0");
  const kind = toolKind(meta);
  const tn = { plus: false, keepLeadingZero: true };
  const d = meta.cutterDiam != null ? fmtNum(meta.cutterDiam, tn) : "?";
  const l = meta.toolLength != null ? fmtNum(meta.toolLength, tn) : "?";
  let radius = "";
  if (kind === "BALL" && meta.cutterDiam != null) {
    radius = ` R=${fmtNum(meta.cutterDiam / 2, tn)}`;
  } else if (kind === "BULL" && meta.cornerRadius != null) {
    radius = ` RAD=${fmtNum(meta.cornerRadius, tn)}`;
  }
  const flute =
    meta.fluteLength != null && !nearly(meta.fluteLength, 0)
      ? ` FLUTE=${fmtNum(meta.fluteLength, tn)}`
      : "";
  const name = meta.toolName
    ? kind === "BALL"
      ? `  (${meta.toolName})`
      : ` (${meta.toolName})`
    : "";
  return `;T${t}  : ${kind} D=${d}${radius} L=${l}${flute}${name}`;
}

function headMCode(horizontal, machine = MACHINE_DMU80P) {
  return horizontal ? machine.horizontalHeadM : machine.verticalHeadM;
}

function q339Line(q339, machine = MACHINE_DMU80P) {
  const q = q339 != null ? q339 : machine.q339;
  if (q == null || q === "") return ` Q339=    ;DATUM NUMBER`;
  return `    Q339=${q}   ;DATUM NUMBER`;
}

/** Після TOOL CALL: підйом шпинделя + СОЖ (цех). */
function afterToolCallLines(horizontal, machine = MACHINE_DMU80P) {
  const m91 = machine.machineCoordM;
  return [
    `L Z-1 F MAX ${m91}`,
    machine.coolantOn,
    machine.spindleOrientM,
    headMCode(horizontal, machine),
  ];
}

function toolCallAxis(horizontal) {
  return horizontal ? "Y" : "Z";
}

function buildHeader(
  pgm,
  meta,
  toolNo,
  rpm,
  machine = MACHINE_DMU80P,
  { horizontal = false, q339 = null } = {}
) {
  const date = meta.date || new Date().toISOString();
  const op = meta.opName || "OP010";
  const seqLabel = meta.seqType || meta.seqName || "SEQ";
  const toolName = meta.toolName || `T${toolNo}`;
  const m91 = machine.machineCoordM;
  const axis = toolCallAxis(horizontal);

  return [
    `BEGIN PGM ${pgm} ${meta.units || "MM"}`,
    `;DATE : ${date}`,
    `;MACHINE : ${machine.name}`,
    `;${op}`,
    toolComment(meta, toolNo),
    `CYCL DEF 7.0 DATUM SHIFT`,
    `CYCL DEF 7.1 X+0`,
    `CYCL DEF 7.2 Y+0`,
    `CYCL DEF 7.3 Z+0`,
    `CYCL DEF 247 DATUM SETTING ~`,
    q339Line(q339, machine),
    `CYCL DEF 19.0 ${machine.planeComment}`,
    `CYCL DEF 19.1 A+0. C+0. F AUTO SET UP 10`,
    `L Z-1 F MAX ${m91}`,
    `L A+Q120 C+Q122 F MAX`,
    `;SEQ ${pgm} (${seqLabel})`,
    `;${toolName}`,
    `TOOL CALL ${toolNo} ${axis} S${Math.round(rpm || 0)}`,
    ...afterToolCallLines(horizontal, machine),
    `CYCL DEF 7.0 DATUM SHIFT`,
    `CYCL DEF 7.1 X+0`,
    `CYCL DEF 7.2 Y+0`,
    `CYCL DEF 7.3 Z+0`,
  ];
}

function buildTiltPlaneBlock(A, C, machine = MACHINE_DMU80P) {
  const m91 = machine.machineCoordM;
  return [
    `L Z-1 F MAX ${m91}`,
    `CYCL DEF 19.0 ${machine.planeComment}`,
    `CYCL DEF 19.1 A${fmtAngle(A)} C${fmtAngle(C)} F AUTO SET UP 10`,
    `L A+Q120 C+Q122 F MAX`,
  ];
}

/** M53: зміщення перед обробкою (після CYCL 19, якщо площина є). */
function buildM53DatumShift(machine = MACHINE_DMU80P) {
  const y = machine.m53DatumY;
  const z = machine.m53DatumZ;
  return [
    `CYCL DEF 7.0 DATUM SHIFT`,
    `CYCL DEF 7.1 Y${fmtNum(y, { plus: true })}`,
    `CYCL DEF 7.2 Z${fmtNum(z, { plus: true })}`,
  ];
}

function buildToolChangeBlock(
  toolNo,
  rpm,
  meta,
  machine = MACHINE_DMU80P,
  { horizontal = false } = {}
) {
  const toolName = meta.toolName || `T${toolNo}`;
  const axis = toolCallAxis(horizontal);
  return [
    machine.coolantOff,
    `;${toolName}`,
    toolComment(meta, toolNo),
    `TOOL CALL ${toolNo} ${axis} S${Math.round(rpm || 0)}`,
    ...afterToolCallLines(horizontal, machine),
  ];
}

/** Вертикальна голова (M54) — класичний кінець. */
function buildFooterVertical(pgm, units, machine = MACHINE_DMU80P) {
  const m91 = machine.machineCoordM;
  return [
    machine.coolantOff,
    `L Z-1 F MAX ${m91}`,
    `CYCL DEF 19.0 WORKING PLANE`,
    `CYCL DEF 19.1 A+0 C+0 F AUTO SET UP 10`,
    `L A+Q120 C+Q122 F MAX`,
    `CYCL DEF 19.0 WORKING PLANE`,
    `CYCL DEF 19.1`,
    machine.endToolCall,
    `L X+1 Y-1 F MAX ${m91}`,
    machine.endM,
    `END PGM ${pgm} ${units || "MM"}`,
  ];
}

/** Горизонтальна голова (M53) — класичний кінець + повернення M54. */
function buildFooterHorizontal(pgm, units, machine = MACHINE_DMU80P) {
  const m91 = machine.machineCoordM;
  return [
    `L Y-1 F MAX ${m91}`,
    `CYCL DEF 7.0 DATUM SHIFT`,
    `CYCL DEF 7.1 Y+0`,
    `CYCL DEF 7.2 Z+0`,
    machine.coolantOff,
    `M54`,
    machine.endToolCall,
    `L X+1 Y-1 F MAX ${m91}`,
    `CYCL DEF 19.0 WORKING PLANE`,
    `CYCL DEF 19.1 A+0 C+0 F AUTO SET UP 10`,
    `L A+Q120 C+Q122 F MAX`,
    `CYCL DEF 19.0 WORKING PLANE`,
    `CYCL DEF 19.1`,
    machine.endM,
    `END PGM ${pgm} ${units || "MM"}`,
  ];
}

function buildFooter(
  pgm,
  units,
  machine = MACHINE_DMU80P,
  { horizontal = false } = {}
) {
  return horizontal
    ? buildFooterHorizontal(pgm, units, machine)
    : buildFooterVertical(pgm, units, machine);
}

/** Голова горизонтальна, якщо вектор інструмента майже лежачий (A≈90). */
function detectHorizontalHead(planeA, cmds) {
  if (Math.abs(Math.abs(planeA) - 90) < 5) return true;
  for (const c of cmds) {
    if (c.type === "GOTO" && c.i != null) {
      const n = Math.hypot(c.i, c.j, c.k) || 1;
      return Math.abs(c.k / n) < 0.15;
    }
  }
  return false;
}

function radiusWord(cutcom) {
  if (cutcom === "LEFT") return "RL";
  if (cutcom === "RIGHT") return "RR";
  return "R0";
}

function angleDeltaDeg(cx, cy, x0, y0, x1, y1, k) {
  if (nearly(x0, x1) && nearly(y0, y1)) return k >= 0 ? 360 : -360;
  const a0 = Math.atan2(y0 - cy, x0 - cx);
  const a1 = Math.atan2(y1 - cy, x1 - cx);
  let d = ((a1 - a0) * 180) / Math.PI;
  if (k >= 0) {
    while (d <= 0) d += 360;
    while (d > 360) d -= 360;
  } else {
    while (d >= 0) d -= 360;
    while (d < -360) d += 360;
  }
  return d;
}

function detectPlaneAngles(cmds) {
  for (const c of cmds) {
    if (c.type === "GOTO" && c.i != null) {
      return toolAxisToAC(c.i, c.j, c.k);
    }
  }
  return { A: 0, C: 0 };
}

function convert(meta, cmds, pgmName, options = {}) {
  const warnings = options.warnings || [];
  let toolNo = 1;
  let rpm = 0;
  let spindleClw = true;
  let rapid = false;
  let feed = null;
  let cutcom = "OFF";
  let pos = { x: null, y: null, z: null };
  let pendingCircle = null;
  let spindleOnEmitted = false;
  let multax = false;
  let planeReady = false;
  let modalFeed = null;
  let modalR = null;
  let motionStarted = false;
  let activeTool = null;
  let pendingTool = null;

  let headerTool = 1;
  let headerRpm = 0;
  let sawLoad = false;
  for (const c of cmds) {
    if (c.type === "LOADTL") {
      if (!sawLoad) {
        headerTool = c.tool;
        sawLoad = true;
      }
      toolNo = c.tool;
    }
    if (c.type === "SPINDL") {
      if (!headerRpm && c.rpm) headerRpm = c.rpm;
      rpm = c.rpm;
      spindleClw = c.clw;
    }
    if (c.type === "MULTAX") multax = c.on;
  }
  rpm = headerRpm || rpm;
  toolNo = headerTool;

  // Перший MULTAX-вектор: M54 → toolAxisToAC; M53 → стіл A0 / C=atan2(-i,j)
  let sampleIjk = null;
  for (const c of cmds) {
    if (c.type === "GOTO" && c.i != null) {
      sampleIjk = c;
      break;
    }
  }
  const toolAC = sampleIjk
    ? toolAxisToAC(sampleIjk.i, sampleIjk.j, sampleIjk.k)
    : { A: 0, C: 0 };
  const horizontal = detectHorizontalHead(toolAC.A, cmds);
  const planeAC = horizontal && sampleIjk
    ? horizontalPlaneAC(sampleIjk.i, sampleIjk.j, sampleIjk.k)
    : toolAC;
  const planeA = planeAC.A;
  const planeC = planeAC.C;
  // M53: CYCL 19 лише якщо стіл реально повернутий (C≠0); НЕ A+90 C+180
  const useTilt =
    Math.abs(planeA) > ANGLE_EPS || Math.abs(planeC) > ANGLE_EPS;
  // СОЖ уже в шапці після TOOL CALL — не дублювати M3 на першому кадрі
  spindleOnEmitted = true;

  const body = [];
  const emit = (s) => body.push(s);
  const seqLabel = meta.seqType || meta.seqName || "SEQ";
  let clearanceZ = null;
  let pendingSeqAfterClearance = false;
  let m53DatumReady = false;

  const toOut = (x, y, z) => {
    if (!useTilt)
      return { x: roundCoord(x), y: roundCoord(y), z: roundCoord(z) };
    const p = machineToPlane(x, y, z, planeA, planeC);
    return {
      x: roundCoord(p.x),
      y: roundCoord(p.y),
      z: roundCoord(p.z),
    };
  };

  const ensureTiltPlane = () => {
    if (!useTilt || planeReady) return;
    for (const line of buildTiltPlaneBlock(planeA, planeC)) emit(line);
    planeReady = true;
    modalFeed = null;
    modalR = null;
  };

  /** M53: DATUM SHIFT Y/Z перед різзю; якщо є CYCL 19 — одразу після нього. */
  const ensureM53Datum = () => {
    if (!horizontal || m53DatumReady) return;
    for (const line of buildM53DatumShift(MACHINE_DMU80P)) emit(line);
    m53DatumReady = true;
    modalFeed = null;
    modalR = null;
  };

  const ensurePreMachining = () => {
    ensureTiltPlane();
    ensureM53Datum();
  };

  const maybeEmitSeqComment = () => {
    if (!pendingSeqAfterClearance) return;
    emit(`;SEQ ${pgmName} (${seqLabel})`);
    pendingSeqAfterClearance = false;
    modalFeed = null;
  };

  const flushToolChange = () => {
    if (pendingTool == null) return;
    if (activeTool != null && pendingTool !== activeTool && motionStarted) {
      for (const line of buildToolChangeBlock(
        pendingTool,
        rpm,
        meta,
        MACHINE_DMU80P,
        { horizontal }
      )) {
        emit(line);
      }
      spindleOnEmitted = true; // M3 M8 already in tool-change block
      modalFeed = null;
      modalR = null;
      if (useTilt) planeReady = false;
      if (horizontal) m53DatumReady = false;
    }
    activeTool = pendingTool;
    toolNo = pendingTool;
    pendingTool = null;
  };

  const emitLinear = (x, y, z, { forceR = false } = {}) => {
    flushToolChange();
    ensurePreMachining();
    maybeEmitSeqComment();
    const first = pos.x == null;
    const sameX = !first && nearly(x, pos.x);
    const sameY = !first && nearly(y, pos.y);
    const sameZ = !first && nearly(z, pos.z);

    const mCodes = [];
    if (!spindleOnEmitted && rpm > 0) {
      mCodes.push(spindleClw ? "M3" : "M4");
      spindleOnEmitted = true;
    }

    const wantR = (() => {
      if (cutcom === "LEFT" || cutcom === "RIGHT") return radiusWord(cutcom);
      if (forceR || first || mCodes.length) return "R0";
      return null;
    })();

    const wantFeed = rapid ? "MAX" : feed;

    if (rapid) {
      // M53: підхід по Y (як Z на вертикалі) — спочатку Y, потім XZ
      if (horizontal) {
        const xzChange = first || !sameX || !sameZ;
        const yChange = first || !sameY;
        if (xzChange && yChange) {
          const yParts = ["L", fmtAxis("Y", y)];
          if (wantR && wantR !== modalR) {
            yParts.push(wantR);
            modalR = wantR;
          } else if (mCodes.length && !modalR) {
            yParts.push("R0");
            modalR = "R0";
          }
          yParts.push("F MAX");
          modalFeed = "MAX";
          for (const m of mCodes) yParts.push(m);
          emit(yParts.join(" "));
          const xzParts = ["L"];
          if (first || !sameX) xzParts.push(fmtAxis("X", x));
          if (first || !sameZ) xzParts.push(fmtAxis("Z", z));
          xzParts.push("F MAX");
          emit(xzParts.join(" "));
          if (clearanceZ == null) clearanceZ = y;
          pos = { x, y, z };
          motionStarted = true;
          return;
        }
      } else {
        const xyChange = first || !sameX || !sameY;
        const zChange = first || !sameZ;
        if (xyChange && zChange) {
          const parts = ["L"];
          if (first || !sameX) parts.push(fmtAxis("X", x));
          if (first || !sameY) parts.push(fmtAxis("Y", y));
          if (wantR && wantR !== modalR) {
            parts.push(wantR);
            modalR = wantR;
          } else if (mCodes.length && !modalR) {
            parts.push("R0");
            modalR = "R0";
          }
          parts.push("F MAX");
          modalFeed = "MAX";
          for (const m of mCodes) parts.push(m);
          emit(parts.join(" "));
          emit(`L ${fmtAxis("Z", z)} F MAX`);
          if (clearanceZ == null) clearanceZ = z;
          pos = { x, y, z };
          motionStarted = true;
          return;
        }
      }
      const parts = ["L"];
      if (!sameX) parts.push(fmtAxis("X", x));
      if (!sameY) parts.push(fmtAxis("Y", y));
      if (!sameZ) parts.push(fmtAxis("Z", z));
      if (parts.length === 1 && !mCodes.length) {
        pos = { x, y, z };
        return;
      }
      if (mCodes.length) {
        parts.push("R0");
        modalR = "R0";
      }
      parts.push("F MAX");
      modalFeed = "MAX";
      for (const m of mCodes) parts.push(m);
      emit(parts.join(" "));
      markClearanceRetract(sameX, sameY, sameZ, horizontal ? y : z);
      if (clearanceZ == null) clearanceZ = horizontal ? y : z;
      pos = { x, y, z };
      motionStarted = true;
      return;
    }

    const parts = ["L"];
    if (!sameX) parts.push(fmtAxis("X", x));
    if (!sameY) parts.push(fmtAxis("Y", y));
    if (!sameZ) parts.push(fmtAxis("Z", z));
    if (parts.length === 1 && !mCodes.length) {
      pos = { x, y, z };
      return;
    }

    if (cutcom === "LEFT" || cutcom === "RIGHT") {
      const rw = radiusWord(cutcom);
      parts.push(rw);
      modalR = rw;
      if (feed != null) {
        parts.push(`F${fmtFeed(feed)}`);
        modalFeed = feed;
      }
    } else if (cutcom === "OFF" && forceR) {
      parts.push("R0");
      modalR = "R0";
    } else {
      if (
        wantFeed != null &&
        (modalFeed === null ||
          modalFeed === "MAX" ||
          !nearly(modalFeed, wantFeed))
      ) {
        parts.push(`F${fmtFeed(wantFeed)}`);
        modalFeed = wantFeed;
      }
    }
    for (const m of mCodes) parts.push(m);
    emit(parts.join(" "));
    markClearanceRetract(sameX, sameY, sameZ, horizontal ? y : z);
    if (clearanceZ == null) clearanceZ = horizontal ? y : z;
    pos = { x, y, z };
    motionStarted = true;
  };

  function markClearanceRetract(sameX, sameY, sameZ, approach) {
    if (!useTilt || clearanceZ == null) return;
    if (horizontal) {
      if (sameX && sameZ && !sameY && nearly(approach, clearanceZ)) {
        pendingSeqAfterClearance = true;
      }
    } else if (sameX && sameY && !sameZ && nearly(approach, clearanceZ)) {
      pendingSeqAfterClearance = true;
    }
  }

  const emitGoto = (mx, my, mz) => {
    const p = toOut(mx, my, mz);

    if (pendingCircle) {
      flushToolChange();
      ensurePreMachining();
      const c = pendingCircle;
      pendingCircle = null;
      const cc = toOut(c.x, c.y, c.z);
      // Як у ICAM etalon: CR (не CC/CP). M53 → дуга в XZ; M54 → у XY.
      let ipa;
      if (horizontal) {
        // нормаль кола ≈ ±Y; sense як у ICAM (etalon DR-)
        ipa = angleDeltaDeg(cc.x, cc.z, pos.x, pos.z, p.x, p.z, c.j ?? 1);
      } else {
        ipa = angleDeltaDeg(cc.x, cc.y, pos.x, pos.y, p.x, p.y, c.k);
      }
      const dr = ipa >= 0 ? "DR+" : "DR-";
      const rAbs = Math.abs(c.r != null ? c.r : 0);
      const rWord = `R${fmtNum(rAbs, { plus: true })}`;
      if (feed != null && modalFeed !== feed) {
        emit(`F${fmtFeed(feed)}`);
        modalFeed = feed;
      }
      // etalon: "CR  X… Z… R… DR…" (два пробіли після CR)
      if (horizontal) {
        emit(
          `CR  ${fmtAxis("X", p.x)} ${fmtAxis("Z", p.z)} ${rWord} ${dr}`
        );
      } else {
        emit(
          `CR  ${fmtAxis("X", p.x)} ${fmtAxis("Y", p.y)} ${rWord} ${dr}`
        );
      }
      pos = { x: p.x, y: p.y, z: p.z };
      motionStarted = true;
      return;
    }

    emitLinear(p.x, p.y, p.z);
  };

  activeTool = toolNo;

  for (const c of cmds) {
    switch (c.type) {
      case "LOADTL":
        if (c.tool !== toolNo && motionStarted) {
          pendingTool = c.tool;
        } else {
          toolNo = c.tool;
          activeTool = c.tool;
        }
        break;
      case "SPINDL":
        rpm = c.rpm;
        spindleClw = c.clw;
        if (pendingTool != null) flushToolChange();
        break;
      case "SPINDL_OFF":
        break;
      case "MULTAX":
        multax = c.on;
        break;
      case "RAPID":
        rapid = true;
        break;
      case "FEDRAT":
        feed = c.feed;
        rapid = false;
        break;
      case "CUTCOM":
        cutcom = c.mode;
        break;
      case "CIRCLE":
        pendingCircle = c;
        break;
      case "GOTO":
        if (c.i != null) {
          const ac = horizontal
            ? horizontalPlaneAC(c.i, c.j, c.k)
            : toolAxisToAC(c.i, c.j, c.k);
          if (
            useTilt &&
            planeReady &&
            (!nearly(ac.A, planeA, ANGLE_EPS) ||
              !nearly(ac.C, planeC, ANGLE_EPS))
          ) {
            // warned in collectWarnings; keep first plane
          }
        }
        emitGoto(c.x, c.y, c.z);
        break;
      default:
        break;
    }
  }

  const q339 = options.q339 != null ? options.q339 : MACHINE_DMU80P.q339;
  return {
    lines: [
      ...buildHeader(pgmName, meta, headerTool, headerRpm || rpm, MACHINE_DMU80P, {
        horizontal,
        q339,
      }),
      ...body,
      ...buildFooter(pgmName, meta.units || "MM", MACHINE_DMU80P, {
        horizontal,
      }),
    ],
    useTilt,
    horizontal,
    planeA,
    planeC,
    warnings,
  };
}

function numberBlocks(lines, enabled = MACHINE_DMU80P.numberBlocks) {
  if (!enabled) return lines.slice();
  const out = [];
  let n = 0;
  for (const line of lines) {
    if (/^\s+Q\d+/.test(line)) {
      out.push(line);
      continue;
    }
    out.push(`${n}  ${line}`);
    n += 1;
  }
  return out;
}

function encodeCp1251(str) {
  const out = [];
  for (const ch of str) {
    const code = ch.codePointAt(0);
    if (code < 128) {
      out.push(code);
      continue;
    }
    if (code >= 0x0410 && code <= 0x044f) {
      out.push(code - 0x0410 + 0xc0);
      continue;
    }
    if (code === 0x0401) {
      out.push(0xa8);
      continue;
    }
    if (code === 0x0451) {
      out.push(0xb8);
      continue;
    }
    out.push(0x3f);
  }
  return Buffer.from(out);
}

function decodeCp1251(buf) {
  const map = {};
  for (let i = 0; i < 128; i++) map[i] = String.fromCharCode(i);
  for (let i = 0; i < 64; i++) {
    map[0xc0 + i] = String.fromCharCode(0x0410 + i);
  }
  map[0xa8] = "\u0401";
  map[0xb8] = "\u0451";
  let s = "";
  for (const b of buf) s += map[b] || "?";
  return s;
}

/**
 * Validate Klartext .h structure (no shop etalon required).
 * @returns {{ ok: boolean, errors: string[], info: object }}
 */
function checkKlartext(text, opts = {}) {
  const errors = [];
  const lines = String(text)
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .filter((l) => l.length);

  const bodies = lines.map((l) => l.replace(/^\d+\s\s/, ""));
  const joined = bodies.join("\n");

  if (!bodies.some((l) => l.startsWith("BEGIN PGM"))) {
    errors.push("Missing BEGIN PGM");
  }
  if (!bodies.some((l) => l.startsWith("END PGM"))) {
    errors.push("Missing END PGM");
  }
  const hasEndM =
    bodies.some((l) => l === "M30" || l.startsWith("M30 ")) ||
    bodies.some((l) => l === "M1" || l.startsWith("M1 "));
  if (!hasEndM) {
    errors.push("Missing M1/M30");
  }
  if (!bodies.some((l) => l.startsWith("TOOL CALL"))) {
    errors.push("Missing TOOL CALL");
  }

  const hasBlockNumbers = lines.some((l) => /^\d+\s\s/.test(l));
  let blockCount = bodies.length;
  if (hasBlockNumbers) {
    let expect = 0;
    for (const line of lines) {
      if (/^\s+Q\d+/.test(line)) continue;
      const m = line.match(/^(\d+)\s\s/);
      if (!m) {
        errors.push(`Line without block number: ${line.slice(0, 60)}`);
        break;
      }
      const n = Number(m[1]);
      if (n !== expect) {
        errors.push(`Block number jump: expected ${expect}, got ${n}`);
        break;
      }
      expect += 1;
    }
    blockCount = expect;
  }

  if (opts.expectTilt) {
    const hasTilt = bodies.some(
      (l) =>
        /CYCL DEF 19\.1\s+A[+-]?\d/.test(l) &&
        !/A\+0\.\s+C\+0\./.test(l) &&
        !/A\+0\s+C\+0\b/.test(l)
    );
    if (!hasTilt) {
      errors.push("Expected tilted CYCL DEF 19.1 A/C (non-zero), not found");
    }
  }

  return {
    ok: errors.length === 0,
    errors,
    info: {
      blocks: blockCount,
      hasMachine: joined.includes("MACHINE"),
      toolCalls: bodies.filter((l) => l.startsWith("TOOL CALL")).length,
    },
  };
}

function convertFile(
  inputPath,
  { output, name, numbers = null, q339 = null } = {}
) {
  const text = fs.readFileSync(inputPath, "utf8");
  const { meta, cmds, unknown } = parseNcl(text);
  if (!cmds.length) die(`No motion commands found in ${inputPath}`);

  const warnings = collectWarnings(meta, cmds, unknown);
  for (const w of warnings) console.error(`WARN: ${w}`);

  const pgm = sanitizePgmName(name || defaultPgmName(inputPath, meta));
  const result = convert(meta, cmds, pgm, { warnings, q339 });
  const withNumbers =
    numbers != null ? numbers : MACHINE_DMU80P.numberBlocks;
  const numbered = numberBlocks(result.lines, withNumbers);
  const payload = numbered.join("\r\n") + "\r\n";

  const outPath = path.resolve(output || defaultOutPath(inputPath));
  ensureConvertedDir();
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, encodeCp1251(payload));

  const check = checkKlartext(payload, { expectTilt: result.useTilt });
  if (!check.ok) {
    for (const e of check.errors) console.error(`CHECK FAIL: ${e}`);
  } else {
    console.log(`CHECK: OK (${check.info.blocks} lines)`);
  }

  console.log(`OK: ${outPath}`);
  console.log(`MACHINE: ${MACHINE_DMU80P.name} / ${MACHINE_DMU80P.control}`);
  console.log(`PGM: ${pgm}`);
  console.log(`HEAD: ${result.horizontal ? "M53 horizontal" : "M54 vertical"}`);
  if (result.useTilt) {
    console.log(
      `PLANE: A${fmtAngle(result.planeA)} C${fmtAngle(result.planeC)}`
    );
  }
  console.log(`Lines: ${numbered.length}`);
  if (warnings.length) console.log(`Warnings: ${warnings.length}`);

  return { outPath, numbered, result, check, warnings, pgm };
}

function listNclFiles(dirPath) {
  const abs = path.resolve(dirPath);
  return fs
    .readdirSync(abs)
    .filter((f) => /\.ncl(\.\d+)?$/i.test(f))
    .map((f) => path.join(abs, f))
    .sort();
}

function snapshotKeyLines(numberedLines) {
  const bodies = numberedLines.map((l) => l.replace(/^\d+\s\s/, ""));
  const keys = bodies.filter(
    (l) =>
      l.startsWith("BEGIN PGM") ||
      l.startsWith("TOOL CALL") ||
      l.startsWith("CYCL DEF 19.1") ||
      l.startsWith("END PGM") ||
      l === "M30" ||
      /^L .*M3$/.test(l) ||
      l.startsWith("CC ") ||
      l.startsWith("CP ")
  );
  return keys;
}

function hashPayload(str) {
  return crypto.createHash("sha256").update(str).digest("hex").slice(0, 16);
}

function runBatch(files, opts = {}) {
  if (!files.length) {
    die(
      "Немає .ncl / .ncl.1.\n" +
        "Usage:\n" +
        "  node convert.js <file.ncl.1> [-o out.h] [--name PGM8] [--q339 4]\n" +
        "  node convert.js --dir <folder>\n" +
        "  node convert.js --numbers …\n" +
        "  node convert.js --check <file.h>"
    );
  }
  console.log(`Batch: ${files.length} file(s)`);
  let failed = 0;
  for (const f of files) {
    console.log(`--- ${path.basename(f)}`);
    try {
      const r = convertFile(f, opts);
      if (!r.check.ok) failed += 1;
    } catch (e) {
      console.error(`ERROR: ${e.message || e}`);
      failed += 1;
    }
  }
  if (failed) die(`Batch finished with ${failed} failure(s)`);
}

function main() {
  const args = parseArgs(process.argv);
  const fileOpts = {
    numbers: args.numbers ? true : null,
    q339: args.q339,
    name: args.name,
    output: args.output,
  };

  if (args.check) {
    const p = path.resolve(args.check);
    if (!fs.existsSync(p)) die(`File not found: ${p}`);
    const buf = fs.readFileSync(p);
    const text = decodeCp1251(buf);
    const res = checkKlartext(text);
    if (res.ok) {
      console.log(`CHECK OK: ${p}`);
      console.log(`Lines: ${res.info.blocks}, TOOL CALL: ${res.info.toolCalls}`);
      process.exit(0);
    }
    console.error(`CHECK FAIL: ${p}`);
    for (const e of res.errors) console.error(`  - ${e}`);
    process.exit(1);
  }

  if (args.dir) {
    runBatch(listNclFiles(args.dir), fileOpts);
    return;
  }

  if (!args.input) {
    runBatch(collectDefaultNclFiles(), fileOpts);
    return;
  }

  const input = resolveNclInput(args.input);
  if (!input) die(`File not found: ${args.input}`);
  convertFile(input, fileOpts);
}

if (require.main === module) main();

module.exports = {
  parseNcl,
  convert,
  numberBlocks,
  sanitizePgmName,
  encodeCp1251,
  decodeCp1251,
  MACHINE_DMU80P,
  toolAxisToAC,
  horizontalPlaneAC,
  machineToPlane,
  toolComment,
  toolKind,
  checkKlartext,
  convertFile,
  collectWarnings,
  snapshotKeyLines,
  hashPayload,
  fmtNum,
  ANGLE_EPS,
  CONVERTED_DIR,
  defaultOutPath,
  listNclFiles,
};
