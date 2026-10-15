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

function _collectScoreWritesForSheetRows(sh, targetRows, sharedCtx, affectedStandardIndexSet, affectedLabelSet) {
  if (sh.getName() === SHEET_TRAINING_CHANGE) return [];

  const ctx = sharedCtx || _buildScoreSharedContext(_getTargetSpreadsheet());
  if (!ctx) {
    Logger.log("score-standard collect: sheet=%s sharedCtx=null", sh.getName());
    return [];
  }
  const dataStartCol = 2;
  const dataColOffset = dataStartCol - 1;
  const scoreStdCtx = ctx.scoreStdCtx;
  const nicknameLookup = ctx.nicknameLookup;
  const resultMemberRowMap = ctx.resultLookup.memberRowMap;
  const resultCharacterColMap = ctx.resultLookup.characterColMap;
  const resultOutputCharacterColMap = ctx.resultLookup.outputCharacterColMap || new Map();
  const resultOutputTotalColByElement = ctx.resultLookup.outputTotalColByElement;
  const sourceElementKey = normalizeLabel(_displayNameFromSheet(sh.getName()));
  const sheetCfg = _getScoreSheetConfig(
    sh,
    dataStartCol,
    scoreStdCtx,
    resultCharacterColMap,
    resultOutputCharacterColMap,
    nicknameLookup
  );
  if (!sheetCfg || !sheetCfg.characterConfigs.length) {
    Logger.log(
      "score-standard collect: sheet=%s cfg=0 sourceElement=%s resultCols=%s",
      sh.getName(),
      sourceElementKey,
      resultCharacterColMap.size
    );
    return [];
  }
  const sourceStartRow = _getScoreSourceStartRow(sh.getName());
  const sourceProfile = getMemberRowProfileByNameSheet(sourceStartRow, false);
  const rosterCount = sourceProfile.spanCount;
  if (!sourceProfile.count || rosterCount <= 0) return [];
  const activeSourceRows = new Set(sourceProfile.rows);
  const memberNameByRow = new Map();
  for (let i = 0; i < sourceProfile.rows.length; i += 1) {
    memberNameByRow.set(sourceProfile.rows[i], sourceProfile.names[i]);
  }
  const sourceEndRow = sourceStartRow + rosterCount - 1;

  const uniqueRows = Array.from(
    new Set((targetRows || []).filter(r => activeSourceRows.has(r) && r >= sourceStartRow && r <= sourceEndRow))
  ).sort((a, b) => a - b);
  if (!uniqueRows.length) {
    Logger.log(
      "score-standard collect: sheet=%s no-target-rows sourceStart=%s sourceEnd=%s",
      sh.getName(),
      sourceStartRow,
      sourceEndRow
    );
    return [];
  }
  const rowSet = new Set(uniqueRows);

  const rowsData = sh.getRange(
    sourceStartRow,
    1,
    rosterCount,
    sheetCfg.dataEndCol
  ).getValues();

  const allCharacterConfigs = sheetCfg.characterConfigs.map(cfg => ({
    writeCols: cfg.writeCols,
    outputCols: cfg.outputCols,
    labelNorm: cfg.labelNorm,
    totalWeight: cfg.totalWeight,
    terms: cfg.terms,
    rowRawMap: new Map(),
    maxRaw: 0,
  }));
  const outputTotalConfigs = allCharacterConfigs.filter(cfg => cfg.outputCols && cfg.outputCols.length);
  let characterConfigs = allCharacterConfigs;
  const allConfigLabels = allCharacterConfigs.map(cfg => cfg.labelNorm);
  if (affectedLabelSet && affectedLabelSet.size) {
    characterConfigs = characterConfigs.filter(cfg => affectedLabelSet.has(cfg.labelNorm));
    if (!characterConfigs.length) return [];
  }
  if (affectedStandardIndexSet && affectedStandardIndexSet.size) {
    characterConfigs = characterConfigs.filter(cfg =>
      cfg.terms.some(t => affectedStandardIndexSet.has(t.sIdx))
    );
    if (!characterConfigs.length) return [];
  }
  Logger.log(
    "score-standard collect: sheet=%s rows=%s cfgAll=%s cfgFiltered=%s affectedLabels=%s cfgLabels=%s",
    sh.getName(),
    uniqueRows.length,
    allCharacterConfigs.length,
    characterConfigs.length,
    affectedLabelSet ? JSON.stringify(Array.from(affectedLabelSet)) : "[]",
    JSON.stringify(allConfigLabels)
  );

  const rowTotalRawMap = new Map();
  let totalMaxRaw = 0;

  for (let offsetRow = 0; offsetRow < rowsData.length; offsetRow += 1) {
    const row = sourceStartRow + offsetRow;
    if (!activeSourceRows.has(row)) continue;
    const rowData = rowsData[offsetRow];
    const memberNameRaw = memberNameByRow.get(row) || rowData[0];
    if (memberNameRaw == null || memberNameRaw === "") continue;

    allCharacterConfigs.forEach(cfg => {
      let sum = 0;
      let hasAnyTerm = false;
      for (let i = 0; i < cfg.terms.length; i += 1) {
        const tIdx = cfg.terms[i].tIdx;
        const sIdx = cfg.terms[i].sIdx;
        const valueNum = _toScoreNumber(rowData[dataColOffset + tIdx]);
        const weightNum = _toScoreNumber(scoreStdCtx.standardWeightValues[sIdx]);
        if (Number.isNaN(valueNum) || Number.isNaN(weightNum)) continue;
        sum += valueNum * weightNum;
        hasAnyTerm = true;
      }
      if (!hasAnyTerm) return;

      cfg.rowRawMap.set(row, sum);
      if (sum > 0 && sum > cfg.maxRaw) cfg.maxRaw = sum;
    });
  }

  for (let offsetRow = 0; offsetRow < rowsData.length; offsetRow += 1) {
    const row = sourceStartRow + offsetRow;
    if (!activeSourceRows.has(row)) continue;
    let rawTotal = 0;
    let hasAny = false;
    for (let i = 0; i < outputTotalConfigs.length; i += 1) {
      const cfg = outputTotalConfigs[i];
      const raw = cfg.rowRawMap.has(row) ? cfg.rowRawMap.get(row) : NaN;
      if (Number.isNaN(raw) || raw <= 0 || cfg.maxRaw <= 0 || cfg.totalWeight <= 0) continue;
      const pct = _truncatePercentInteger(Math.min(100, Math.max(0, (raw / cfg.maxRaw) * 100)));
      rawTotal += pct * cfg.totalWeight;
      hasAny = true;
    }
    if (!hasAny) continue;
    const rounded = Math.round(rawTotal);
    rowTotalRawMap.set(row, rounded);
    if (rounded > 0 && rounded > totalMaxRaw) totalMaxRaw = rounded;
  }

  Logger.log(
    "score-standard collect: sheet=%s charMaxes=%s totalMax=%s",
    sh.getName(),
    JSON.stringify(characterConfigs.map(cfg => ({ label: cfg.labelNorm, maxRaw: cfg.maxRaw }))),
    totalMaxRaw
  );

  const writes = [];
  for (let offsetRow = 0; offsetRow < rowsData.length; offsetRow += 1) {
    const row = sourceStartRow + offsetRow;
    if (!rowSet.has(row)) continue;
    const memberKey = String(memberNameByRow.get(row) || rowsData[offsetRow][0] || "").trim();
    if (!memberKey || !resultMemberRowMap.has(memberKey)) continue;
    const resultRow = resultMemberRowMap.get(memberKey);

    for (let i = 0; i < characterConfigs.length; i += 1) {
      const cfg = characterConfigs[i];
      const raw = cfg.rowRawMap.has(row) ? cfg.rowRawMap.get(row) : NaN;
      let score = 0;
      if (!Number.isNaN(raw) && raw > 0 && cfg.maxRaw > 0) {
        const pct = (raw / cfg.maxRaw) * 100;
        score = _truncatePercentInteger(Math.min(100, Math.max(0, pct)));
      }
      for (let j = 0; j < cfg.writeCols.length; j += 1) {
        writes.push({
          row: resultRow,
          col: cfg.writeCols[j],
          value: score,
        });
      }
    }

    const outputTotalCol = resultOutputTotalColByElement.get(sourceElementKey);
    if (outputTotalCol) {
      const rawTotal = rowTotalRawMap.has(row) ? rowTotalRawMap.get(row) : NaN;
      let totalScore = 0;
      if (!Number.isNaN(rawTotal) && rawTotal > 0 && totalMaxRaw > 0) {
        totalScore = _truncatePercentInteger(Math.min(100, Math.max(0, (rawTotal / totalMaxRaw) * 100)));
      }
      writes.push({
        row: resultRow,
        col: outputTotalCol,
        value: totalScore,
      });
    }
  }

  Logger.log(
    "score-standard collect: sheet=%s writes=%s outputTotalCol=%s",
    sh.getName(),
    writes.length,
    resultOutputTotalColByElement.has(sourceElementKey)
      ? resultOutputTotalColByElement.get(sourceElementKey)
      : ""
  );
  return writes;
}

