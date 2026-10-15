// Frozen GS numeric helpers; see gs_reference_provenance.json.
const CONV_AB = new Map([
  ["T9", -1],
  ["R0", 1],
  ["R1", 2],
  ["R2", 3],
  ["R3", 4],
  ["R4", 5],
  ["R5", 6],
  ["R6", 7],
  ["R7", 8],
  ["R8", 9],
  ["R9", 10],
  ["R10", 11],
  ["R11", 12],
  ["R12", 13],
  ["R13", 14],
  ["R14", 15],
  ["R15", 16],
  ["SR0", 17],
  ["SR1", 18],
  ["SR2", 19],
  ["SR3", 20],
  ["SR4", 21],
  ["SR5", 22],
  ["SR6", 23],
  ["SR7", 24],
  ["SR8", 25],
  ["SR9", 26],
  ["SR10", 27],
  ["SR11", 28],
  ["SR12", 29],
  ["SR13", 30],
  ["SR14", 31],
  ["SR15", 32],
]);

const CONV_CD = new Map([
  ["+", "<="],
  ["-", ">"],
]);



function meetWithMaps(v, rule) {
  if (v === "" || v === null || rule === "" || rule === null) return true;

  const mapAB = CONV_AB;
  const mapCD = CONV_CD;

  function parseValue(raw) {
    const str = String(raw)
      .trim()
      .replace(/\s+/g, "")
      .replace(/：/g, ":");
    if (str === "") return { num: NaN, op: null };

    let n = Number(str);
    if (!Number.isNaN(n)) return { num: n, op: null };
    if (mapAB.has(str)) return { num: mapAB.get(str), op: null };

    const last = str.slice(-1);
    const op = mapCD.has(last) ? mapCD.get(last) : null;
    const core = op ? str.slice(0, -1).trim() : str;

    if (core !== str) {
      n = Number(core);
      if (!Number.isNaN(n)) return { num: n, op };
      if (mapAB.has(core)) return { num: mapAB.get(core), op };
    }

    return { num: NaN, op };
  }

  const vParsed = parseValue(v);
  const rParsed = parseValue(rule);
  const op = rParsed.op || "<=";

  if (Number.isNaN(vParsed.num) || Number.isNaN(rParsed.num)) return true;

  switch (op) {
    case ">":  return vParsed.num <= rParsed.num;
    case "<=": return vParsed.num >= rParsed.num;
    case ">=": return vParsed.num >= rParsed.num;
    case "<":  return vParsed.num <= rParsed.num;
    case "=":  return vParsed.num === rParsed.num;
    default:   return vParsed.num >= rParsed.num;
  }
}

function _toScoreNumber(raw) {
  if (raw === "" || raw == null) return NaN;
  const str = String(raw).trim();
  if (!str) return NaN;

  let n = Number(str);
  if (!Number.isNaN(n)) return n;
  if (CONV_AB.has(str)) return CONV_AB.get(str);

  const last = str.slice(-1);
  const core = (last === "+" || last === "-") ? str.slice(0, -1).trim() : str;
  n = Number(core);
  if (!Number.isNaN(n)) return n;
  if (CONV_AB.has(core)) return CONV_AB.get(core);
  return NaN;
}

function _truncatePercentInteger(n) {
  if (!Number.isFinite(n)) return 0;
  return Math.floor(n);
}

function _buildCompetitionRankMap(memberRows, scoreByRow) {
  const sortedRows = memberRows.slice().sort((a, b) => {
    const aScore = scoreByRow.has(a) ? scoreByRow.get(a) : 0;
    const bScore = scoreByRow.has(b) ? scoreByRow.get(b) : 0;
    if (bScore !== aScore) return bScore - aScore;
    return a - b;
  });
  const rankMap = new Map();
  let prevScore = null;
  let prevRank = 0;
  for (let i = 0; i < sortedRows.length; i += 1) {
    const row = sortedRows[i];
    const score = scoreByRow.has(row) ? scoreByRow.get(row) : 0;
    const rank = (prevScore !== null && score === prevScore) ? prevRank : (i + 1);
    rankMap.set(row, rank);
    prevScore = score;
    prevRank = rank;
  }
  return rankMap;
}
