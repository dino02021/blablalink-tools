// Frozen local GS header reference. Synthetic Sheet range operations only.

const HEALTHCHECK_SHEET_HEADERS = ["名稱", "屬性", "類型", "詞條1", "詞條2", "詞條3", "評分追蹤", "暱稱", "優先度"];
const HEALTHCHECK_LEGACY_SHEET_HEADERS = ["名稱", "屬性", "類型", "詞條1", "詞條2", "詞條3", "暱稱", "優先度"];
const HEALTHCHECK_COL_NAME = 0;
const HEALTHCHECK_COL_ELEMENT = 1;
const HEALTHCHECK_COL_TYPE = 2;
const HEALTHCHECK_COL_TRAIT1 = 3;
const HEALTHCHECK_COL_TRAIT2 = 4;
const HEALTHCHECK_COL_TRAIT3 = 5;
const HEALTHCHECK_COL_SCORE_TRACKING = 6;
const HEALTHCHECK_COL_NICKNAME = 7;
const HEALTHCHECK_COL_PRIORITY = 8;
const HEALTHCHECK_SCORE_TRACKING_ENABLED = "追蹤";
const HEALTHCHECK_ELEMENT_ORDER = ["電擊", "水冷", "燃燒", "風壓", "鐵甲"];
const HEALTHCHECK_TYPE_ORDER = ["輸出", "輔助"];
const HEALTHCHECK_DUAL_ELEMENT_EXCEPTIONS = {
  "拉毗：小紅帽": ["燃燒", "鐵甲"],
};
const HEALTHCHECK_TEMPLATE_BLOCK_WIDTH = 12;
const HEALTHCHECK_TEMPLATE_TRAIT_OFFSET = 9;
const HEALTHCHECK_TEMPLATE_COLUMN_WIDTHS = [35, 30, 30, 30, 30, 30, 30, 30, 47, 50, 50, 50];
const HEALTHCHECK_TEMPLATE_GROUPS = [
  { offset: 0, width: 1, label: "突破" },
  { offset: 1, width: 3, label: "技能" },
  { offset: 4, width: 4, label: "裝備 (頭/手/身/腳)" },
  { offset: 8, width: 1, label: "收藏品" },
  { offset: 9, width: 3, label: "詞條 (優/攻/彈)" },
];
const HEALTHCHECK_TEMPLATE_HEADER_BG = "#f7cb4d";
const HEALTHCHECK_TEMPLATE_FONT_COLOR = "#000000";
const HEALTHCHECK_TEMPLATE_BORDER_COLOR = "#660000";
const HEALTHCHECK_TRAIT_LABEL_PREFIX = "詞條";
const HEALTHCHECK_TRAIT_ABBREVIATIONS = {
  "優越代碼傷害": "優",
  "攻擊力": "攻",
  "最大裝彈數": "彈",
  "蓄力速度": "速",
  "暴擊率": "暴率",
  "暴擊傷害": "暴傷",
  "蓄力傷害": "蓄傷",
  "命中率": "命",
  "防禦力": "防",
};

function _hcNormalizeText(value) {
  return String(value == null ? "" : value).trim();
}

function _hcCompareKey(value) {
  let text = _hcNormalizeText(value);
  try {
    text = text.normalize("NFKC");
  } catch (err) {
    // Apps Script supports normalize in V8; keep a fallback for older runtimes.
  }
  return text
    .replace(/[\s\u00a0\u200b-\u200d\ufeff]+/g, "")
    .replace(/：/g, ":");
}

function _hcTraitAbbreviation(value) {
  const text = _hcNormalizeText(value);
  if (!text) return "";
  const mapped = HEALTHCHECK_TRAIT_ABBREVIATIONS[_hcCompareKey(text)];
  return mapped || text;
}

function _hcBuildTraitHeaderLabelFromTraits(traits) {
  const values = Array.isArray(traits) ? traits : [];
  const parts = values.map(item => _hcTraitAbbreviation(item)).filter(Boolean);
  return parts.length
    ? `${HEALTHCHECK_TRAIT_LABEL_PREFIX} (${parts.join("/")})`
    : HEALTHCHECK_TRAIT_LABEL_PREFIX;
}

function _hcApplyTemplateColumnWidths(sheet, startCol) {
  for (let i = 0; i < HEALTHCHECK_TEMPLATE_COLUMN_WIDTHS.length; i += 1) {
    sheet.setColumnWidth(startCol + i, HEALTHCHECK_TEMPLATE_COLUMN_WIDTHS[i]);
  }
}

