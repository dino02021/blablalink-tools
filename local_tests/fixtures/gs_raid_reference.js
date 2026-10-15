function _estimateRaidKnifeDamageYi(sync, baseSync, baseDamageYi) {
  if (!Number.isFinite(sync) || sync <= 0) return 0;
  if (!Number.isFinite(baseSync) || baseSync <= 0) return 0;
  if (!Number.isFinite(baseDamageYi) || baseDamageYi <= 0) return 0;
  const ratio = Math.pow(sync, 3) / Math.pow(baseSync, 3);
  return Math.round(ratio * baseDamageYi);
}

function _createRaidFinitePhaseRemaining(context) {
  const remaining = [];
  for (let phase = 1; phase <= RAID_PHASE_COUNT; phase += 1) {
    const state = context.bossStates[phase - 1];
    remaining[phase] = state ? state.bosses.slice() : Array(RAID_BOSS_COUNT).fill(0);
  }
  return remaining;
}

function _getRaidCurrentFinitePhaseFromRemaining(remaining) {
  for (let phase = 1; phase <= RAID_PHASE_COUNT; phase += 1) {
    const bosses = remaining[phase] || [];
    for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
      if ((bosses[bossIndex] || 0) > 0) return phase;
    }
  }
  return 0;
}

function _isRaidFiniteFrontClearedFromRemaining(remaining) {
  return _getRaidCurrentFinitePhaseFromRemaining(remaining) === 0;
}

function _collectRaidPlanStageItemsForBoss(phasePlan, bossIndex) {
  const items = [];
  phasePlan.futureByMember.forEach((entry, memberKey) => {
    for (let i = 0; i < entry.knives.length; i += 1) {
      const knife = entry.knives[i];
      if (knife.bossIndex !== bossIndex) continue;
      items.push({
        memberKey,
        memberName: entry.name || memberKey,
        memberSync: entry.sync || 0,
        knife,
      });
    }
  });
  return items;
}

function _assignRaidKnifeFiniteStageForBoss(knife, remaining, tailKnives) {
  if (!knife) return;
  const bossIndex = knife.bossIndex;
  const damageTrue = knife.damageTrue || 0;
  if (!Number.isFinite(bossIndex) || bossIndex < 0 || bossIndex >= RAID_BOSS_COUNT || damageTrue <= 0) {
    knife.stage = 0;
    knife.effectiveTrue = 0;
    return;
  }

  if (knife.lockedStage) {
    const lockedStage = knife.stage || 0;
    if (lockedStage >= 1 && lockedStage <= RAID_PHASE_COUNT) {
      const bosses = remaining[lockedStage] || [];
      const remainingTrue = bosses[bossIndex] || 0;
      knife.effectiveTrue = Math.min(damageTrue, remainingTrue);
      bosses[bossIndex] = Math.max(0, remainingTrue - damageTrue);
      return;
    }
    if (lockedStage === RAID_TAIL_STAGE && bossIndex === RAID_TAIL_BOSS_INDEX) {
      tailKnives.push(knife);
      return;
    }
    knife.stage = 0;
    knife.effectiveTrue = 0;
    return;
  }

  for (let phase = 1; phase <= RAID_PHASE_COUNT; phase += 1) {
    const bosses = remaining[phase] || [];
    const remainingTrue = bosses[bossIndex] || 0;
    if (remainingTrue <= 0) continue;
    knife.stage = phase;
    knife.effectiveTrue = Math.min(damageTrue, remainingTrue);
    bosses[bossIndex] = Math.max(0, remainingTrue - damageTrue);
    return;
  }

  if (bossIndex === RAID_TAIL_BOSS_INDEX) {
    tailKnives.push(knife);
    return;
  }

  knife.stage = 0;
  knife.effectiveTrue = 0;
}

function _assignRaidTailStageKnives(tailKnives, remaining) {
  const finiteCleared = _isRaidFiniteFrontClearedFromRemaining(remaining);
  for (let i = 0; i < (tailKnives || []).length; i += 1) {
    const knife = tailKnives[i];
    if (!finiteCleared) {
      knife.stage = 0;
      knife.effectiveTrue = 0;
      continue;
    }
    knife.stage = RAID_TAIL_STAGE;
    knife.effectiveTrue = knife.damageTrue || 0;
  }
}

function _normalizeRaidRoundPlanStagesByBoss(context, phasePlan) {
  if (!phasePlan || !phasePlan.futureByMember) return;
  const remaining = _createRaidFinitePhaseRemaining(context);
  const tailKnives = [];
  for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
    const items = _collectRaidPlanStageItemsForBoss(phasePlan, bossIndex);
    items.sort((a, b) => {
      if (!!a.knife.lockedStage !== !!b.knife.lockedStage) return a.knife.lockedStage ? -1 : 1;
      if ((a.knife.stage || 0) !== (b.knife.stage || 0)) return (a.knife.stage || 0) - (b.knife.stage || 0);
      if (a.memberSync !== b.memberSync) return a.memberSync - b.memberSync;
      const nameCmp = a.memberName.localeCompare(b.memberName);
      if (nameCmp !== 0) return nameCmp;
      if ((a.knife.round || 0) !== (b.knife.round || 0)) return (a.knife.round || 0) - (b.knife.round || 0);
      if ((a.knife.sequence || 0) !== (b.knife.sequence || 0)) return (a.knife.sequence || 0) - (b.knife.sequence || 0);
      return (a.knife.groupRank || 0) - (b.knife.groupRank || 0);
    });
    for (let i = 0; i < items.length; i += 1) {
      _assignRaidKnifeFiniteStageForBoss(items[i].knife, remaining, tailKnives);
    }
  }
  _assignRaidTailStageKnives(tailKnives, remaining);
}
