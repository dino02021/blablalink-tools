'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const sandbox = {input, RAID_PHASE_COUNT: 3, RAID_BOSS_COUNT: 5, RAID_YI_TO_TRUE: 100000000,
  RAID_MAX_KNIVES_PER_MEMBER: 3, RAID_TAIL_BOSS_INDEX: 4, RAID_TAIL_STAGE: 4};
const context = vm.createContext(sandbox, {codeGeneration: {strings: false, wasm: false}});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'fixtures/gs_raid_pool_reference.js'), 'utf8'), context, {timeout: 1000});
vm.runInContext(`
output = input.map(item => {
  const context = {...item, shortageLogs: [], members: item.members.map(raw => {
    const excludedElements = raw.reverseInputs.map(i => item.bossElements[i.bossIndex]);
    return {...raw, excludedElements, outputRanks: new Map(raw.ranks),
      rankedElements: _rankRaidElements(new Map(raw.ranks)).filter(e => !excludedElements.includes(e)),
      autoKnives: [], remainingKnives: 3 - raw.historyKnives.length - raw.fixedKnives.length};
  })};
  const pool = _buildRaidCandidatePool(context);
  const plan = _buildRaidMemberPlan(context, pool);
  return Array.from(plan.futureByMember.entries()).flatMap(([key, entry]) => entry.knives.map(k =>
    ({member:key, boss:k.bossIndex+1, damage:String(k.damageTrue), group:k.groupRank, top:k.topOrder || 0, kind:k.kind})));
});
`, context, {timeout: 1000});
process.stdout.write(JSON.stringify(sandbox.output));
