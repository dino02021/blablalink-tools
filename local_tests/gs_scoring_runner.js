"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const context = vm.createContext({input, Logger: {log() {}}}, {codeGeneration: {strings: false, wasm: false}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "fixtures/gs_scoring_reference.js"), "utf8"), context, {timeout:1000});
vm.runInContext(`
const SHEET_TRAINING_CHANGE = 'ignored_change_sheet';
let currentProfile, currentConfig;
function getMemberRowProfileByNameSheet() { return currentProfile; }
function _getScoreSourceStartRow() { return 3; }
function _getScoreSheetConfig() { return currentConfig; }
output = input.map(item => {
  const rules = item.rules.filter(r => r.is_score_tracked && r.tracked_types.length);
  const rows = item.members.map((_, i) => 3 + i * 2);
  const memberRowMap = new Map(item.members.map((m, i) => [m, 4 + i * 2]));
  currentProfile = {rows, names:item.members, count:rows.length, spanCount:rows[rows.length-1]-2};
  let termIndex = 0;
  const roleConfigs = rules.map((rule, i) => {
    const terms = rule.metrics.map(() => ({tIdx:termIndex, sIdx:termIndex++}));
    return {rule, terms, writeCols:[i+2], outputCols:rule.tracked_types.includes('output') ? [i+2] : [],
      labelNorm:rule.name_code, totalWeight:rule.total_weight};
  });
  const groups = ['電擊','水冷','燃燒','風壓','鐵甲'];
  let nextCol = rules.length + 2;
  const outputTotalColByElement = new Map(), outputRankColByElement = new Map();
  groups.forEach(group => {
    if (rules.some(r=>r.tracked_types.includes('output') && r.elements.includes(group) && r.total_weight>0)) {
      outputTotalColByElement.set(group,nextCol++); outputRankColByElement.set(group,nextCol++);
    }
  });
  const helperCharacterCols = roleConfigs.filter(c=>c.rule.tracked_types.includes('support')).map(c=>({col:c.writeCols[0],charNorm:c.labelNorm}));
  const hasHelper = rules.some(r=>r.tracked_types.includes('support') && r.total_weight>0);
  const helperTotalCol = hasHelper ? nextCol++ : 0, helperRankCol = hasHelper ? nextCol++ : 0;
  const grid = new Map();
  function apply(writes) { for (const w of writes) grid.set(w.row+':'+w.col,w.value); }
  const resultSheet = {getRange(row,col,height,width) { return {getValues() {
    return Array.from({length:height},(_,r)=>Array.from({length:width},(_,c)=>grid.get((row+r)+':'+(col+c)) ?? ''));
  }}; }};
  const standardGroups = roleConfigs.map(c=>({start:c.terms[0].sIdx+2}));
  const scoreStdCtx = {standardStartCol:2, standardGroups, standardLabels:rules.map(r=>r.name_code),
    standardWeightValues:rules.flatMap(r=>r.metrics.map(m=>m.coefficient)),
    standardTotalWeightValues:rules.flatMap(r=>r.metrics.map(()=>r.total_weight))};
  const sharedCtx = {resultSheet,scoreStdCtx,nicknameLookup:{nicknameToOriginal:new Map()},resultLookup:{
    memberRowMap, characterColMap:new Map(), outputTotalColByElement, outputRankColByElement,
    helperCharacterCols, helperTotalCol, helperRankCol}};
  const data = Array.from({length:currentProfile.spanCount},()=>Array(termIndex+1).fill(99999999));
  item.members.forEach((member,index)=>{data[index*2]=[member,...rules.flatMap(r=>r.metrics.map(m=>item.values[member][r.name_code]?.[m.metric_id] ?? ''))];});
  for (const element of groups) {
    const characterConfigs = roleConfigs.filter(c=>c.rule.elements.includes(element));
    if (!characterConfigs.length) continue;
    currentConfig = {characterConfigs,dataEndCol:termIndex+1};
    const sourceSheet = {getName(){return element;},getRange(){return {getValues(){return data;}};}};
    apply(_collectScoreWritesForSheetRows(sourceSheet,rows,sharedCtx));
  }
  const batch = {sharedCtx,writeBatch:{rowColMap:new Map()}};
  _appendHelperTotalWrites(batch);
  for (const [row,cols] of batch.writeBatch.rowColMap) for(const [col,value] of cols) grid.set(row+':'+col,value);
  apply(_collectTotalRankWrites(sharedCtx));
  return {isolated: typeof require==='undefined' && typeof fetch==='undefined' && typeof SpreadsheetApp==='undefined',
    roles:rules.flatMap((r,i)=>item.members.map(m=>[m,r.name_code,grid.get(memberRowMap.get(m)+':'+(i+2))])),
    groups:[...outputTotalColByElement.entries(),...(hasHelper ? [['輔助',helperTotalCol]] : [])].flatMap(([group,col])=>
      item.members.map(m=>[m,group,grid.get(memberRowMap.get(m)+':'+col),grid.get(memberRowMap.get(m)+':'+(group==='輔助'?helperRankCol:outputRankColByElement.get(group)))]))};
});
`,context,{timeout:5000});
process.stdout.write(JSON.stringify(context.output));
