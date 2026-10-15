'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const sandbox = {input, RAID_PHASE_COUNT: 3, RAID_BOSS_COUNT: 5, RAID_YI_TO_TRUE: 100000000,
  RAID_MAX_KNIVES_PER_MEMBER: 3, RAID_TAIL_BOSS_INDEX: 4, RAID_TAIL_STAGE: 4, Logger: {log() {}}};
const context = vm.createContext(sandbox, {codeGeneration: {strings: false, wasm: false}});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'fixtures/gs_raid_final_reference.js'), 'utf8'), context, {timeout: 1000});
vm.runInContext(`
output = input.map(item => {
  const context = {...item, shortageLogs: [], members: item.members.map(raw => {
    const excludedElements = raw.reverseInputs.map(i => item.bossElements[i.bossIndex]);
    return {...raw, excludedElements, outputRanks: new Map(raw.ranks),
      rankedElements: _rankRaidElements(new Map(raw.ranks)).filter(e => !excludedElements.includes(e)),
      autoKnives: [], remainingKnives: Math.max(0,3-raw.historyKnives.length-raw.fixedKnives.length)};
  }), bossStates:item.hp.map((bosses,i) => ({phase:i+1,label:String(i+1),bosses:bosses.slice()}))};
  context.memberMap = new Map(context.members.map(m => [m.key,m]));
  context.fixedInfo = {knives:context.members.reduce((sum,m)=>sum+m.fixedKnives.length,0)};
  for (const member of context.members) for(const k of member.historyKnives)
    _applyRaidDamageToBossStates(context.bossStates,k.stage,k.bossIndex,k.damageTrue);
  const pool = _buildRaidCandidatePool(context);
  const result = _buildRaidRoundPlanArtifacts(context,pool);
  return {isolated: typeof SpreadsheetApp === 'undefined' && typeof require === 'undefined' && typeof fetch === 'undefined',
    legacyOverflow: _getRaidRoundPhase3OverflowTrue(context,result.finalPlan),
    assignments: Array.from(result.finalPlan.futureByMember.entries()).flatMap(([member,entry]) => entry.knives.map(k =>
    ({member,boss:k.bossIndex+1,stage:k.stage,kind:k.kind,damage:String(k.damageTrue)})))};
});
`, context, {timeout: 5000});
process.stdout.write(JSON.stringify(sandbox.output));
