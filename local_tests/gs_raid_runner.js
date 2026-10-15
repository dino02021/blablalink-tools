'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const sandbox = {input: payload, RAID_PHASE_COUNT: 3, RAID_BOSS_COUNT: 5, RAID_TAIL_STAGE: 4, RAID_TAIL_BOSS_INDEX: 4};
const context = vm.createContext(sandbox, {codeGeneration: {strings: false, wasm: false}});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'fixtures/gs_raid_reference.js'), 'utf8'), context, {timeout: 1000});
vm.runInContext(`
const estimates = (input.estimates || []).map(args => _estimateRaidKnifeDamageYi(...args));
const simulations = (input.simulations || []).map(item => {
  const context = {bossStates: item.hp.map((bosses,i) => ({phase: i+1, bosses: bosses.slice()}))};
  const phasePlan = {futureByMember: new Map(item.members.map(member => [member.key,
    {sync: member.sync, name: member.name, knives: item.knives.filter(k => k.member === member.key).map(k => ({...k}))}]))};
  _normalizeRaidRoundPlanStagesByBoss(context, phasePlan);
  return Array.from(phasePlan.futureByMember.values()).flatMap(entry => entry.knives.map(k => ({id:k.id, stage:k.stage, effective:k.effectiveTrue})));
});
output = {estimates, simulations, isolated: typeof require === 'undefined' && typeof fetch === 'undefined'};
`, context, {timeout: 1000});
process.stdout.write(JSON.stringify(sandbox.output));