function _appendScoreWritesToBatch(batch, writes) {
  if (!batch || !writes || !writes.length) return 0;
  let appended = 0;
  for (let i = 0; i < writes.length; i += 1) {
    const w = writes[i];
    const row = Number(w && w.row);
    const col = Number(w && w.col);
    if (!Number.isInteger(row) || row <= 0 || !Number.isInteger(col) || col <= 0) {
      Logger.log("score batch skip invalid write: %s", JSON.stringify(w || null));
      continue;
    }
    if (!batch.rowColMap.has(row)) batch.rowColMap.set(row, new Map());
    batch.rowColMap.get(row).set(col, w.value);
    appended += 1;
  }
  return appended;
}

function _buildScoreTotalWeightMap(scoreStdCtx) {
  const map = new Map();
  if (!scoreStdCtx || !scoreStdCtx.standardGroups || !scoreStdCtx.standardLabels) return map;
  for (let i = 0; i < scoreStdCtx.standardGroups.length; i += 1) {
    const labelNorm = normalizeLabel(scoreStdCtx.standardLabels[i]);
    if (!labelNorm || map.has(labelNorm)) continue;
    const startCol = scoreStdCtx.standardGroups[i].start;
    const idx = startCol - scoreStdCtx.standardStartCol;
    const weight = _toScoreNumber(scoreStdCtx.standardTotalWeightValues[idx]);
    map.set(labelNorm, Number.isNaN(weight) ? 0 : weight);
  }
  return map;
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

function _appendHelperTotalWrites(batchCtx) {
  if (!batchCtx || !batchCtx.sharedCtx || !batchCtx.writeBatch) return;
  const resultLookup = batchCtx.sharedCtx.resultLookup;
  const helperTotalCol = resultLookup.helperTotalCol;
  const helperCharacterCols = resultLookup.helperCharacterCols || [];
  if (!helperTotalCol || !helperCharacterCols.length) return;

  const scoreStdCtx = batchCtx.sharedCtx.scoreStdCtx;
  const nicknameLookup = batchCtx.sharedCtx.nicknameLookup || { nicknameToOriginal: new Map() };
  const totalWeightByOriginal = _buildScoreTotalWeightMap(scoreStdCtx);
  const memberRows = Array.from(resultLookup.memberRowMap.values()).sort((a, b) => a - b);
  if (!memberRows.length) return;

  const weightedCols = [];
  for (let i = 0; i < helperCharacterCols.length; i += 1) {
    const item = helperCharacterCols[i];
    const originalNorm = nicknameLookup.nicknameToOriginal.has(item.charNorm)
      ? nicknameLookup.nicknameToOriginal.get(item.charNorm)
      : item.charNorm;
    const weight = totalWeightByOriginal.has(originalNorm) ? totalWeightByOriginal.get(originalNorm) : 0;
    if (weight <= 0) continue;
    weightedCols.push({ col: item.col, weight });
  }
  if (!weightedCols.length) return;

  const minRow = memberRows[0];
  const maxRow = memberRows[memberRows.length - 1];
  const minCol = Math.min.apply(null, weightedCols.map(x => x.col));
  const maxCol = Math.max.apply(null, weightedCols.map(x => x.col));
  const valueRange = batchCtx.sharedCtx.resultSheet.getRange(minRow, minCol, maxRow - minRow + 1, maxCol - minCol + 1);
  const values = valueRange.getValues();
  const pendingByRow = batchCtx.writeBatch && batchCtx.writeBatch.rowColMap
    ? batchCtx.writeBatch.rowColMap
    : new Map();

  const rawByRow = new Map();
  let maxRaw = 0;
  for (let i = 0; i < memberRows.length; i += 1) {
    const row = memberRows[i];
    const rIdx = row - minRow;
    let raw = 0;
    let hasAny = false;
    for (let j = 0; j < weightedCols.length; j += 1) {
      const wc = weightedCols[j];
      const pendingRow = pendingByRow.has(row) ? pendingByRow.get(row) : null;
      const cell = pendingRow && pendingRow.has(wc.col)
        ? pendingRow.get(wc.col)
        : values[rIdx][wc.col - minCol];
      const score = _toScoreNumber(cell);
      if (Number.isNaN(score) || score <= 0) continue;
      raw += score * wc.weight;
      hasAny = true;
    }
    if (!hasAny) continue;
    const rounded = Math.round(raw);
    rawByRow.set(row, rounded);
    if (rounded > 0 && rounded > maxRaw) maxRaw = rounded;
  }

  const writes = [];
  for (let i = 0; i < memberRows.length; i += 1) {
    const row = memberRows[i];
    const raw = rawByRow.has(row) ? rawByRow.get(row) : NaN;
    let score = 0;
    if (!Number.isNaN(raw) && raw > 0 && maxRaw > 0) {
      score = _truncatePercentInteger(Math.min(100, Math.max(0, (raw / maxRaw) * 100)));
    }
    writes.push({ row, col: helperTotalCol, value: score });
  }
  _appendScoreWritesToBatch(batchCtx.writeBatch, writes);
}

function _collectTotalRankWrites(sharedCtx) {
  if (!sharedCtx || !sharedCtx.resultLookup || !sharedCtx.resultSheet) return [];
  const resultLookup = sharedCtx.resultLookup;
  const memberRows = Array.from(resultLookup.memberRowMap.values()).sort((a, b) => a - b);
  if (!memberRows.length) return [];

  const sections = [];
  const outputRankColByElement = resultLookup.outputRankColByElement || new Map();
  resultLookup.outputTotalColByElement.forEach((totalCol, elementKey) => {
    const rankCol = outputRankColByElement.get(elementKey);
    if (totalCol && rankCol) sections.push({ totalCol, rankCol });
  });
  if (resultLookup.helperTotalCol && resultLookup.helperRankCol) {
    sections.push({ totalCol: resultLookup.helperTotalCol, rankCol: resultLookup.helperRankCol });
  }
  if (!sections.length) return [];

  const totalCols = sections.map(section => section.totalCol);
  const minRow = memberRows[0];
  const maxRow = memberRows[memberRows.length - 1];
  const minCol = Math.min.apply(null, totalCols);
  const maxCol = Math.max.apply(null, totalCols);
  const values = sharedCtx.resultSheet
    .getRange(minRow, minCol, maxRow - minRow + 1, maxCol - minCol + 1)
    .getValues();
  const writes = [];

  for (let i = 0; i < sections.length; i += 1) {
    const section = sections[i];
    const scoreByRow = new Map();
    for (let j = 0; j < memberRows.length; j += 1) {
      const row = memberRows[j];
      const cell = values[row - minRow][section.totalCol - minCol];
      const score = _toScoreNumber(cell);
      scoreByRow.set(row, Number.isNaN(score) ? 0 : score);
    }
    const rankMap = _buildCompetitionRankMap(memberRows, scoreByRow);
    for (let j = 0; j < memberRows.length; j += 1) {
      const row = memberRows[j];
      writes.push({ row, col: section.rankCol, value: rankMap.get(row) || 0 });
    }
  }

  return writes;
}

function normalizeLabel(label) {
  return String(label || "")
    .trim()
    .replace(/\s+/g, "")
    .replace(/：/g, ":");
}

function _displayNameFromSheet(sheetName) {
  return String(sheetName || "").replace("\u89d2\u8272\u7df4\u5ea6_", "");
}