function _hcApplyTemplateGroupDividers(sheet, startRow, endRow, startCol) {
  if (endRow < startRow) return;
  const style = SpreadsheetApp.BorderStyle.SOLID;
  for (let i = 1; i < HEALTHCHECK_TEMPLATE_GROUPS.length; i += 1) {
    const col = startCol + HEALTHCHECK_TEMPLATE_GROUPS[i].offset;
    sheet.getRange(startRow, col, endRow - startRow + 1, 1)
      .setBorder(null, true, null, null, null, null, HEALTHCHECK_TEMPLATE_BORDER_COLOR, style);
  }
}

function _hcApplyTemplateOuterBorder(sheet, startRow, endRow, startCol, endCol, includeTopBorder) {
  if (endRow < startRow || endCol < startCol) return;
  sheet.getRange(startRow, startCol, endRow - startRow + 1, endCol - startCol + 1)
    .setBorder(
      includeTopBorder ? true : null,
      true,
      true,
      true,
      null,
      null,
      HEALTHCHECK_TEMPLATE_BORDER_COLOR,
      SpreadsheetApp.BorderStyle.SOLID_MEDIUM
    );
}

function _hcResetTemplateDataArea(sheet, dataStartRow, endRow, startCol, endCol) {
  if (endRow < dataStartRow || endCol < startCol) return;
  sheet.getRange(dataStartRow, startCol, endRow - dataStartRow + 1, endCol - startCol + 1)
    .clearContent()
    .setBackground(null)
    .setFontColor(HEALTHCHECK_TEMPLATE_FONT_COLOR)
    .setFontWeight("normal");
}

function _hcApplyFixedWidthTemplate(sheet, startCol, name, options) {
  const opts = options || {};
  const titleRow = opts.titleRow || 1;
  const groupRow = opts.groupRow || 2;
  const dataStartRow = opts.dataStartRow || groupRow + 1;
  const endRow = Math.max(sheet.getLastRow(), dataStartRow);
  const footerMergeRow = opts.footerMergeRow || null;
  const includeTopBorder = opts.includeTopBorder !== false;
  const endCol = startCol + HEALTHCHECK_TEMPLATE_BLOCK_WIDTH - 1;

  _hcApplyTemplateColumnWidths(sheet, startCol);
  sheet.getRange(titleRow, startCol, groupRow - titleRow + 1, HEALTHCHECK_TEMPLATE_BLOCK_WIDTH)
    .setBackground(HEALTHCHECK_TEMPLATE_HEADER_BG);

  sheet.getRange(titleRow, startCol, 1, HEALTHCHECK_TEMPLATE_BLOCK_WIDTH).clearContent().merge();
  sheet.getRange(titleRow, startCol).setValue(name);

  sheet.getRange(groupRow, startCol, 1, HEALTHCHECK_TEMPLATE_BLOCK_WIDTH).clearContent();
  for (let i = 0; i < HEALTHCHECK_TEMPLATE_GROUPS.length; i += 1) {
    const group = HEALTHCHECK_TEMPLATE_GROUPS[i];
    const groupStartCol = startCol + group.offset;
    const label = group.offset === HEALTHCHECK_TEMPLATE_TRAIT_OFFSET && opts.traitLabel ? opts.traitLabel : group.label;
    const range = sheet.getRange(groupRow, groupStartCol, 1, group.width);
    if (group.width > 1) range.merge();
    sheet.getRange(groupRow, groupStartCol).setValue(label);
  }

  const dividerEndRow = footerMergeRow ? footerMergeRow - 1 : endRow;
  _hcApplyTemplateGroupDividers(sheet, titleRow, dividerEndRow, startCol);

  if (footerMergeRow) {
    sheet.getRange(footerMergeRow, startCol, 1, HEALTHCHECK_TEMPLATE_BLOCK_WIDTH).clearContent().merge();
  }

  _hcResetTemplateDataArea(sheet, dataStartRow, endRow, startCol, endCol);
  _hcApplyTemplateOuterBorder(sheet, titleRow, endRow, startCol, endCol, includeTopBorder);
}

function _hcSetStandardTemplate(sheet, startCol, name, traitLabel) {
  _hcApplyFixedWidthTemplate(sheet, startCol, name, {
    titleRow: 2,
    groupRow: 3,
    dataStartRow: 4,
    footerMergeRow: 6,
    includeTopBorder: false,
    traitLabel,
  });
}
