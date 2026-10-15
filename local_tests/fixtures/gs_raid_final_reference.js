// Raid plan assignment, candidate selection, and core planners.

function _assignRaidPlannedSequence(context, phasePlan) {
  const planned = [];
  phasePlan.futureByMember.forEach((entry, memberKey) => {
    entry.knives.forEach(knife => {
      planned.push({
        memberKey,
        memberSync: entry.sync || 0,
        memberName: entry.name || memberKey,
        knife,
      });
    });
  });
  planned.sort((a, b) => {
    if ((a.knife.stage || 0) !== (b.knife.stage || 0)) {
      return (a.knife.stage || 0) - (b.knife.stage || 0);
    }
    if ((a.memberSync || 0) !== (b.memberSync || 0)) {
      return (a.memberSync || 0) - (b.memberSync || 0);
    }
    const nameCmp = String(a.memberName || a.memberKey || "").localeCompare(String(b.memberName || b.memberKey || ""));
    if (nameCmp !== 0) return nameCmp;
    if ((a.knife.round || 0) !== (b.knife.round || 0)) {
      return (a.knife.round || 0) - (b.knife.round || 0);
    }
    if ((a.knife.sequence || 0) !== (b.knife.sequence || 0)) {
      return (a.knife.sequence || 0) - (b.knife.sequence || 0);
    }
    return (a.knife.bossIndex || 0) - (b.knife.bossIndex || 0);
  });

  for (let i = 0; i < planned.length; i += 1) {
    const item = planned[i];
    const member = context.memberMap.get(item.memberKey);
    const knife = item.knife;
    if (!member || !knife || !knife.stage) continue;
    _applyRaidDamageToBossStates(context.bossStates, knife.stage, knife.bossIndex, knife.damageTrue);
    if (knife.kind === "fixed") {
      const fixedRef = _findRaidPendingFixedKnife(member, knife.bossIndex);
      if (!fixedRef) continue;
      fixedRef.stage = knife.stage;
      fixedRef.sequence = knife.sequence || 0;
      fixedRef.assigned = true;
      continue;
    }
    member.autoKnives.push({
      bossIndex: knife.bossIndex,
      stage: knife.stage,
      damageYi: knife.damageYi,
      damageTrue: knife.damageTrue,
      sequence: knife.sequence || 0,
      elementKey: knife.elementKey,
      kind: knife.kind || "auto",
    });
    member.remainingKnives = Math.max(0, member.remainingKnives - 1);
  }
}

function _cloneRaidBossStates(bossStates) {
  return bossStates.map(state => ({
    phase: state.phase,
    label: state.label,
    bosses: state.bosses.slice(),
  }));
}

function _getRaidMembersBySyncDesc(members) {
  return members.slice().sort((a, b) => {
    if (b.sync !== a.sync) return b.sync - a.sync;
    return a.name.localeCompare(b.name);
  });
}

function _getRaidMembersBySyncAsc(members) {
  return members.slice().sort((a, b) => {
    if (a.sync !== b.sync) return a.sync - b.sync;
    return a.name.localeCompare(b.name);
  });
}

function _createRaidPhasePlanEntries(membersBySyncDesc) {
  const plan = {
    futureByMember: new Map(),
  };
  membersBySyncDesc.forEach(member => {
    plan.futureByMember.set(member.key, {
      sync: member.sync,
      name: member.name,
      sourceMember: member,
      knives: [],
    });
  });
  return plan;
}

function _cloneRaidPlanEntries(plan) {
  const clone = {
    futureByMember: new Map(),
  };
  plan.futureByMember.forEach((entry, memberKey) => {
    clone.futureByMember.set(memberKey, {
      sync: entry.sync,
      name: entry.name,
      sourceMember: entry.sourceMember,
      knives: entry.knives.map(knife => Object.assign({}, knife)),
    });
  });
  return clone;
}

function _stripRaidUnlockedStages(plan) {
  const stripped = _cloneRaidPlanEntries(plan);
  stripped.futureByMember.forEach(entry => {
    entry.knives = entry.knives.map(knife => {
      const nextKnife = Object.assign({}, knife);
      if (nextKnife.lockedStage) {
        const sourceLockedStage = nextKnife.sourceLockedStage
          ? (nextKnife.sourceStage || 0)
          : 0;
        if (!(nextKnife.stage > 0) && sourceLockedStage > 0) {
          nextKnife.stage = sourceLockedStage;
        } else if (!(nextKnife.stage > 0)
          && nextKnife.bossIndex === RAID_TAIL_BOSS_INDEX) {
          nextKnife.stage = RAID_TAIL_STAGE;
        }
        nextKnife.effectiveTrue = nextKnife.damageTrue || 0;
      } else {
        nextKnife.stage = 0;
        nextKnife.effectiveTrue = nextKnife.damageTrue || 0;
      }
      return nextKnife;
    });
  });
  return stripped;
}

function _normalizeRaidPlanStagesByBoss(context, phasePlan) {
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
      return (a.knife.groupRank || 0) - (b.knife.groupRank || 0);
    });
    for (let i = 0; i < items.length; i += 1) {
      _assignRaidKnifeFiniteStageForBoss(items[i].knife, remaining, tailKnives);
    }
  }
  _assignRaidTailStageKnives(tailKnives, remaining);
}

function _appendRaidPlannedKnife(plan, memberKey, knife) {
  const entry = plan.futureByMember.get(memberKey);
  if (!entry) return;
  const sourceKnife = knife.sourceKnife || knife;
  entry.knives.push({
    bossIndex: knife.bossIndex,
    stage: knife.stage,
    lockedStage: !!knife.lockedStage,
    elementKey: knife.elementKey,
    groupRank: knife.groupRank || 0,
    damageYi: knife.damageYi,
    damageTrue: knife.damageTrue,
    effectiveTrue: knife.effectiveTrue || knife.damageTrue,
    kind: knife.kind || "auto",
    round: knife.round || 0,
    sequence: knife.sequence || 0,
    sourceBossIndex: sourceKnife.bossIndex,
    sourceStage: sourceKnife.stage || 0,
    sourceLockedStage: !!sourceKnife.lockedStage,
    sourceElementKey: sourceKnife.elementKey || "",
    sourceGroupRank: sourceKnife.groupRank || 0,
    sourceKind: sourceKnife.kind || knife.kind || "auto",
  });
}

function _getRaidPlannedKnivesForMember(plan, memberKey) {
  const entry = plan.futureByMember.get(memberKey);
  return entry ? entry.knives.slice() : [];
}

function _getRaidFrontDeficitBosses(bossStates) {
  const deficitsByBoss = new Map();
  for (let phase = 1; phase <= Math.min(RAID_PHASE_COUNT, bossStates.length); phase += 1) {
    const state = bossStates[phase - 1];
    if (!state) continue;
    for (let bossIndex = 0; bossIndex < state.bosses.length; bossIndex += 1) {
      const remainingTrue = state.bosses[bossIndex] || 0;
      if (remainingTrue > 0) {
        const current = deficitsByBoss.get(bossIndex);
        if (current) {
          current.remainingTrue += remainingTrue;
          current.phase = Math.min(current.phase, phase);
        } else {
          deficitsByBoss.set(bossIndex, {
            phase,
            bossIndex,
            remainingTrue,
          });
        }
      }
    }
  }
  return Array.from(deficitsByBoss.values()).sort((a, b) => {
    if (a.phase !== b.phase) return a.phase - b.phase;
    if (a.remainingTrue !== b.remainingTrue) return b.remainingTrue - a.remainingTrue;
    return a.bossIndex - b.bossIndex;
  });
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

function _isRaidFiniteFrontClearedFromBossStates(bossStates) {
  return _getRaidCurrentFinitePhaseFromBossStates(bossStates) === 0;
}

function _getRaidCurrentFinitePhaseFromBossStates(bossStates) {
  for (let phase = 0; phase < Math.min(RAID_PHASE_COUNT, (bossStates || []).length); phase += 1) {
    const state = bossStates[phase];
    if (!state) continue;
    for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
      if ((state.bosses[bossIndex] || 0) > 0) return phase + 1;
    }
  }
  return 0;
}

function _raidBossHasFiniteRemaining(bossStates, bossIndex) {
  for (let phase = 0; phase < Math.min(RAID_PHASE_COUNT, (bossStates || []).length); phase += 1) {
    const state = bossStates[phase];
    if (!state) continue;
    if ((state.bosses[bossIndex] || 0) > 0) return true;
  }
  return false;
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

function _collectRaidKnifeCandidates(member, phase, context, bossIndexes, options) {
  const opts = options || {};
  const allowedElements = opts.allowedElements || null;
  const bossHp = opts.bossHp || context.bossStates[phase - 1].bosses;
  const plannedKnives = opts.plannedKnives || [];
  const ignoreBossCheckFor = opts.ignoreBossCheckFor || new Set();
  const allowElementConflict = !!opts.allowElementConflict;
  const candidates = [];
  for (let i = 0; i < bossIndexes.length; i += 1) {
    const bossIndex = bossIndexes[i];
    if (_memberBlocksRaidBoss(member, bossIndex)) continue;
    if (!ignoreBossCheckFor.has(bossIndex) && _memberHasRaidBoss(member, bossIndex, plannedKnives)) continue;
    const elementKey = context.bossElements[bossIndex];
    if (allowedElements && allowedElements.indexOf(elementKey) === -1) continue;
    if (!allowElementConflict && !_canUseRaidElement(member, elementKey, plannedKnives)) continue;

    const damageYi = _estimateRaidKnifeDamageYi(
      member.sync,
      context.baseSync,
      context.baseDamageYi[bossIndex]
    );
    if (!Number.isFinite(damageYi) || damageYi <= 0) continue;

    const damageTrue = Math.round(damageYi * RAID_YI_TO_TRUE);
    const remainingTrue = bossHp[bossIndex];
    if (!Number.isFinite(remainingTrue) || remainingTrue <= 0) continue;
    const effectiveTrue = Math.min(damageTrue, remainingTrue);
    const candidate = {
      bossIndex,
      elementKey,
      damageYi,
      damageTrue,
      effectiveTrue,
      remainingTrue,
      rawDamageTrue: damageTrue,
    };
    candidates.push(candidate);
  }
  candidates.sort((a, b) => {
    if (_isBetterRaidKnife(a, b)) return -1;
    if (_isBetterRaidKnife(b, a)) return 1;
    return 0;
  });
  return candidates;
}

function _isBetterRaidKnife(candidate, current) {
  if (!current) return true;
  if (candidate.effectiveTrue !== current.effectiveTrue) {
    return candidate.effectiveTrue > current.effectiveTrue;
  }
  if (candidate.remainingTrue !== current.remainingTrue) {
    return candidate.remainingTrue > current.remainingTrue;
  }
  if (candidate.rawDamageTrue !== current.rawDamageTrue) {
    return candidate.rawDamageTrue > current.rawDamageTrue;
  }
  return candidate.bossIndex < current.bossIndex;
}



﻿// Raid plan member-level plan construction.

function _buildRaidCandidatePool(context) {
  const byMember = new Map();
  context.members.forEach(member => {
    const groups = _computeRaidElementGroups(member);
    const staticCandidates = [];
    for (let phase = 1; phase <= RAID_PHASE_COUNT; phase += 1) {
      for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
        const damageYi = _estimateRaidKnifeDamageYi(
          member.sync,
          context.baseSync,
          context.baseDamageYi[bossIndex]
        );
        staticCandidates.push({
          phase,
          bossIndex,
          elementKey: context.bossElements[bossIndex],
          damageYi,
          damageTrue: Math.round(damageYi * RAID_YI_TO_TRUE),
        });
      }
    }
    byMember.set(member.key, {
      elementGroups: groups.map(group => group.slice()),
      rankedElements: (member.rankedElements || []).slice(),
      staticCandidates,
    });
  });
  return { byMember };
}

function _buildRaidMemberPlan(context, candidatePool) {
  const membersBySyncDesc = _getRaidMembersBySyncDesc(context.members);
  const memberPlan = _createRaidPhasePlanEntries(membersBySyncDesc);
  membersBySyncDesc.forEach(member => {
    const entry = memberPlan.futureByMember.get(member.key);
    if (!entry) return;
    const targetAutoSlots = member.remainingKnives;

    _getRaidPendingFixedKnives(member).forEach(fixedKnife => {
      entry.knives.push({
        bossIndex: fixedKnife.bossIndex,
        stage: fixedKnife.stage || 0,
        lockedStage: !!fixedKnife.stage,
        elementKey: fixedKnife.elementKey,
        damageYi: fixedKnife.damageYi,
        damageTrue: fixedKnife.damageTrue,
        effectiveTrue: fixedKnife.damageTrue,
        groupRank: _getRaidElementRank(member, fixedKnife.elementKey),
        slotIndex: fixedKnife.slotIndex,
        kind: "fixed",
      });
    });

    for (let slot = 0; slot < targetAutoSlots; slot += 1) {
      const candidate = _chooseRaidMemberPlanAutoKnife(member, context, entry.knives, candidatePool);
      if (!candidate) {
        _recordRaidShortageLog(context, {
          type: "memberPlan",
          memberKey: member.key,
          memberName: member.name,
          missingKnives: targetAutoSlots - slot,
          reason: _detectRaidMemberPlanShortageReason(member, context, entry.knives, candidatePool),
        });
        break;
      }
      entry.knives.push(candidate);
    }
  });
  return memberPlan;
}

function _chooseRaidMemberPlanAutoKnife(member, context, plannedKnives, candidatePool) {
  const strictCandidate = _findRaidMemberPlanAutoCandidate(member, context, plannedKnives, candidatePool, false);
  if (strictCandidate) return strictCandidate;
  return _findRaidMemberPlanAutoCandidate(member, context, plannedKnives, candidatePool, true);
}

function _findRaidMemberPlanAutoCandidate(member, context, plannedKnives, candidatePool, allowElementConflict) {
  const groups = _getRaidMemberPlanElementGroups(member, candidatePool, context.bossElements);
  for (let groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
    const candidates = _collectRaidMemberPlanBossCandidates(member, context, plannedKnives, groups[groupIndex], {
      allowElementConflict: !!allowElementConflict,
      allowTailBossRepeat: true,
    });
    if (!candidates.length) continue;
    const candidate = candidates[0];
    return {
      bossIndex: candidate.bossIndex,
      stage: 0,
      lockedStage: false,
      elementKey: candidate.elementKey,
      damageYi: candidate.damageYi,
      damageTrue: candidate.damageTrue,
      effectiveTrue: candidate.damageTrue,
      groupRank: groupIndex,
      topOrder: candidate.topOrder || 0,
      kind: "auto",
    };
  }
  return null;
}

function _getRaidMemberPlanElementGroups(member, candidatePool, bossElements) {
  const groups = _getRaidElementGroups(member, candidatePool).map(group => group.slice());
  const used = new Set();
  groups.forEach(group => group.forEach(elementKey => used.add(elementKey)));
  const nonPreferred = [];
  (bossElements || []).forEach(elementKey => {
    if (!used.has(elementKey) && nonPreferred.indexOf(elementKey) === -1) {
      nonPreferred.push(elementKey);
    }
  });
  if (nonPreferred.length) groups.push(nonPreferred);
  return groups;
}

function _collectRaidMemberPlanBossCandidates(member, context, plannedKnives, allowedElements, options) {
  const opts = options || {};
  const allowElementConflict = !!opts.allowElementConflict;
  const allowTailBossRepeat = !!opts.allowTailBossRepeat;
  const candidates = [];
  for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
    if (_memberBlocksRaidBoss(member, bossIndex)) continue;
    const repeatsBoss = _memberHasRaidBoss(member, bossIndex, plannedKnives);
    if (repeatsBoss && !(allowTailBossRepeat && bossIndex === RAID_TAIL_BOSS_INDEX)) continue;
    const elementKey = context.bossElements[bossIndex];
    if (allowedElements && allowedElements.indexOf(elementKey) === -1) continue;
    if (!allowElementConflict && !_canUseRaidElement(member, elementKey, plannedKnives)) continue;
    const damageYi = _estimateRaidKnifeDamageYi(member.sync, context.baseSync, context.baseDamageYi[bossIndex]);
    if (!Number.isFinite(damageYi) || damageYi <= 0) continue;
    const damageTrue = Math.round(damageYi * RAID_YI_TO_TRUE);
    candidates.push({
      bossIndex,
      elementKey,
      topOrder: allowedElements ? Math.max(0, allowedElements.indexOf(elementKey)) : 0,
      damageYi,
      damageTrue,
      remainingTrue: damageTrue,
      effectiveTrue: damageTrue,
      rawDamageTrue: damageTrue,
    });
  }
  candidates.sort((a, b) => {
    if (_isBetterRaidKnife(a, b)) return -1;
    if (_isBetterRaidKnife(b, a)) return 1;
    return 0;
  });
  return candidates;
}

function _detectRaidMemberPlanShortageReason(member, context, plannedKnives, candidatePool) {
  const blockedReverse = (member.reverseInputs || []).length > 0;
  const allBossesBlocked = [];
  for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
    if (_memberBlocksRaidBoss(member, bossIndex) || _memberHasRaidBoss(member, bossIndex, plannedKnives)) {
      allBossesBlocked.push(bossIndex);
    }
  }
  if (allBossesBlocked.length >= RAID_BOSS_COUNT) {
    return blockedReverse ? "指定刀 / 反向指定限制" : "同人不可同王限制";
  }

  const groups = _getRaidMemberPlanElementGroups(member, candidatePool, context.bossElements);
  let hasElementAllowedBoss = false;
  let hasConflictBlockedBoss = false;
  for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
    if (_memberBlocksRaidBoss(member, bossIndex) || _memberHasRaidBoss(member, bossIndex, plannedKnives)) continue;
    const elementKey = context.bossElements[bossIndex];
    if (groups.some(group => group.indexOf(elementKey) !== -1)) {
      hasElementAllowedBoss = true;
    }
    if (!_canUseRaidElement(member, elementKey, plannedKnives)) {
      hasConflictBlockedBoss = true;
    }
  }

  if (!hasElementAllowedBoss && blockedReverse) return "指定刀 / 反向指定限制";
  if (hasConflictBlockedBoss) return "燃燒 / 鐵甲 衝突限制";
  return "無合法候選刀";
}




﻿// Raid plan output, logging, and shared helpers.

function _buildRaidPlanOutputRows(context) {
  const activeMembers = [];
  const idleMembers = [];
  context.members.forEach(member => {
    const newKnives = _getRaidRunKnives(member);
    const displayKnives = _getRaidDisplayKnives(member);
    const bossDamageLines = Array.from({ length: RAID_BOSS_COUNT }, () => []);
    const bossStageLines = Array.from({ length: RAID_BOSS_COUNT }, () => []);
    const bossDamageSources = Array.from({ length: RAID_BOSS_COUNT }, () => []);
    const damageFontColors = Array(RAID_PLAN_DAMAGE_DISPLAY_NUM_COLS).fill(null);
    const damageBackgrounds = Array(RAID_PLAN_DAMAGE_DISPLAY_NUM_COLS).fill(null);
    const damageRichTextSegments = Array(RAID_PLAN_DAMAGE_DISPLAY_NUM_COLS).fill(null);
    const plannedKnifeCount = newKnives.length;
    displayKnives.forEach(knife => {
      bossDamageLines[knife.bossIndex].push(knife.damageYi);
      bossStageLines[knife.bossIndex].push(_formatRaidStage(knife.stage));
      bossDamageSources[knife.bossIndex].push(knife.source);
    });
    const bossDamageYi = bossDamageLines.map(lines => {
      if (!lines.length) return "";
      if (lines.length === 1) return lines[0];
      return lines.join("\n");
    });
    for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
      const sources = bossDamageSources[bossIndex];
      const historyCount = sources.filter(source => source === "history").length;
      if (!historyCount) continue;
      const damageColOffset = bossIndex * 2;
      if (historyCount === sources.length) {
        damageFontColors[damageColOffset] = RAID_PLAN_HISTORY_FONT_COLOR;
        damageBackgrounds[damageColOffset] = RAID_PLAN_HISTORY_BG_COLOR;
        continue;
      }
      let offset = 0;
      const segments = [];
      for (let lineIndex = 0; lineIndex < bossDamageLines[bossIndex].length; lineIndex += 1) {
        const text = String(bossDamageLines[bossIndex][lineIndex]);
        if (sources[lineIndex] === "history") {
          segments.push({
            start: offset,
            end: offset + text.length,
            color: RAID_PLAN_HISTORY_FONT_COLOR,
          });
        }
        offset += text.length + 1;
      }
      damageRichTextSegments[damageColOffset] = segments;
    }
    const bossStages = bossStageLines.map(lines => {
      const unique = [];
      for (let i = 0; i < lines.length; i += 1) {
        if (lines[i] && unique.indexOf(lines[i]) === -1) unique.push(lines[i]);
      }
      if (!unique.length) return "";
      if (unique.length === 1) return unique[0];
      return lines.join("\n");
    });
    const row = [
      member.name,
      member.sync,
      plannedKnifeCount,
      bossDamageYi[0],
      bossStages[0],
      bossDamageYi[1],
      bossStages[1],
      bossDamageYi[2],
      bossStages[2],
      bossDamageYi[3],
      bossStages[3],
      bossDamageYi[4],
      bossStages[4],
    ];
    const entry = {
      leftRow: row,
      fixedRow: (member.designatedRow || Array(RAID_PLAN_FIXED_NUM_COLS).fill("")).slice(0, RAID_PLAN_FIXED_NUM_COLS),
      damageFontColors,
      damageBackgrounds,
      damageRichTextSegments,
      member,
    };
    if (plannedKnifeCount > 0) {
      activeMembers.push(entry);
    } else {
      idleMembers.push(entry);
    }
  });

  activeMembers.sort((a, b) => {
    if (a.member.sync !== b.member.sync) return a.member.sync - b.member.sync;
    return a.member.name.localeCompare(b.member.name);
  });
  idleMembers.sort((a, b) => {
    if (a.member.sync !== b.member.sync) return a.member.sync - b.member.sync;
    return a.member.name.localeCompare(b.member.name);
  });

  return activeMembers.concat(idleMembers);
}

function _writeRaidPlanSheet(planSheet, outputRows) {
  const leftRows = outputRows.map(entry => entry.leftRow);
  const fixedRows = outputRows.map(entry => entry.fixedRow);
  const lastRow = Math.max(
    planSheet.getLastRow(),
    RAID_PLAN_OUTPUT_START_ROW + Math.max(outputRows.length, 1) - 1
  );
  const clearRows = Math.max(0, lastRow - RAID_PLAN_OUTPUT_START_ROW + 1);
  if (clearRows > 0) {
    planSheet.getRange(
      RAID_PLAN_OUTPUT_START_ROW,
      RAID_PLAN_OUTPUT_START_COL,
      clearRows,
      RAID_PLAN_OUTPUT_NUM_COLS
    ).clearContent();
    planSheet.getRange(
      RAID_PLAN_FIXED_START_ROW,
      RAID_PLAN_FIXED_START_COL,
      clearRows,
      RAID_PLAN_FIXED_NUM_COLS
    ).clearContent();
    planSheet.getRange(
      RAID_PLAN_OUTPUT_START_ROW,
      RAID_PLAN_DAMAGE_DISPLAY_START_COL,
      clearRows,
      RAID_PLAN_DAMAGE_DISPLAY_NUM_COLS
    ).setFontColor(null).setBackground(null);
  }
  if (!outputRows.length) return;
  planSheet.getRange(
    RAID_PLAN_OUTPUT_START_ROW,
    RAID_PLAN_OUTPUT_START_COL,
    leftRows.length,
    RAID_PLAN_OUTPUT_NUM_COLS
  ).setValues(leftRows);
  planSheet.getRange(
    RAID_PLAN_OUTPUT_START_ROW,
    RAID_PLAN_DAMAGE_DISPLAY_START_COL,
    leftRows.length,
    RAID_PLAN_DAMAGE_DISPLAY_NUM_COLS
  ).setWrap(true);
  planSheet.autoResizeRows(RAID_PLAN_OUTPUT_START_ROW, leftRows.length);
  planSheet.getRange(
    RAID_PLAN_FIXED_START_ROW,
    RAID_PLAN_FIXED_START_COL,
    fixedRows.length,
    RAID_PLAN_FIXED_NUM_COLS
  ).setValues(fixedRows);
  _applyRaidPlanDisplayStyles(planSheet, outputRows);
}

function _getRaidCurrentPhase(bossStates) {
  for (let i = 0; i < bossStates.length; i += 1) {
    const bosses = bossStates[i].bosses;
    for (let bossIndex = 0; bossIndex < bosses.length; bossIndex += 1) {
      if (bosses[bossIndex] > 0) return i + 1;
    }
  }
  return 0;
}

function _applyRaidDamageToBossStates(bossStates, phase, bossIndex, damageTrue) {
  const phaseState = bossStates[phase - 1];
  if (!phaseState) return;
  if (!Number.isFinite(damageTrue) || damageTrue <= 0) return;
  phaseState.bosses[bossIndex] = Math.max(0, phaseState.bosses[bossIndex] - damageTrue);
}

function _isRaidPhaseCleared(bossStates, phase) {
  const phaseState = bossStates[phase - 1];
  if (!phaseState) return true;
  for (let i = 0; i < phaseState.bosses.length; i += 1) {
    if (phaseState.bosses[i] > 0) return false;
  }
  return true;
}

function _estimateRaidKnifeDamageYi(sync, baseSync, baseDamageYi) {
  if (!Number.isFinite(sync) || sync <= 0) return 0;
  if (!Number.isFinite(baseSync) || baseSync <= 0) return 0;
  if (!Number.isFinite(baseDamageYi) || baseDamageYi <= 0) return 0;
  const ratio = Math.pow(sync, 3) / Math.pow(baseSync, 3);
  return Math.round(ratio * baseDamageYi);
}

function _parseRaidStage(raw) {
  const v = normalizeLabel(raw);
  if (!v) return 0;
  if (v === "I") return 1;
  if (v === "II") return 2;
  if (v === "III") return 3;
  if (v === "IV") return RAID_TAIL_STAGE;
  const n = Number(v);
  if (Number.isFinite(n) && n >= 1 && n <= RAID_TAIL_STAGE) return Math.floor(n);
  return 0;
}

function _rankRaidElements(rankMap) {
  const items = Array.from(rankMap.entries()).sort((a, b) => {
    if (a[1] !== b[1]) return a[1] - b[1];
    return a[0].localeCompare(b[0]);
  });
  const ordered = items.map(item => item[0]);
  const burnKey = normalizeLabel("\u71c3\u71d2");
  const ironKey = normalizeLabel("\u9435\u7532");
  const topThree = ordered.slice(0, 3);
  const burnIndex = topThree.indexOf(burnKey);
  const ironIndex = topThree.indexOf(ironKey);
  if (burnIndex !== -1 && ironIndex !== -1) {
    const burnRank = rankMap.get(burnKey) || Number.POSITIVE_INFINITY;
    const ironRank = rankMap.get(ironKey) || Number.POSITIVE_INFINITY;
    let demoteKey = ironKey;
    if (burnRank > ironRank) {
      demoteKey = burnKey;
    } else if (burnRank === ironRank) {
      demoteKey = burnIndex > ironIndex ? burnKey : ironKey;
    }
    const demoteIndex = ordered.indexOf(demoteKey);
    const replacementIndex = 3;
    if (demoteIndex !== -1 && replacementIndex < ordered.length) {
      const reordered = ordered.slice();
      const removed = reordered.splice(demoteIndex, 1)[0];
      reordered.splice(replacementIndex, 0, removed);
      return reordered;
    }
  }
  return ordered;
}

function _getRaidScorePercent(member, elementKey) {
  if (!member || !member.outputRanks) return Number.POSITIVE_INFINITY;
  return member.outputRanks.get(elementKey) || Number.POSITIVE_INFINITY;
}

function _memberHasRaidBoss(member, bossIndex, extraKnives) {
  const knives = _getRaidAllKnives(member).concat(extraKnives || []);
  for (let i = 0; i < knives.length; i += 1) {
    if (knives[i].bossIndex === bossIndex) return true;
  }
  return false;
}

function _canUseRaidElement(member, elementKey, extraKnives) {
  const burnKey = normalizeLabel("\u71c3\u71d2");
  const ironKey = normalizeLabel("\u9435\u7532");
  if (elementKey !== burnKey && elementKey !== ironKey) return true;
  const knives = _getRaidAllKnives(member).concat(extraKnives || []);
  const hasBurn = knives.some(knife => knife.elementKey === burnKey);
  const hasIron = knives.some(knife => knife.elementKey === ironKey);
  if (elementKey === burnKey && hasIron) return false;
  if (elementKey === ironKey && hasBurn) return false;
  return true;
}

function _isRaidFireOrIronElement(elementKey) {
  const normalized = normalizeLabel(elementKey);
  return normalized === normalizeLabel("\u71c3\u71d2")
    || normalized === normalizeLabel("\u9435\u7532");
}

function _memberHasRaidFireOrIron(member, extraKnives) {
  const knives = _getRaidAllKnives(member).concat(extraKnives || []);
  return knives.some(knife => _isRaidFireOrIronElement(knife.elementKey));
}

function _getRaidElementGroups(member, candidatePool) {
  if (candidatePool && candidatePool.byMember) {
    const profile = candidatePool.byMember.get(member.key);
    if (profile && profile.elementGroups) {
      return profile.elementGroups.map(group => group.slice());
    }
  }
  return _computeRaidElementGroups(member);
}

function _computeRaidElementGroups(member) {
  const excluded = new Set(member.excludedElements || []);
  const ranked = (member.rankedElements || []).filter(elementKey => !excluded.has(elementKey));
  const groups = [];
  const topThree = ranked.slice(0, 3);
  if (topThree.length) groups.push(topThree);
  if (ranked.length >= 4) groups.push([ranked[3]]);
  if (ranked.length >= 5) groups.push([ranked[4]]);
  if (!groups.length && ranked.length) groups.push(ranked.slice());
  return groups;
}

function _logRaidMemberPlanSummary(context, phasePlan) {
  const membersBySyncDesc = _getRaidMembersBySyncDesc(context.members);
  membersBySyncDesc.forEach(member => {
    const entry = phasePlan.futureByMember.get(member.key);
    const knives = entry && entry.knives && entry.knives.length
      ? entry.knives.map(knife => `${knife.kind}:${knife.bossIndex + 1}-${knife.stage || 0}`).join("/")
      : "-";
    Logger.log(
      "raid-member-plan: member=%s sync=%s knives=%s",
      member.name,
      member.sync,
      knives
    );
  });
}

function _countRaidPlanKnives(phasePlan) {
  let count = 0;
  if (!phasePlan || !phasePlan.futureByMember) return count;
  phasePlan.futureByMember.forEach(entry => {
    count += (entry.knives || []).length;
  });
  return count;
}

function _getRaidElementRank(member, elementKey) {
  const ranked = member.rankedElements || [];
  const index = ranked.indexOf(elementKey);
  return index >= 0 ? index : ranked.length;
}

function _getRaidAliveBossIndexesFromArray(bosses) {
  const alive = [];
  for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
    if (bosses[bossIndex] > 0) alive.push(bossIndex);
  }
  return alive;
}

function _getRaidRunKnives(member) {
  return member.fixedKnives.filter(knife => knife.assigned).concat(member.autoKnives);
}

function _getRaidAllKnives(member) {
  return member.historyKnives.concat(member.fixedKnives, member.autoKnives);
}

function _getRaidDisplayKnives(member) {
  const historyKnives = (member.historyKnives || []).map(knife => ({
    bossIndex: knife.bossIndex,
    stage: knife.stage,
    damageYi: Number.isFinite(knife.damageYi)
      ? knife.damageYi
      : Math.round((knife.damageTrue || 0) / RAID_YI_TO_TRUE),
    damageTrue: knife.damageTrue || 0,
    sequence: knife.sequence || 0,
    source: "history",
  }));
  const runKnives = _getRaidRunKnives(member).map(knife => ({
    bossIndex: knife.bossIndex,
    stage: knife.stage,
    damageYi: knife.damageYi,
    damageTrue: knife.damageTrue || 0,
    sequence: knife.sequence || 0,
    source: "planned",
  }));
  return historyKnives.concat(runKnives);
}

function _applyRaidPlanDisplayStyles(planSheet, outputRows) {
  for (let rowIndex = 0; rowIndex < outputRows.length; rowIndex += 1) {
    const entry = outputRows[rowIndex];
    const sheetRow = RAID_PLAN_OUTPUT_START_ROW + rowIndex;
    const fontColors = entry.damageFontColors || [];
    const backgrounds = entry.damageBackgrounds || [];
    const richTextSegments = entry.damageRichTextSegments || [];
    for (let colOffset = 0; colOffset < RAID_PLAN_DAMAGE_DISPLAY_NUM_COLS; colOffset += 1) {
      const fontColor = fontColors[colOffset];
      const background = backgrounds[colOffset];
      const segments = richTextSegments[colOffset];
      if (!fontColor && !background && !(segments && segments.length)) continue;
      const cell = planSheet.getRange(sheetRow, RAID_PLAN_DAMAGE_DISPLAY_START_COL + colOffset);
      if (fontColor) cell.setFontColor(fontColor);
      if (background) cell.setBackground(background);
      if (segments && segments.length) {
        const text = String(entry.leftRow[RAID_PLAN_DAMAGE_DISPLAY_START_COL - 1 + colOffset] || "");
        const style = SpreadsheetApp.newTextStyle()
          .setForegroundColor(RAID_PLAN_HISTORY_FONT_COLOR)
          .build();
        const builder = SpreadsheetApp.newRichTextValue().setText(text);
        segments.forEach(segment => {
          if (segment.end > segment.start) {
            builder.setTextStyle(segment.start, segment.end, style);
          }
        });
        cell.setRichTextValue(builder.build());
      }
    }
  }
}

function _memberBlocksRaidBoss(member, bossIndex) {
  return !!(member && (member.reverseInputs || []).some(input => input.bossIndex === bossIndex));
}

function _getRaidPendingFixedKnives(member) {
  return member.fixedKnives.filter(knife => !knife.assigned);
}

function _findRaidPendingFixedKnife(member, bossIndex) {
  const pending = _getRaidPendingFixedKnives(member);
  for (let i = 0; i < pending.length; i += 1) {
    if (pending[i].bossIndex === bossIndex) return pending[i];
  }
  return null;
}

function _getRaidFirstKnife(knives) {
  if (!knives.length) return null;
  return knives.slice().sort((a, b) => {
    if (a.stage !== b.stage) return a.stage - b.stage;
    if (a.sequence !== b.sequence) return a.sequence - b.sequence;
    return a.bossIndex - b.bossIndex;
  })[0];
}

function _parseRaidPlanDamageCell(raw) {
  if (raw == null || raw === "") return null;
  const n = _toRaidNumber(raw);
  if (!Number.isFinite(n)) return null;
  return n;
}

function _recordRaidShortageLog(context, log) {
  if (!context || !context.shortageLogs || !log) return;
  const normalized = Object.assign({
    type: "other",
    reason: "其他",
  }, log);
  const signature = JSON.stringify(normalized);
  if (!context._shortageLogSignatures) context._shortageLogSignatures = new Set();
  if (context._shortageLogSignatures.has(signature)) return;
  context._shortageLogSignatures.add(signature);
  context.shortageLogs.push(normalized);
}

function _emitRaidShortageLogs(context) {
  const logs = context && context.shortageLogs ? context.shortageLogs : [];
  if (!logs.length) return;
  logs.forEach(log => {
    if (log.type === "memberPlan") {
      Logger.log(
        "raid-shortage memberPlan: member=%s missing=%s reason=%s",
        log.memberName || log.memberKey || "ALL",
        Number.isFinite(log.missingKnives) ? log.missingKnives : "-",
        log.reason || "其他"
      );
      return;
    }
    if (log.type === "landing") {
      Logger.log(
        "raid-shortage landing: phase=%s boss=%s phaseKnife=%s missing=%s reason=%s",
        log.phase || "-",
        Number.isFinite(log.bossIndex) && log.bossIndex >= 0 ? log.bossIndex + 1 : "-",
        Number.isFinite(log.phaseKnifeIndex) ? log.phaseKnifeIndex : "-",
        Number.isFinite(log.missingKnives) ? log.missingKnives : "-",
        log.reason || "其他"
      );
      return;
    }
    if (log.type === "roundSelection") {
      Logger.log(
        "raid-shortage roundSelection: missing=%s reason=%s",
        Number.isFinite(log.missingKnives) ? log.missingKnives : "-",
        log.reason || "其他"
      );
      return;
    }
    if (log.type === "roundSelectionMember") {
      Logger.log(
        "raid-shortage roundSelectionMember: member=%s round=%s source=%s candidates=%s coveredFiltered=%s rejectedDeficit=%s rejectedOverflow=%s reason=%s",
        log.memberName || "-",
        Number.isFinite(log.round) ? log.round : "-",
        Number.isFinite(log.sourceKnifeCount) ? log.sourceKnifeCount : "-",
        Number.isFinite(log.candidateCount) ? log.candidateCount : "-",
        Number.isFinite(log.coveredFiltered) ? log.coveredFiltered : "-",
        Number.isFinite(log.rejectedDeficit) ? log.rejectedDeficit : "-",
        Number.isFinite(log.rejectedOverflow) ? log.rejectedOverflow : "-",
        log.reason || "其他"
      );
      return;
    }
    if (log.type === "fixedInput") {
      Logger.log(
        "raid-fixed-input: member=%s slot=%s boss=%s reason=%s",
        log.memberName || log.memberKey || "-",
        Number.isFinite(log.slotIndex) ? log.slotIndex + 1 : "-",
        Number.isFinite(log.bossIndex) && log.bossIndex >= 0 ? log.bossIndex + 1 : "-",
        log.reason || "其他"
      );
      return;
    }
    Logger.log("raid-shortage: type=%s reason=%s", log.type || "-", log.reason || "其他");
  });
}

function _recordRaidMemberPlanShortageSummary(context, phasePlan) {
  const missingKnives = _getRaidPlanKnifeShortage(context, phasePlan);
  if (missingKnives <= 0) return;
  _recordRaidShortageLog(context, {
    type: "memberPlan",
    missingKnives,
    reason: "memberPlan 補滿後仍不足 96 刀",
  });
}

function _recordRaidLandingShortageSummary(context, phasePlan) {
  const expectedTotal = _getRaidExpectedKnifeTotal(context);
  const actualTotal = context.members.reduce((sum, member) => sum + _getRaidRunKnives(member).length, 0);
  if (actualTotal >= expectedTotal) return;
  _logRaidOutputMissingMembers(context);
  _logRaidRemainingPlannedKnives(context._raidLastRemainingPlan || phasePlan);
  const finalMissingKnives = expectedTotal - actualTotal;
  const existingLandingLog = (context.shortageLogs || []).find(log => log.type === "landing");
  if (existingLandingLog) {
    existingLandingLog.missingKnives = finalMissingKnives;
    if (_hasRaidMemberPlanShortageLog(context)) {
      existingLandingLog.reason = "memberPlan 補滿後仍不足 96 刀";
    }
    return;
  }

  const phase = _getRaidCurrentPhase(context.bossStates);
  let bossIndex = -1;
  let phaseKnifeIndex = 0;
  if (phase > 0) {
    const phaseState = context.bossStates[phase - 1];
    bossIndex = phaseState ? phaseState.bosses.findIndex(hp => hp > 0) : -1;
    phaseKnifeIndex = context.members.reduce((sum, member) => {
      return sum + _getRaidRunKnives(member).filter(knife => knife.stage === phase).length;
    }, 0) + 1;
  }
  _recordRaidShortageLog(context, {
    type: "landing",
    phase: phase || "-",
    bossIndex,
    phaseKnifeIndex,
    missingKnives: finalMissingKnives,
    reason: _hasRaidMemberPlanShortageLog(context)
      ? "memberPlan 補滿後仍不足 96 刀"
      : "補洞後仍無合法方案",
  });
}

function _formatRaidStage(stage) {
  if (stage === 1) return "I";
  if (stage === 2) return "II";
  if (stage === 3) return "III";
  if (stage === RAID_TAIL_STAGE) return "IV";
  return "";
}

function _getRaidExpectedKnifeTotal(context) {
  return context.members.reduce((sum, member) => {
    return sum + Math.max(0, RAID_MAX_KNIVES_PER_MEMBER - member.historyKnives.length);
  }, 0);
}

function _getRaidPlanKnifeShortage(context, phasePlan) {
  return Math.max(0, _getRaidExpectedKnifeTotal(context) - _countRaidPlanKnives(phasePlan));
}

function _hasRaidMemberPlanShortageLog(context) {
  return !!((context.shortageLogs || []).some(log => log.type === "memberPlan"));
}

function _logRaidRemainingPlannedKnives(phasePlan) {
  if (!phasePlan || !phasePlan.futureByMember) return;
  const rows = [];
  phasePlan.futureByMember.forEach(entry => {
    if (!entry || !entry.knives || !entry.knives.length) return;
    const knives = entry.knives
      .map(knife => `${knife.kind}:${knife.bossIndex + 1}-${knife.stage || 0}`)
      .join("/");
    rows.push(`${entry.name}:${entry.knives.length}[${knives}]`);
  });
  if (!rows.length) return;
  Logger.log("raid-shortage remaining-planned: %s", rows.join(" | "));
}

function _logRaidOutputMissingMembers(context) {
  if (!context || !context.members) return;
  const rows = [];
  context.members.forEach(member => {
    const expected = Math.max(0, RAID_MAX_KNIVES_PER_MEMBER - (member.historyKnives || []).length);
    const actual = _getRaidRunKnives(member).length;
    if (actual >= expected) return;
    rows.push(`${member.name}:${actual}/${expected}`);
  });
  if (!rows.length) return;
  Logger.log("raid-shortage output-missing-members: %s", rows.join(" | "));
}

function _toRaidNumber(raw) {
  if (raw == null || raw === "") return NaN;
  if (typeof raw === "number") return raw;
  const str = String(raw).replace(/,/g, "").trim();
  if (!str) return NaN;
  const n = Number(str);
  return Number.isFinite(n) ? n : NaN;
}


﻿// Raid plan phase planner helpers and round selection.

function _createRaidPlanningContext(context) {
  const members = context.members.map(member => ({
    key: member.key,
    name: member.name,
    sync: member.sync,
    historyKnives: member.historyKnives.map(knife => Object.assign({}, knife)),
    fixedKnives: member.fixedKnives.map(knife => Object.assign({}, knife)),
    reverseInputs: (member.reverseInputs || []).map(input => Object.assign({}, input)),
    autoKnives: member.autoKnives.map(knife => Object.assign({}, knife)),
    remainingKnives: member.remainingKnives,
    outputRanks: new Map(member.outputRanks || []),
    rankedElements: (member.rankedElements || []).slice(),
    preferredElements: (member.preferredElements || []).slice(),
    excludedElements: (member.excludedElements || []).slice(),
    designatedRow: (member.designatedRow || []).slice(),
  }));
  const memberMap = new Map();
  members.forEach(member => memberMap.set(member.key, member));
  return {
    members,
    memberMap,
    bossStates: _cloneRaidBossStates(context.bossStates),
    bossElements: context.bossElements.slice(),
    baseSync: context.baseSync,
    baseDamageYi: context.baseDamageYi.slice(),
  };
}




// Raid plan round-mode planner.

function _buildRaidRoundPlanArtifacts(context, candidatePool) {
  const memberPlan = _buildRaidMemberPlan(context, candidatePool);
  _logRaidMemberPlanSummary(context, memberPlan);
  context._raidRoundMemberPlanSource = _cloneRaidPlanEntries(memberPlan);
  const finalPlan = _buildRaidRoundFinalPlan(context, memberPlan, candidatePool);
  return {
    memberPlan,
    finalPlan,
  };
}

function _buildRaidRoundFinalPlan(context, memberPlan, candidatePool) {
  const selectedPlan = _createRaidPhasePlanEntries(_getRaidMembersBySyncDesc(context.members));
  const roundState = _createRaidRoundState(memberPlan);
  const targetKnifeCount = _countRaidPlanKnives(memberPlan);
  let sequence = context.fixedInfo.knives;
  const highToLowMembers = _getRaidMembersBySyncDesc(context.members);
  const lowToHighMembers = _getRaidMembersBySyncAsc(context.members);

  sequence = _runRaidRoundPass(context, selectedPlan, roundState, candidatePool, 1, highToLowMembers, sequence, "strictTopFill");
  sequence = _runRaidRoundPass(context, selectedPlan, roundState, candidatePool, 2, highToLowMembers, sequence, "strictTopFill");
  sequence = _runRaidRoundPass(context, selectedPlan, roundState, candidatePool, 3, highToLowMembers, sequence, "strictTopFill");
  sequence = _runRaidRoundFallbackPasses(context, selectedPlan, roundState, candidatePool, lowToHighMembers, sequence, targetKnifeCount);

  _recordRaidRoundSelectionCheck(context, targetKnifeCount, selectedPlan);
  let simulation = _stabilizeRaidRoundPlan(context, selectedPlan, candidatePool, _simulateRaidRoundSelectedPlan(context, selectedPlan));
  simulation = _preserveRaidRoundFixedKnifePriority(context, simulation);
  simulation = _balanceRaidRoundFireIronCoverage(context, simulation);
  simulation = _recoverRaidRoundUnlandedAutoKnives(context, simulation);
  simulation = _fillRaidRoundRemainingMemberKnives(context, simulation, memberPlan, candidatePool);
  simulation = _balanceRaidRoundFireIronCoverage(context, simulation);
  simulation = _recoverRaidRoundUnlandedAutoKnives(context, simulation);
  simulation = _preserveRaidRoundFixedKnifePriority(context, simulation);
  context._raidLastRemainingPlan = _buildRaidRoundRemainingPlan(memberPlan, simulation.plan);
  return simulation.plan;
}

function _fillRaidRoundRemainingMemberKnives(context, initialSimulation, memberPlan, candidatePool) {
  const remainingPlan = _buildRaidRoundRemainingPlan(memberPlan, initialSimulation.plan);
  if (_countRaidPlanKnives(remainingPlan) <= 0) return initialSimulation;

  const selectedPlan = _stripRaidUnlockedStages(initialSimulation.plan);
  const roundState = _createRaidRoundState(remainingPlan);
  const lowToHighMembers = _getRaidMembersBySyncAsc(context.members || []);
  let sequence = _getRaidRoundNextSequence(selectedPlan);
  let roundIndex = RAID_MAX_KNIVES_PER_MEMBER + 1;
  let changed = false;

  for (let pass = 0; pass < RAID_MAX_KNIVES_PER_MEMBER; pass += 1) {
    const beforeCount = _countRaidPlanKnives(selectedPlan);
    sequence = _runRaidRoundPass(
      context,
      selectedPlan,
      roundState,
      candidatePool,
      roundIndex,
      lowToHighMembers,
      sequence,
      "compromiseFill"
    );
    const afterCount = _countRaidPlanKnives(selectedPlan);
    if (afterCount <= beforeCount) break;
    changed = true;
    roundIndex += 1;
  }

  if (!changed) return initialSimulation;
  let simulation = _stabilizeRaidRoundPlan(
    context,
    selectedPlan,
    candidatePool,
    _simulateRaidRoundSelectedPlan(context, selectedPlan)
  );
  simulation = _balanceRaidRoundFireIronCoverage(context, simulation);
  return _recoverRaidRoundUnlandedAutoKnives(context, simulation);
}

function _recoverRaidRoundUnlandedAutoKnives(context, initialSimulation) {
  let simulation = initialSimulation;
  const maxSteps = Math.max(1, (context.members || []).length * RAID_MAX_KNIVES_PER_MEMBER);

  for (let step = 0; step < maxSteps; step += 1) {
    const members = _getRaidMembersBySyncAsc(context.members || []);
    let accepted = null;

    for (let memberIndex = 0; memberIndex < members.length && !accepted; memberIndex += 1) {
      const member = members[memberIndex];
      const entry = simulation.plan.futureByMember.get(member.key);
      if (!entry) continue;

      for (let knifeIndex = 0; knifeIndex < entry.knives.length && !accepted; knifeIndex += 1) {
        const knife = entry.knives[knifeIndex];
        if (knife.kind === "fixed" || knife.lockedStage || (knife.stage || 0) > 0) continue;
        accepted = _tryRecoverRaidRoundAutoKnife(
          context,
          simulation,
          member,
          knifeIndex
        );
      }
    }

    if (!accepted) return simulation;
    simulation = accepted;
  }
  return simulation;
}

function _tryRecoverRaidRoundAutoKnife(context, simulation, member, knifeIndex) {
  const entry = simulation.plan.futureByMember.get(member.key);
  if (!entry || !entry.knives[knifeIndex]) return null;
  const otherKnives = entry.knives.filter((knife, index) => index !== knifeIndex);
  const deficitBosses = new Set((simulation.deficitBosses || []).map(item => item.bossIndex));
  const finiteFrontCleared = deficitBosses.size === 0;
  const currentLandedCount = _countRaidRoundLandedKnives(simulation.plan);
  const currentOverflowTrue = _getRaidRoundPhase3OverflowTrue(context, simulation.plan);

  for (let conflictPass = 0; conflictPass < 2; conflictPass += 1) {
    const candidates = _collectRaidMemberPlanBossCandidates(
      member,
      context,
      otherKnives,
      null,
      {
        allowElementConflict: conflictPass === 1,
        allowTailBossRepeat: finiteFrontCleared,
      }
    ).filter(candidate => {
      if (finiteFrontCleared) return candidate.bossIndex === RAID_TAIL_BOSS_INDEX;
      return deficitBosses.has(candidate.bossIndex);
    });

    for (let i = 0; i < candidates.length; i += 1) {
      const candidate = candidates[i];
      const nextPlan = _cloneRaidPlanEntries(simulation.plan);
      const nextEntry = nextPlan.futureByMember.get(member.key);
      if (!nextEntry || !nextEntry.knives[knifeIndex]) continue;
      nextEntry.knives[knifeIndex] = _retargetRaidRoundKnife(
        context,
        member,
        nextEntry.knives[knifeIndex],
        candidate.bossIndex,
        otherKnives
      );

      const rerun = _simulateRaidRoundSelectedPlan(context, _stripRaidUnlockedStages(nextPlan));
      if (_countRaidRoundLandedKnives(rerun.plan) <= currentLandedCount) continue;
      if ((rerun.deficitBosses || []).length > (simulation.deficitBosses || []).length) continue;
      if ((rerun.deficitRemainingTrue || 0) > (simulation.deficitRemainingTrue || 0)) continue;
      if (_getRaidRoundPhase3OverflowTrue(context, rerun.plan) > currentOverflowTrue) continue;
      return rerun;
    }
  }
  return null;
}

function _countRaidRoundLandedKnives(phasePlan) {
  let count = 0;
  if (!phasePlan || !phasePlan.futureByMember) return count;
  phasePlan.futureByMember.forEach(entry => {
    count += (entry.knives || []).filter(knife => (knife.stage || 0) > 0).length;
  });
  return count;
}

function _preserveRaidRoundFixedKnifePriority(context, initialSimulation) {
  let simulation = initialSimulation;
  const maxSteps = Math.max(1, (context.members || []).length * RAID_MAX_KNIVES_PER_MEMBER);

  for (let step = 0; step < maxSteps; step += 1) {
    const unlandedFixed = _collectRaidRoundUnlandedFixedKnives(context, simulation.plan);
    let accepted = null;

    for (let i = 0; i < unlandedFixed.length && !accepted; i += 1) {
      accepted = _tryPreserveRaidRoundFixedKnife(context, simulation, unlandedFixed[i]);
    }

    if (!accepted) return simulation;
    simulation = accepted;
  }
  return simulation;
}

function _collectRaidRoundUnlandedFixedKnives(context, phasePlan) {
  const items = [];
  if (!phasePlan || !phasePlan.futureByMember) return items;
  phasePlan.futureByMember.forEach((entry, memberKey) => {
    (entry.knives || []).forEach((knife, knifeIndex) => {
      if (knife.kind !== "fixed" || knife.lockedStage || (knife.stage || 0) > 0) return;
      if (knife.bossIndex === RAID_TAIL_BOSS_INDEX) return;
      items.push({
        memberKey,
        memberName: entry.name || memberKey,
        memberSync: entry.sync || 0,
        knifeIndex,
        knife,
      });
    });
  });
  items.sort((a, b) => {
    if (a.memberSync !== b.memberSync) return b.memberSync - a.memberSync;
    if ((a.knife.slotIndex || 0) !== (b.knife.slotIndex || 0)) {
      return (a.knife.slotIndex || 0) - (b.knife.slotIndex || 0);
    }
    return a.memberName.localeCompare(b.memberName);
  });
  return items;
}

function _tryPreserveRaidRoundFixedKnife(context, simulation, fixedItem) {
  const currentLandedCount = _countRaidRoundLandedKnives(simulation.plan);
  const currentOverflowTrue = _getRaidRoundPhase3OverflowTrue(context, simulation.plan);
  const currentFixedKeys = _getRaidRoundLandedFixedKnifeKeys(simulation.plan);
  const donors = _collectRaidRoundFixedKnifeDonors(simulation.plan, fixedItem);
  const deficitBosses = (simulation.deficitBosses || []).map(item => item.bossIndex);
  const targetBosses = deficitBosses
    .filter(bossIndex => bossIndex !== fixedItem.knife.bossIndex);
  if (targetBosses.indexOf(RAID_TAIL_BOSS_INDEX) === -1) {
    targetBosses.push(RAID_TAIL_BOSS_INDEX);
  }

  for (let donorIndex = 0; donorIndex < donors.length; donorIndex += 1) {
    const donor = donors[donorIndex];
    const donorMember = context.memberMap.get(donor.memberKey);
    if (!donorMember) continue;

    for (let conflictPass = 0; conflictPass < 2; conflictPass += 1) {
      for (let targetIndex = 0; targetIndex < targetBosses.length; targetIndex += 1) {
        const targetBossIndex = targetBosses[targetIndex];
        const nextPlan = _cloneRaidPlanEntries(simulation.plan);
        const donorEntry = nextPlan.futureByMember.get(donor.memberKey);
        if (!donorEntry || !donorEntry.knives[donor.knifeIndex]) continue;
        const donorOther = donorEntry.knives.filter((knife, index) => index !== donor.knifeIndex);
        if (!_canRetargetRaidRoundPriorityDonor(
          context,
          donorMember,
          donorOther,
          targetBossIndex,
          conflictPass === 1
        )) continue;

        donorEntry.knives[donor.knifeIndex] = _retargetRaidRoundKnife(
          context,
          donorMember,
          donorEntry.knives[donor.knifeIndex],
          targetBossIndex,
          donorOther
        );
        const rerun = _simulateRaidRoundSelectedPlan(context, _stripRaidUnlockedStages(nextPlan));
        if (!_isRaidRoundFixedKnifeLanded(rerun.plan, fixedItem)) continue;
        if (!_raidRoundKeepsLandedFixedKnives(rerun.plan, currentFixedKeys)) continue;
        if (_countRaidRoundLandedKnives(rerun.plan) < currentLandedCount) continue;
        if ((rerun.deficitBosses || []).length > (simulation.deficitBosses || []).length) continue;
        if ((rerun.deficitRemainingTrue || 0) > (simulation.deficitRemainingTrue || 0)) continue;
        if (_getRaidRoundPhase3OverflowTrue(context, rerun.plan) > currentOverflowTrue) continue;
        return rerun;
      }
    }
  }
  return null;
}

function _collectRaidRoundFixedKnifeDonors(phasePlan, fixedItem) {
  const donors = [];
  phasePlan.futureByMember.forEach((entry, memberKey) => {
    (entry.knives || []).forEach((knife, knifeIndex) => {
      if (knife.kind === "fixed" || !(knife.stage > 0)) return;
      if (knife.bossIndex !== fixedItem.knife.bossIndex) return;
      donors.push({
        memberKey,
        memberName: entry.name || memberKey,
        memberSync: entry.sync || 0,
        knifeIndex,
        knife,
      });
    });
  });
  donors.sort((a, b) => {
    const aLower = a.memberSync < fixedItem.memberSync;
    const bLower = b.memberSync < fixedItem.memberSync;
    if (aLower !== bLower) return aLower ? -1 : 1;
    if (a.memberSync !== b.memberSync) return b.memberSync - a.memberSync;
    if ((a.knife.effectiveTrue || 0) !== (b.knife.effectiveTrue || 0)) {
      return (a.knife.effectiveTrue || 0) - (b.knife.effectiveTrue || 0);
    }
    return a.memberName.localeCompare(b.memberName);
  });
  return donors;
}

function _canRetargetRaidRoundPriorityDonor(context, member, otherKnives, bossIndex, allowElementConflict) {
  if (_memberBlocksRaidBoss(member, bossIndex)) return false;
  if (bossIndex !== RAID_TAIL_BOSS_INDEX && _memberHasRaidBoss(member, bossIndex, otherKnives)) return false;
  const elementKey = context.bossElements[bossIndex] || "";
  return !!allowElementConflict || _canUseRaidElement(member, elementKey, otherKnives);
}

function _getRaidRoundFixedKnifeKey(memberKey, knife) {
  return [memberKey, knife.slotIndex || 0, knife.sequence || 0, knife.bossIndex || 0].join(":");
}

function _getRaidRoundLandedFixedKnifeKeys(phasePlan) {
  const keys = new Set();
  phasePlan.futureByMember.forEach((entry, memberKey) => {
    (entry.knives || []).forEach(knife => {
      if (knife.kind === "fixed" && (knife.stage || 0) > 0) {
        keys.add(_getRaidRoundFixedKnifeKey(memberKey, knife));
      }
    });
  });
  return keys;
}

function _raidRoundKeepsLandedFixedKnives(phasePlan, requiredKeys) {
  const landedKeys = _getRaidRoundLandedFixedKnifeKeys(phasePlan);
  for (const key of requiredKeys) {
    if (!landedKeys.has(key)) return false;
  }
  return true;
}

function _isRaidRoundFixedKnifeLanded(phasePlan, fixedItem) {
  const entry = phasePlan.futureByMember.get(fixedItem.memberKey);
  if (!entry) return false;
  return (entry.knives || []).some(knife => {
    return knife.kind === "fixed"
      && (knife.slotIndex || 0) === (fixedItem.knife.slotIndex || 0)
      && (knife.sequence || 0) === (fixedItem.knife.sequence || 0)
      && knife.bossIndex === fixedItem.knife.bossIndex
      && (knife.stage || 0) > 0;
  });
}

function _balanceRaidRoundFireIronCoverage(context, initialSimulation) {
  let simulation = initialSimulation || _simulateRaidRoundSelectedPlan(
    context,
    _createRaidPhasePlanEntries(_getRaidMembersBySyncDesc(context.members))
  );
  const maxSteps = Math.max(1, (context.members || []).length);

  for (let step = 0; step < maxSteps; step += 1) {
    const coverage = _getRaidRoundFireIronCoverage(context, simulation.plan);
    if (!coverage.missing.length || !coverage.both.length) return simulation;
    const candidates = _buildRaidRoundFireIronSwapCandidates(context, simulation.plan, coverage);
    let accepted = null;

    for (let i = 0; i < candidates.length; i += 1) {
      const candidate = candidates[i];
      const nextPlan = _cloneRaidPlanEntries(simulation.plan);
      const donorEntry = nextPlan.futureByMember.get(candidate.donor.member.key);
      const recipientEntry = nextPlan.futureByMember.get(candidate.recipient.member.key);
      if (!donorEntry || !recipientEntry) continue;

      const donorOther = donorEntry.knives.filter((knife, index) => index !== candidate.donorKnifeIndex);
      const recipientOther = recipientEntry.knives.filter((knife, index) => index !== candidate.recipientKnifeIndex);
      donorEntry.knives[candidate.donorKnifeIndex] = _retargetRaidRoundKnife(
        context,
        candidate.donor.member,
        donorEntry.knives[candidate.donorKnifeIndex],
        candidate.recipientKnife.bossIndex,
        donorOther
      );
      recipientEntry.knives[candidate.recipientKnifeIndex] = _retargetRaidRoundKnife(
        context,
        candidate.recipient.member,
        recipientEntry.knives[candidate.recipientKnifeIndex],
        candidate.donorKnife.bossIndex,
        recipientOther
      );

      const rerun = _simulateRaidRoundSelectedPlan(context, _stripRaidUnlockedStages(nextPlan));
      if ((rerun.deficitBosses || []).length > (simulation.deficitBosses || []).length) continue;
      if ((rerun.deficitRemainingTrue || 0) > (simulation.deficitRemainingTrue || 0)) continue;
      if (_getRaidRoundPhase3OverflowTrue(context, rerun.plan)
        > _getRaidRoundPhase3OverflowTrue(context, simulation.plan)) continue;

      const nextCoverage = _getRaidRoundFireIronCoverage(context, rerun.plan);
      if (nextCoverage.missing.length >= coverage.missing.length) continue;
      if (nextCoverage.both.length >= coverage.both.length) continue;
      accepted = rerun;
      break;
    }

    if (!accepted) return simulation;
    simulation = accepted;
  }
  return simulation;
}

function _getRaidRoundFireIronCoverage(context, phasePlan) {
  const burnKey = normalizeLabel("\u71c3\u71d2");
  const ironKey = normalizeLabel("\u9435\u7532");
  const result = { missing: [], both: [] };

  (context.members || []).forEach(member => {
    const entry = phasePlan && phasePlan.futureByMember
      ? phasePlan.futureByMember.get(member.key)
      : null;
    const landed = (member.historyKnives || []).concat(entry ? entry.knives : [])
      .filter(knife => (knife.stage || 0) > 0);
    const hasBurn = landed.some(knife => normalizeLabel(knife.elementKey) === burnKey);
    const hasIron = landed.some(knife => normalizeLabel(knife.elementKey) === ironKey);
    const item = { member, entry, hasBurn, hasIron };
    if (!hasBurn && !hasIron) result.missing.push(item);
    if (hasBurn && hasIron) result.both.push(item);
  });

  const compare = (a, b) => {
    if ((a.member.sync || 0) !== (b.member.sync || 0)) return (a.member.sync || 0) - (b.member.sync || 0);
    return String(a.member.name || "").localeCompare(String(b.member.name || ""));
  };
  result.missing.sort(compare);
  result.both.sort(compare);
  return result;
}

function _buildRaidRoundFireIronSwapCandidates(context, phasePlan, coverage) {
  const candidates = [];
  (coverage.missing || []).forEach(recipient => {
    const recipientEntry = phasePlan.futureByMember.get(recipient.member.key);
    if (!recipientEntry) return;
    (recipientEntry.knives || []).forEach((recipientKnife, recipientKnifeIndex) => {
      if (recipientKnife.kind === "fixed" || !(recipientKnife.stage > 0)) return;
      if (_isRaidFireOrIronElement(recipientKnife.elementKey)) return;

      (coverage.both || []).forEach(donor => {
        if (donor.member.key === recipient.member.key) return;
        const donorEntry = phasePlan.futureByMember.get(donor.member.key);
        if (!donorEntry) return;
        (donorEntry.knives || []).forEach((donorKnife, donorKnifeIndex) => {
          if (donorKnife.kind === "fixed" || !(donorKnife.stage > 0)) return;
          if (!_isRaidFireOrIronElement(donorKnife.elementKey)) return;
          const donorOther = donorEntry.knives.filter((knife, index) => index !== donorKnifeIndex);
          const recipientOther = recipientEntry.knives.filter((knife, index) => index !== recipientKnifeIndex);
          if (!_canRaidRoundMemberTakeSwapBoss(
            context,
            donor.member,
            donorOther,
            recipientKnife.bossIndex
          )) return;
          if (!_canRaidRoundMemberTakeSwapBoss(
            context,
            recipient.member,
            recipientOther,
            donorKnife.bossIndex
          )) return;

          candidates.push({
            donor,
            recipient,
            donorKnife,
            recipientKnife,
            donorKnifeIndex,
            recipientKnifeIndex,
            rankCost: _getRaidElementRank(donor.member, recipientKnife.elementKey)
              + _getRaidElementRank(recipient.member, donorKnife.elementKey),
          });
        });
      });
    });
  });

  candidates.sort((a, b) => {
    if (a.rankCost !== b.rankCost) return a.rankCost - b.rankCost;
    if ((a.recipient.member.sync || 0) !== (b.recipient.member.sync || 0)) {
      return (a.recipient.member.sync || 0) - (b.recipient.member.sync || 0);
    }
    if ((a.donor.member.sync || 0) !== (b.donor.member.sync || 0)) {
      return (a.donor.member.sync || 0) - (b.donor.member.sync || 0);
    }
    if (a.donorKnife.bossIndex !== b.donorKnife.bossIndex) {
      return a.donorKnife.bossIndex - b.donorKnife.bossIndex;
    }
    return a.recipientKnife.bossIndex - b.recipientKnife.bossIndex;
  });
  return candidates;
}

function _canRaidRoundMemberTakeSwapBoss(context, member, otherKnives, bossIndex) {
  if (_memberBlocksRaidBoss(member, bossIndex)) return false;
  if (_memberHasRaidBoss(member, bossIndex, otherKnives)) return false;
  const elementKey = context.bossElements[bossIndex] || "";
  return _canUseRaidElement(member, elementKey, otherKnives);
}

function _retargetRaidRoundKnife(context, member, knife, bossIndex, otherKnives) {
  const damageYi = _estimateRaidKnifeDamageYi(
    member.sync,
    context.baseSync,
    context.baseDamageYi[bossIndex]
  );
  const repeatsTailBoss = bossIndex === RAID_TAIL_BOSS_INDEX
    && _memberHasRaidBoss(member, bossIndex, otherKnives);
  return Object.assign({}, knife, {
    bossIndex,
    stage: repeatsTailBoss ? RAID_TAIL_STAGE : 0,
    lockedStage: repeatsTailBoss,
    elementKey: context.bossElements[bossIndex] || "",
    groupRank: _getRaidElementRank(member, context.bossElements[bossIndex] || ""),
    damageYi,
    damageTrue: Math.round(damageYi * RAID_YI_TO_TRUE),
    effectiveTrue: Math.round(damageYi * RAID_YI_TO_TRUE),
  });
}
/*
function _recordRaidRoundSelectionShortageFixed(context, targetKnifeCount, selectedPlan) {
  const selectedCount = _countRaidPlanKnives(selectedPlan);
  const missingKnives = Math.max(0, (targetKnifeCount || 0) - selectedCount);
  if (missingKnives <= 0) return;
  _recordRaidShortageLog(context, {
    type: "roundSelection",
    missingKnives,
    reason: "三輪選刀後仍不足應排刀數",
  });
}
*/
function _recordRaidRoundSelectionCheck(context, targetKnifeCount, selectedPlan) {
  const selectedCount = _countRaidPlanKnives(selectedPlan);
  const missingKnives = Math.max(0, (targetKnifeCount || 0) - selectedCount);
  if (missingKnives <= 0) return;
  _recordRaidShortageLog(context, {
    type: "roundSelection",
    missingKnives,
    reason: "round selection shortage",
  });
}

/*
function _recordRaidRoundSelectionShortage(context, targetKnifeCount, selectedPlan) {
  const selectedCount = _countRaidPlanKnives(selectedPlan);
  const missingKnives = Math.max(0, (targetKnifeCount || 0) - selectedCount);
  if (missingKnives <= 0) return;
  _recordRaidShortageLog(context, {
    type: "roundSelection",
    missingKnives,
    reason: "三輪選刀後仍不足應排刀數",
  });
}

*/
function _stabilizeRaidRoundPlan(context, selectedPlan, candidatePool, initialSimulation) {
  let workingPlan = _cloneRaidPlanEntries(selectedPlan);
  let simulation = initialSimulation || _simulateRaidRoundSelectedPlan(context, workingPlan);
  const seen = new Set();

  for (let step = 0; step < 50; step += 1) {
    workingPlan = _stripRaidUnlockedStages(workingPlan);
    simulation = _simulateRaidRoundSelectedPlan(context, workingPlan);
    const strippedPlan = _stripRaidUnlockedStages(simulation.plan);
    const baseSignature = _getRaidRoundPlanSignature(strippedPlan);
    const overflowSignature = _getRaidRoundPhase3OverflowBosses(context, simulation.plan)
      .map(item => `${item.bossIndex}:${item.overflowTrue}`)
      .join(",");
    const signature = `${baseSignature}#${overflowSignature}`;
    if (seen.has(signature)) return simulation;
    seen.add(signature);

    if (simulation.deficitBosses.length) {
      const cycle = _runRaidRoundReassignCycles(context, strippedPlan, candidatePool, simulation);
      const nextSignature = _getRaidRoundPlanSignature(cycle.selectedPlan);
      if (nextSignature === baseSignature) return simulation;
      workingPlan = cycle.selectedPlan;
      continue;
    }

    const overflowCycle = _runRaidRoundPhase3OverflowCycles(context, strippedPlan, candidatePool, simulation);
    const overflowChanged = _getRaidRoundPlanSignature(overflowCycle.selectedPlan) !== baseSignature
      || _getRaidRoundPhase3OverflowTrue(context, overflowCycle.simulation.plan) !== _getRaidRoundPhase3OverflowTrue(context, simulation.plan);
    if (overflowChanged) {
      workingPlan = overflowCycle.selectedPlan;
      continue;
    }

    return simulation;
  }

  return _simulateRaidRoundSelectedPlan(context, _stripRaidUnlockedStages(workingPlan));
}

function _runRaidRoundPass(context, finalPlan, roundState, candidatePool, roundIndex, orderedMembers, startSequence, mode) {
  let sequence = startSequence;
  for (let i = 0; i < orderedMembers.length; i += 1) {
    const member = orderedMembers[i];
    const knife = _chooseRaidRoundKnife(
      context,
      member,
      roundIndex,
      finalPlan,
      roundState,
      candidatePool,
      mode || "compromiseFill"
    );
    if (!knife) continue;
    knife.round = roundIndex;
    knife.sequence = sequence;
    _appendRaidPlannedKnife(finalPlan, member.key, knife);
    _consumeRaidRoundStateKnife(roundState, member.key, knife.sourceKnife);
    sequence += 1;
  }
  return sequence;
}

function _runRaidRoundFallbackPasses(context, finalPlan, roundState, candidatePool, orderedMembers, startSequence, targetKnifeCount) {
  let sequence = startSequence;
  let roundIndex = 4;
  while (_countRaidPlanKnives(finalPlan) < (targetKnifeCount || 0)) {
    const beforeCount = _countRaidPlanKnives(finalPlan);
    sequence = _runRaidRoundPass(
      context,
      finalPlan,
      roundState,
      candidatePool,
      roundIndex,
      orderedMembers,
      sequence,
      "compromiseFill"
    );
    const afterCount = _countRaidPlanKnives(finalPlan);
    if (afterCount <= beforeCount) break;
    roundIndex += 1;
  }
  return sequence;
}

function _chooseRaidRoundKnife(context, member, roundIndex, finalPlan, roundState, candidatePool, mode) {
  const currentKnives = _getRaidPlannedKnivesForMember(finalPlan, member.key);
  const fixedKnife = _chooseRaidRoundFixedKnife(member, currentKnives, roundState);
  if (fixedKnife) return fixedKnife;
  return _chooseRaidRoundAutoKnife(
    context,
    member,
    roundIndex,
    finalPlan,
    currentKnives,
    roundState,
    candidatePool,
    mode || "compromiseFill"
  );
}

function _chooseRaidRoundFixedKnife(member, currentKnives, roundState) {
  const state = roundState.byMember.get(member.key);
  if (!state || !state.fixedKnives.length) return null;
  let best = null;
  for (let i = 0; i < state.fixedKnives.length; i += 1) {
    const sourceKnife = state.fixedKnives[i];
    const knife = {
      sourceKnife,
      bossIndex: sourceKnife.bossIndex,
      stage: sourceKnife.stage || 0,
      elementKey: sourceKnife.elementKey,
      groupRank: sourceKnife.groupRank || 0,
      damageYi: sourceKnife.damageYi,
      damageTrue: sourceKnife.damageTrue,
      effectiveTrue: sourceKnife.damageTrue,
      kind: "fixed",
      lockedStage: !!sourceKnife.lockedStage,
      slotIndex: sourceKnife.slotIndex,
    };
    if (!best || _isBetterRaidRoundSelectionKnife(knife, best)) best = knife;
  }
  return best;
}

function _chooseRaidRoundAutoKnife(context, member, roundIndex, finalPlan, currentKnives, roundState, candidatePool, mode) {
  const state = roundState.byMember.get(member.key);
  if (!state || !state.autoKnives.length) return null;
  let orderedTokens = state.autoKnives.slice();
  if (mode === "strictTopFill") {
    orderedTokens = orderedTokens.filter(knife => (knife.groupRank || 0) === 0);
  }
  if (!orderedTokens.length) return null;
  orderedTokens.sort((a, b) => {
    if ((a.groupRank || 0) !== (b.groupRank || 0)) return (a.groupRank || 0) - (b.groupRank || 0);
    if ((a.topOrder || 0) !== (b.topOrder || 0)) return (a.topOrder || 0) - (b.topOrder || 0);
    if ((b.damageTrue || 0) !== (a.damageTrue || 0)) return (b.damageTrue || 0) - (a.damageTrue || 0);
    return (a.bossIndex || 0) - (b.bossIndex || 0);
  });
  const selection = _buildRaidRoundAutoSelectionOptions(
    context,
    member,
    roundIndex,
    finalPlan,
    currentKnives,
    orderedTokens,
    candidatePool,
    mode || "compromiseFill"
  );
  if (_isRaidRoundTraceMember(context, member)) {
    _logRaidRoundTrace(
      context,
      member.name,
      "round=%s ordered=%s deficits=%s current=%s",
      roundIndex,
      orderedTokens.map(knife => _formatRaidRoundTraceKnifeToken(context, member, knife)).join(" | "),
      selection.deficitSummary || "-",
      currentKnives.map(knife => _formatRaidRoundTraceSelectedKnife(context, member, knife)).join(" | ") || "-"
    );
  }
  if (!selection.options.length) {
    _recordRaidShortageLog(context, {
      type: "roundSelectionMember",
      memberName: member.name,
      round: roundIndex,
      sourceKnifeCount: orderedTokens.length,
      candidateCount: selection.diagnostics.candidateCount,
      coveredFiltered: selection.diagnostics.coveredFiltered,
      rejectedDeficit: selection.diagnostics.rejectedDeficit,
      rejectedOverflow: selection.diagnostics.rejectedOverflow,
      reason: selection.diagnostics.reason,
    });
    if (_isRaidRoundTraceMember(context, member)) {
      _logRaidRoundTrace(
        context,
        member.name,
        "round=%s no-selection diagnostics=candidates:%s covered:%s rejectDeficit:%s rejectOverflow:%s reason=%s trace=%s",
        roundIndex,
        selection.diagnostics.candidateCount,
        selection.diagnostics.coveredFiltered,
        selection.diagnostics.rejectedDeficit,
        selection.diagnostics.rejectedOverflow,
        selection.diagnostics.reason,
        (selection.traceEntries || []).join(" || ") || "-"
      );
    }
    return null;
  }
  if (_isRaidRoundTraceMember(context, member)) {
    _logRaidRoundTrace(
      context,
      member.name,
      "round=%s selected=%s trace=%s",
      roundIndex,
      _formatRaidRoundTraceSelectedKnife(context, member, selection.options[0].knife),
      (selection.traceEntries || []).join(" || ") || "-"
    );
  }
  return selection.options[0].knife;
}

function _getRaidRoundAllowedElements(member, sourceKnife, candidatePool, bossElements) {
  const groups = _getRaidMemberPlanElementGroups(member, candidatePool, bossElements);
  const groupRank = sourceKnife.groupRank || 0;
  if (groupRank < 0 || groupRank >= groups.length) return null;
  return groups[groupRank] ? groups[groupRank].slice() : null;
}

function _createRaidRoundState(memberPlan) {
  const state = {
    byMember: new Map(),
  };
  memberPlan.futureByMember.forEach((entry, memberKey) => {
    state.byMember.set(memberKey, {
      fixedKnives: entry.knives.filter(knife => knife.kind === "fixed").map(knife => Object.assign({}, knife)),
      autoKnives: entry.knives.filter(knife => knife.kind !== "fixed").map(knife => Object.assign({}, knife)),
    });
  });
  return state;
}

function _cloneRaidRoundState(roundState) {
  const clone = { byMember: new Map() };
  roundState.byMember.forEach((entry, memberKey) => {
    clone.byMember.set(memberKey, {
      fixedKnives: entry.fixedKnives.map(knife => Object.assign({}, knife)),
      autoKnives: entry.autoKnives.map(knife => Object.assign({}, knife)),
    });
  });
  return clone;
}

function _replaceRaidRoundState(target, source) {
  target.byMember = new Map();
  source.byMember.forEach((entry, memberKey) => {
    target.byMember.set(memberKey, {
      fixedKnives: entry.fixedKnives.map(knife => Object.assign({}, knife)),
      autoKnives: entry.autoKnives.map(knife => Object.assign({}, knife)),
    });
  });
}

function _consumeRaidRoundStateKnife(roundState, memberKey, sourceKnife) {
  if (!sourceKnife) return;
  const entry = roundState.byMember.get(memberKey);
  if (!entry) return;
  const list = sourceKnife.kind === "fixed" ? entry.fixedKnives : entry.autoKnives;
  const index = list.findIndex(knife => {
    return (knife.kind || "auto") === (sourceKnife.kind || "auto")
      && (knife.bossIndex || 0) === (sourceKnife.bossIndex || 0)
      && (knife.stage || 0) === (sourceKnife.stage || 0)
      && (knife.groupRank || 0) === (sourceKnife.groupRank || 0);
  });
  if (index >= 0) list.splice(index, 1);
}

function _buildRaidRoundRemainingPlan(memberPlan, selectedPlan) {
  const remainingPlan = _cloneRaidPlanEntries(memberPlan);
  if (!selectedPlan || !selectedPlan.futureByMember) return remainingPlan;
  selectedPlan.futureByMember.forEach((entry, memberKey) => {
    const remainingEntry = remainingPlan.futureByMember.get(memberKey);
    if (!remainingEntry) return;
    (entry.knives || []).forEach(selectedKnife => {
      const index = remainingEntry.knives.findIndex(sourceKnife => {
        return _isRaidRoundSameSourceKnife(sourceKnife, selectedKnife);
      });
      if (index >= 0) remainingEntry.knives.splice(index, 1);
    });
  });
  return remainingPlan;
}

function _getRaidRoundSelectionState(context, selectedPlan) {
  const simulation = _simulateRaidRoundSelectedPlan(context, selectedPlan);
  return {
    simulation,
    deficitBosses: new Set((simulation.deficitBosses || []).map(item => item.bossIndex)),
    fullyCoveredBosses: _getRaidRoundFullyCoveredBossesFromBossStates(simulation.bossStates),
  };
}

function _buildRaidRoundAutoSelectionOptions(context, member, roundIndex, finalPlan, currentKnives, orderedTokens, candidatePool, mode) {
  if (!_memberHasRaidFireOrIron(member, currentKnives)) {
    const coverageSelection = _buildRaidRoundAutoSelectionOptionsInternal(
      context,
      member,
      roundIndex,
      finalPlan,
      currentKnives,
      orderedTokens,
      candidatePool,
      mode,
      false,
      true
    );
    if (coverageSelection.options.length) return coverageSelection;
  }
  const strictSelection = _buildRaidRoundAutoSelectionOptionsInternal(
    context,
    member,
    roundIndex,
    finalPlan,
    currentKnives,
    orderedTokens,
    candidatePool,
    mode,
    false,
    false
  );
  if (strictSelection.options.length) return strictSelection;
  return _buildRaidRoundAutoSelectionOptionsInternal(
    context,
    member,
    roundIndex,
    finalPlan,
    currentKnives,
    orderedTokens,
    candidatePool,
    mode,
    true,
    false
  );
}

function _buildRaidRoundAutoSelectionOptionsInternal(context, member, roundIndex, finalPlan, currentKnives, orderedTokens, candidatePool, mode, allowElementConflict, requireFireOrIron) {
  const selectionState = _getRaidRoundSelectionState(context, finalPlan);
  const allowTailBossRepeat = selectionState.simulation.deficitBosses.length === 0;
  const currentDeficitCount = (selectionState.simulation.deficitBosses || []).length;
  const currentDeficitRemainingTrue = selectionState.simulation.deficitRemainingTrue || 0;
  const deficitRemainingByBoss = new Map(
    (selectionState.simulation.deficitBosses || []).map(item => [item.bossIndex, item.remainingTrue || 0])
  );
  const nextSequence = _getRaidRoundNextSequence(finalPlan);
  const groups = _getRaidMemberPlanElementGroups(member, candidatePool, context.bossElements);
  const options = [];
  const traceEntries = [];
  const diagnostics = {
    candidateCount: 0,
    coveredFiltered: 0,
    rejectedDeficit: 0,
    rejectedOverflow: 0,
    reason: "無合法候選刀",
  };
  const deficitSummary = (selectionState.simulation.deficitBosses || [])
    .map(item => `b${(item.bossIndex || 0) + 1}:${item.remainingTrue || 0}`)
    .join("/");

  const strictTopFill = mode === "strictTopFill";

  for (let i = 0; i < orderedTokens.length; i += 1) {
    const sourceKnife = orderedTokens[i];
    const startGroupRank = sourceKnife.groupRank || 0;
    const endGroupRank = requireFireOrIron
      ? groups.length
      : (strictTopFill ? Math.min(startGroupRank + 1, groups.length) : groups.length);
    for (let candidateGroupRank = startGroupRank; candidateGroupRank < endGroupRank; candidateGroupRank += 1) {
      const allowedElements = groups[candidateGroupRank] ? groups[candidateGroupRank].slice() : null;
      if (!allowedElements || !allowedElements.length) continue;
      const rawCandidates = _collectRaidMemberPlanBossCandidates(member, context, currentKnives, allowedElements, {
        allowElementConflict: !!allowElementConflict,
        allowTailBossRepeat,
      }).filter(candidate => !requireFireOrIron || _isRaidFireOrIronElement(candidate.elementKey));
      diagnostics.candidateCount += rawCandidates.length;
      const allCandidates = rawCandidates.filter(candidate => {
        const blocked = selectionState.fullyCoveredBosses.has(candidate.bossIndex);
        if (blocked) {
          diagnostics.coveredFiltered += 1;
          if (_isRaidRoundTraceMember(context, member)) {
            traceEntries.push(
              `src=${_formatRaidRoundTraceKnifeToken(context, member, sourceKnife)} group=${candidateGroupRank + 1} boss=${candidate.bossIndex + 1} rank=${_formatRaidRoundTraceRank(context, member, candidate.elementKey, candidate.bossIndex)} filtered=covered trem=${deficitRemainingByBoss.get(candidate.bossIndex) || 0}`
            );
          }
        }
        return !blocked;
      });
      if (!allCandidates.length) continue;

      for (let j = 0; j < allCandidates.length; j += 1) {
        const chosenCandidate = allCandidates[j];
        const preferredDeficit = selectionState.deficitBosses.has(chosenCandidate.bossIndex);
        const isTailBossRepeat = allowTailBossRepeat
          && chosenCandidate.bossIndex === RAID_TAIL_BOSS_INDEX
          && _memberHasRaidBoss(member, chosenCandidate.bossIndex, currentKnives);
        const knife = {
          sourceKnife,
          bossIndex: chosenCandidate.bossIndex,
          stage: isTailBossRepeat ? RAID_TAIL_STAGE : 0,
          elementKey: chosenCandidate.elementKey,
          groupRank: candidateGroupRank,
          topOrder: chosenCandidate.topOrder || 0,
          damageYi: chosenCandidate.damageYi,
          damageTrue: chosenCandidate.damageTrue,
          effectiveTrue: chosenCandidate.damageTrue,
          kind: "auto",
          lockedStage: isTailBossRepeat,
          preferredDeficit,
          round: roundIndex,
          sequence: nextSequence,
        };
        const nextPlan = _cloneRaidPlanEntries(finalPlan);
        _appendRaidPlannedKnife(nextPlan, member.key, knife);
          const rerun = _simulateRaidRoundSelectedPlan(context, nextPlan);
          const rerunDeficitCount = (rerun.deficitBosses || []).length;
          const rerunDeficitRemainingTrue = rerun.deficitRemainingTrue || 0;
          const rerunOverflowTrue = _getRaidRoundPhase3OverflowTrue(context, rerun.plan);
          if (rerunDeficitCount > currentDeficitCount) {
            diagnostics.rejectedDeficit += 1;
            if (_isRaidRoundTraceMember(context, member)) {
            traceEntries.push(
              `src=${_formatRaidRoundTraceKnifeToken(context, member, sourceKnife)} group=${candidateGroupRank + 1} boss=${chosenCandidate.bossIndex + 1} rank=${_formatRaidRoundTraceRank(context, member, chosenCandidate.elementKey, chosenCandidate.bossIndex)} reject=deficit trem=${deficitRemainingByBoss.get(chosenCandidate.bossIndex) || 0} deficit=${currentDeficitCount}->${rerunDeficitCount} remain=${currentDeficitRemainingTrue}->${rerunDeficitRemainingTrue} overflow=${rerunOverflowTrue}`
            );
          }
          continue;
        }
        if (rerunDeficitCount === currentDeficitCount
          && rerun.deficitRemainingTrue > currentDeficitRemainingTrue) {
          diagnostics.rejectedDeficit += 1;
          if (_isRaidRoundTraceMember(context, member)) {
            traceEntries.push(
              `src=${_formatRaidRoundTraceKnifeToken(context, member, sourceKnife)} group=${candidateGroupRank + 1} boss=${chosenCandidate.bossIndex + 1} rank=${_formatRaidRoundTraceRank(context, member, chosenCandidate.elementKey, chosenCandidate.bossIndex)} reject=deficitRemaining trem=${deficitRemainingByBoss.get(chosenCandidate.bossIndex) || 0} deficit=${currentDeficitCount}->${rerunDeficitCount} remain=${currentDeficitRemainingTrue}->${rerunDeficitRemainingTrue} overflow=${rerunOverflowTrue}`
            );
          }
          continue;
        }
        if (rerunOverflowTrue > 0) {
          diagnostics.rejectedOverflow += 1;
          if (_isRaidRoundTraceMember(context, member)) {
            traceEntries.push(
              `src=${_formatRaidRoundTraceKnifeToken(context, member, sourceKnife)} group=${candidateGroupRank + 1} boss=${chosenCandidate.bossIndex + 1} rank=${_formatRaidRoundTraceRank(context, member, chosenCandidate.elementKey, chosenCandidate.bossIndex)} reject=overflow trem=${deficitRemainingByBoss.get(chosenCandidate.bossIndex) || 0} deficit=${rerunDeficitCount} remain=${rerunDeficitRemainingTrue} overflow=${rerunOverflowTrue} preferred=${preferredDeficit ? 1 : 0} dmg=${chosenCandidate.damageTrue || 0}`
            );
          }
          continue;
        }

        if (_isRaidRoundTraceMember(context, member)) {
          traceEntries.push(
            `src=${_formatRaidRoundTraceKnifeToken(context, member, sourceKnife)} group=${candidateGroupRank + 1} boss=${chosenCandidate.bossIndex + 1} rank=${_formatRaidRoundTraceRank(context, member, chosenCandidate.elementKey, chosenCandidate.bossIndex)} accept preferred=${preferredDeficit ? 1 : 0} trem=${deficitRemainingByBoss.get(chosenCandidate.bossIndex) || 0} deficit=${rerunDeficitCount} remain=${rerunDeficitRemainingTrue} overflow=${rerunOverflowTrue} dmg=${chosenCandidate.damageTrue || 0}`
          );
        }
          options.push({
            knife,
            simulation: rerun,
            preferredDeficit,
            targetRemainingTrue: deficitRemainingByBoss.get(chosenCandidate.bossIndex) || 0,
            groupRank: candidateGroupRank,
            deficitCount: rerunDeficitCount,
            deficitRemainingTrue: rerunDeficitRemainingTrue,
            overflowTrue: rerunOverflowTrue,
          });
      }
    }
  }

  let acceptedOptions = options.slice();
  if (options.some(option => option.preferredDeficit)) {
    acceptedOptions = options.filter(option => option.preferredDeficit);
  } else if (strictTopFill) {
    acceptedOptions = [];
  }

  acceptedOptions.sort(_compareRaidRoundAutoSelectionOption);
  if (_isRaidRoundTraceMember(context, member) && acceptedOptions.length) {
    traceEntries.push(
        "ranked=" + acceptedOptions.slice(0, 5).map(option => {
          return `boss${(option.knife.bossIndex || 0) + 1}/g${(option.groupRank || 0) + 1}/o${(option.knife.topOrder || 0) + 1}/r${_getRaidRoundTraceRankValue(context, member, option.knife)}/pref${option.preferredDeficit ? 1 : 0}/trem${option.targetRemainingTrue || 0}/def${option.deficitCount || 0}/rem${option.deficitRemainingTrue || 0}/ov${option.overflowTrue || 0}/dmg${option.knife.damageTrue || 0}`;
        }).join(" | ")
      );
    }
  if (acceptedOptions.length) diagnostics.reason = "OK";
  else if (diagnostics.rejectedDeficit > 0 || diagnostics.rejectedOverflow > 0) diagnostics.reason = "候選刀完整模擬後被拒絕";
  else if (diagnostics.coveredFiltered > 0) diagnostics.reason = "候選王已足以清到 phase3";
  return {
    options: acceptedOptions,
    diagnostics,
    traceEntries,
    deficitSummary,
  };
}

function _isRaidRoundTraceMember(context, memberOrName) {
  const traceName = String((context && context.raidRoundTraceMemberName) || "AIW").trim().toUpperCase();
  const memberName = typeof memberOrName === "string"
    ? memberOrName
    : (memberOrName && memberOrName.name) || "";
  return String(memberName || "").trim().toUpperCase() === traceName;
}

function _logRaidRoundTrace(context, memberName, format) {
  if (!_isRaidRoundTraceMember(context, memberName)) return;
  const args = Array.prototype.slice.call(arguments, 3);
  Logger.log.apply(Logger, [`raid-round-trace member=${memberName} ${format}`].concat(args));
}

function _getRaidRoundTraceRankValue(context, member, knifeOrElementKey, fallbackBossIndex) {
  if (!member) return 999;
  let elementKey = "";
  if (typeof knifeOrElementKey === "string") {
    elementKey = knifeOrElementKey;
  } else if (knifeOrElementKey && knifeOrElementKey.elementKey) {
    elementKey = knifeOrElementKey.elementKey;
  }
  if (!elementKey && context && context.bossElements && typeof fallbackBossIndex === "number") {
    elementKey = context.bossElements[fallbackBossIndex] || "";
  }
  const rank = _getRaidScorePercent(member, elementKey);
  return Number.isFinite(rank) ? rank : 999;
}

function _formatRaidRoundTraceRank(context, member, knifeOrElementKey, fallbackBossIndex) {
  return `r${_getRaidRoundTraceRankValue(context, member, knifeOrElementKey, fallbackBossIndex)}`;
}

function _formatRaidRoundTraceKnifeToken(context, member, knife) {
  return `b${(knife.bossIndex || 0) + 1}/g${(knife.groupRank || 0) + 1}/o${(knife.topOrder || 0) + 1}/${_formatRaidRoundTraceRank(context, member, knife)}/d${knife.damageTrue || 0}`;
}

function _formatRaidRoundTraceSelectedKnife(context, member, knife) {
  return `boss=${(knife.bossIndex || 0) + 1},stage=${knife.stage || 0},group=${(knife.groupRank || 0) + 1},order=${(knife.topOrder || 0) + 1},rank=${_getRaidRoundTraceRankValue(context, member, knife)},round=${knife.round || 0},damage=${knife.damageTrue || 0}`;
}

function _logRaidRoundTraceFinalKnives(context) {
  if (!context || !_isRaidRoundTraceMember(context, context.raidRoundTraceMemberName)) return;
  const targetName = String(context.raidRoundTraceMemberName || "").trim().toUpperCase();
  const member = (context.members || []).find(item => String(item.name || "").trim().toUpperCase() === targetName);
  if (!member) return;
  const knives = _getRaidRunKnives(member).slice().sort((a, b) => {
    if ((a.stage || 0) !== (b.stage || 0)) return (a.stage || 0) - (b.stage || 0);
    if ((a.round || 0) !== (b.round || 0)) return (a.round || 0) - (b.round || 0);
    return (a.sequence || 0) - (b.sequence || 0);
  });
  _logRaidRoundTrace(
    context,
    member.name,
    "final=%s",
    knives.map(knife => _formatRaidRoundTraceSelectedKnife(context, member, knife)).join(" | ") || "-"
  );
}

function _compareRaidRoundAutoSelectionOption(a, b) {
  if ((a.groupRank || 0) !== (b.groupRank || 0)) {
    return (a.groupRank || 0) - (b.groupRank || 0);
  }
  if ((a.knife.topOrder || 0) !== (b.knife.topOrder || 0)) {
    return (a.knife.topOrder || 0) - (b.knife.topOrder || 0);
  }
  if ((a.targetRemainingTrue || 0) !== (b.targetRemainingTrue || 0)) {
    return (b.targetRemainingTrue || 0) - (a.targetRemainingTrue || 0);
  }
  if ((a.overflowTrue || 0) !== (b.overflowTrue || 0)) {
    return (a.overflowTrue || 0) - (b.overflowTrue || 0);
  }
  if ((a.knife.damageTrue || 0) !== (b.knife.damageTrue || 0)) {
    return (b.knife.damageTrue || 0) - (a.knife.damageTrue || 0);
  }
  return (a.knife.bossIndex || 0) - (b.knife.bossIndex || 0);
}

function _getRaidRoundNextSequence(plan) {
  let maxSequence = 0;
  if (!plan || !plan.futureByMember) return 1;
  plan.futureByMember.forEach(entry => {
    (entry.knives || []).forEach(knife => {
      if ((knife.sequence || 0) > maxSequence) maxSequence = knife.sequence || 0;
    });
  });
  return maxSequence + 1;
}

function _getRaidRoundFullyCoveredBosses(context, selectedPlan) {
  const totalSelected = Array(RAID_BOSS_COUNT).fill(0);
  if (selectedPlan && selectedPlan.futureByMember) {
    selectedPlan.futureByMember.forEach(entry => {
      (entry.knives || []).forEach(knife => {
        totalSelected[knife.bossIndex] += knife.damageTrue || 0;
      });
    });
  }

  const fullCapacity = _getRaidRoundBossFullCapacity(context);
  const covered = new Set();
  for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
    if ((totalSelected[bossIndex] || 0) >= (fullCapacity[bossIndex] || 0)) {
      covered.add(bossIndex);
    }
  }
  return covered;
}

function _getRaidRoundFullyCoveredBossesFromBossStates(bossStates) {
  const covered = new Set();
  if (!bossStates || !bossStates.length) return covered;
  const finiteCleared = _isRaidFiniteFrontClearedFromBossStates(bossStates);
  for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
    const hasRemaining = _raidBossHasFiniteRemaining(bossStates, bossIndex);
    if (!hasRemaining && (bossIndex !== RAID_TAIL_BOSS_INDEX || !finiteCleared)) covered.add(bossIndex);
  }
  return covered;
}

function _getRaidRoundBossFullCapacity(context) {
  const totals = Array(RAID_BOSS_COUNT).fill(0);
  for (let phase = 0; phase < Math.min(RAID_PHASE_COUNT, (context.bossStates || []).length); phase += 1) {
    const state = context.bossStates[phase];
    if (!state) continue;
    for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
      totals[bossIndex] += state.bosses[bossIndex] || 0;
    }
  }
  return totals;
}

function _isRaidRoundSameSourceKnife(sourceKnife, selectedKnife) {
  if (!sourceKnife || !selectedKnife) return false;
  const selectedSource = selectedKnife.sourceKnife || selectedKnife;
  const selectedKind = selectedSource.sourceKind || selectedSource.kind || "auto";
  const selectedBossIndex = Number.isFinite(selectedSource.sourceBossIndex)
    ? selectedSource.sourceBossIndex
    : (selectedSource.bossIndex || 0);
  const selectedStage = Number.isFinite(selectedSource.sourceStage)
    ? selectedSource.sourceStage
    : (selectedSource.stage || 0);
  const selectedLockedStage = Object.prototype.hasOwnProperty.call(selectedSource, "sourceLockedStage")
    ? !!selectedSource.sourceLockedStage
    : !!selectedSource.lockedStage;
  const selectedElementKey = selectedSource.sourceElementKey || selectedSource.elementKey || "";
  const selectedGroupRank = Number.isFinite(selectedSource.sourceGroupRank)
    ? selectedSource.sourceGroupRank
    : (selectedSource.groupRank || 0);
  return (sourceKnife.kind || "auto") === selectedKind
    && (sourceKnife.bossIndex || 0) === selectedBossIndex
    && (sourceKnife.stage || 0) === selectedStage
    && !!sourceKnife.lockedStage === selectedLockedStage
    && (sourceKnife.elementKey || "") === selectedElementKey
    && (sourceKnife.groupRank || 0) === selectedGroupRank;
}

function _replaceRaidPlanEntries(target, source) {
  target.futureByMember = new Map();
  source.futureByMember.forEach((entry, memberKey) => {
    target.futureByMember.set(memberKey, {
      sync: entry.sync,
      name: entry.name,
      sourceMember: entry.sourceMember,
      knives: entry.knives.map(knife => Object.assign({}, knife)),
    });
  });
}

function _simulateRaidRoundSelectedPlan(context, selectedPlan) {
  const planningContext = _createRaidPlanningContext(context);
  const simulatedPlan = _cloneRaidPlanEntries(selectedPlan);
  _normalizeRaidRoundPlanStagesByBoss(context, simulatedPlan);

  const ordered = [];
  simulatedPlan.futureByMember.forEach((entry, memberKey) => {
    entry.knives.forEach(knife => {
      ordered.push({
        memberKey,
        memberName: entry.name || memberKey,
        memberSync: entry.sync || 0,
        knife,
      });
    });
  });

  ordered.sort((a, b) => {
    if ((a.knife.stage || 0) !== (b.knife.stage || 0)) return (a.knife.stage || 0) - (b.knife.stage || 0);
    if (a.memberSync !== b.memberSync) return a.memberSync - b.memberSync;
    const nameCmp = a.memberName.localeCompare(b.memberName);
    if (nameCmp !== 0) return nameCmp;
    if ((a.knife.round || 0) !== (b.knife.round || 0)) return (a.knife.round || 0) - (b.knife.round || 0);
    return (a.knife.sequence || 0) - (b.knife.sequence || 0);
  });

  for (let i = 0; i < ordered.length; i += 1) {
    const item = ordered[i];
    const knife = item.knife;
    if (!knife.stage) continue;
    _applyRaidDamageToBossStates(planningContext.bossStates, knife.stage, knife.bossIndex, knife.damageTrue);
  }

  return {
    plan: simulatedPlan,
    remainingPlan: _buildRaidRoundRemainingPlan(
      context._raidRoundMemberPlanSource || _createRaidPhasePlanEntries(_getRaidMembersBySyncDesc(context.members)),
      simulatedPlan
    ),
    bossStates: planningContext.bossStates,
    deficitBosses: _getRaidFrontDeficitBosses(planningContext.bossStates),
    deficitRemainingTrue: _getRaidRoundDeficitRemainingTrue(planningContext.bossStates),
  };
}

function _runRaidRoundReassignCycles(context, selectedPlan, candidatePool, initialSimulation) {
  let workingPlan = _cloneRaidPlanEntries(selectedPlan);
  let simulation = initialSimulation || _simulateRaidRoundSelectedPlan(context, workingPlan);
  const seen = new Set();

  for (let step = 0; step < 50; step += 1) {
    const stabilizedPlan = _stripRaidUnlockedStages(simulation.plan);
    const signature = _getRaidRoundPlanSignature(stabilizedPlan);
    if (seen.has(signature)) {
      return {
        selectedPlan: stabilizedPlan,
        simulation,
      };
    }
    seen.add(signature);
    if (!simulation.deficitBosses.length) {
      return {
        selectedPlan: stabilizedPlan,
        simulation,
      };
    }
    const options = _buildRaidRoundReassignOptions(context, stabilizedPlan, candidatePool, simulation);
    if (!options.length) {
      return {
        selectedPlan: stabilizedPlan,
        simulation,
      };
    }
    workingPlan = _stripRaidUnlockedStages(options[0].simulation.plan);
    simulation = _simulateRaidRoundSelectedPlan(context, workingPlan);
  }

  return {
    selectedPlan: _stripRaidUnlockedStages(simulation.plan),
    simulation,
  };
}

function _runRaidRoundPhase3OverflowCycles(context, selectedPlan, candidatePool, initialSimulation) {
  let workingPlan = _cloneRaidPlanEntries(selectedPlan);
  let simulation = initialSimulation || _simulateRaidRoundSelectedPlan(context, workingPlan);
  const seen = new Set();

  for (let step = 0; step < 50; step += 1) {
    const strippedPlan = _stripRaidUnlockedStages(simulation.plan);
    const overflowBosses = _getRaidRoundPhase3OverflowBosses(context, simulation.plan);
    const overflowSignature = overflowBosses.map(item => `${item.bossIndex}:${item.overflowTrue}`).join(",");
    const signature = `${_getRaidRoundPlanSignature(strippedPlan)}#${overflowSignature}`;
    if (seen.has(signature)) {
      return {
        selectedPlan: strippedPlan,
        simulation,
      };
    }
    seen.add(signature);

    if (!overflowBosses.length) {
      return {
        selectedPlan: strippedPlan,
        simulation,
      };
    }

    const options = _buildRaidRoundPhase3OverflowOptions(
      context,
      strippedPlan,
      candidatePool,
      simulation,
      overflowBosses
    );
    if (!options.length) {
      return {
        selectedPlan: strippedPlan,
        simulation,
      };
    }

    workingPlan = _stripRaidUnlockedStages(options[0].simulation.plan);
    simulation = options[0].simulation;
  }

  return {
    selectedPlan: _stripRaidUnlockedStages(simulation.plan),
    simulation,
  };
}

function _buildRaidRoundPhase3OverflowOptions(context, selectedPlan, candidatePool, simulation, overflowBosses) {
  const options = [];
  const currentOverflowTrue = _getRaidRoundPhase3OverflowTrue(context, simulation.plan);
  const currentDeficitCount = simulation.deficitBosses.length;
  const currentDeficitRemainingTrue = simulation.deficitRemainingTrue;
  const overflowBossSet = new Set((overflowBosses || []).map(item => item.bossIndex));
  const coveredBosses = _getRaidRoundFullyCoveredBossesFromBossStates(simulation.bossStates);
  const overflowKnives = [];

  selectedPlan.futureByMember.forEach((entry, memberKey) => {
    (entry.knives || []).forEach(knife => {
      if (knife.kind === "fixed") return;
      if (!overflowBossSet.has(knife.bossIndex)) return;
      overflowKnives.push({
        memberKey,
        memberName: entry.name || memberKey,
        memberSync: entry.sync || 0,
        knife,
      });
    });
  });

  overflowKnives.sort((a, b) => {
    if (a.memberSync !== b.memberSync) return a.memberSync - b.memberSync;
    const nameCmp = a.memberName.localeCompare(b.memberName);
    if (nameCmp !== 0) return nameCmp;
    if ((b.knife.round || 0) !== (a.knife.round || 0)) return (b.knife.round || 0) - (a.knife.round || 0);
    return (a.knife.sequence || 0) - (b.knife.sequence || 0);
  });

  for (let i = 0; i < overflowKnives.length; i += 1) {
    const item = overflowKnives[i];
    const member = context.memberMap.get(item.memberKey);
    if (!member) continue;
    const candidates = _collectRaidRoundOverflowTransferCandidates(
      context,
      member,
      selectedPlan,
      candidatePool,
      item.knife,
      overflowBossSet,
      coveredBosses
    );
    for (let j = 0; j < candidates.length; j += 1) {
      const candidate = candidates[j];
      const nextPlan = _cloneRaidPlanEntries(selectedPlan);
      const nextEntry = nextPlan.futureByMember.get(item.memberKey);
      if (!nextEntry) continue;
      const index = nextEntry.knives.findIndex(knife => (knife.sequence || 0) === (item.knife.sequence || 0));
      if (index < 0) continue;
      nextEntry.knives[index] = Object.assign({}, nextEntry.knives[index], {
        bossIndex: candidate.bossIndex,
        stage: 0,
        elementKey: candidate.elementKey,
        groupRank: candidate.groupRank,
        damageYi: candidate.damageYi,
        damageTrue: candidate.damageTrue,
        effectiveTrue: candidate.damageTrue,
      });

      const rerun = _simulateRaidRoundSelectedPlan(context, nextPlan);
      const nextOverflowTrue = _getRaidRoundPhase3OverflowTrue(context, rerun.plan);
      if (rerun.deficitBosses.length > currentDeficitCount) continue;
      if (rerun.deficitBosses.length === currentDeficitCount
        && rerun.deficitRemainingTrue > currentDeficitRemainingTrue) continue;
      if (nextOverflowTrue >= currentOverflowTrue) continue;

      options.push({
        memberKey: item.memberKey,
        memberSync: item.memberSync,
        memberName: item.memberName,
        transferBossIndex: candidate.bossIndex,
        nextPlan,
        simulation: rerun,
        overflowReducedTrue: currentOverflowTrue - nextOverflowTrue,
        groupRank: candidate.groupRank || 0,
      });
    }

    if (options.length) {
      options.sort(_compareRaidRoundOverflowOption);
      return [options[0]];
    }
  }

  return options;
}

function _collectRaidRoundOverflowTransferCandidates(context, member, selectedPlan, candidatePool, replaceKnife, overflowBossSet, coveredBosses) {
  const strictCandidates = _collectRaidRoundOverflowTransferCandidatesInternal(
    context,
    member,
    selectedPlan,
    candidatePool,
    replaceKnife,
    overflowBossSet,
    coveredBosses,
    false
  );
  if (strictCandidates.length) return strictCandidates;
  return _collectRaidRoundOverflowTransferCandidatesInternal(
    context,
    member,
    selectedPlan,
    candidatePool,
    replaceKnife,
    overflowBossSet,
    coveredBosses,
    true
  );
}

function _collectRaidRoundOverflowTransferCandidatesInternal(context, member, selectedPlan, candidatePool, replaceKnife, overflowBossSet, coveredBosses, allowElementConflict) {
  const entry = selectedPlan.futureByMember.get(member.key);
  if (!entry) return [];
  const plannedKnives = entry.knives.filter(knife => (knife.sequence || 0) !== (replaceKnife.sequence || 0));
  const groups = _getRaidMemberPlanElementGroups(member, candidatePool, context.bossElements);
  const candidates = [];
  for (let groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
    const groupCandidates = _collectRaidMemberPlanBossCandidates(member, context, plannedKnives, groups[groupIndex], {
      allowElementConflict: !!allowElementConflict,
    }).filter(candidate => {
      return candidate.bossIndex !== replaceKnife.bossIndex
        && !overflowBossSet.has(candidate.bossIndex)
        && !coveredBosses.has(candidate.bossIndex);
    });
    for (let i = 0; i < groupCandidates.length; i += 1) {
      candidates.push(Object.assign({ groupRank: groupIndex }, groupCandidates[i]));
    }
  }
  return candidates;
}

function _compareRaidRoundOverflowOption(a, b) {
  if (a.memberSync !== b.memberSync) return a.memberSync - b.memberSync;
  const nameCmp = a.memberName.localeCompare(b.memberName);
  if (nameCmp !== 0) return nameCmp;
  if (a.overflowReducedTrue !== b.overflowReducedTrue) {
    return b.overflowReducedTrue - a.overflowReducedTrue;
  }
  return (a.groupRank || 0) - (b.groupRank || 0);
}

function _getRaidRoundPhase3OverflowBosses(context, phasePlan) {
  const phase3Cap = context.bossStates[RAID_PHASE_COUNT - 1]
    ? context.bossStates[RAID_PHASE_COUNT - 1].bosses
    : [];
  const totals = Array(RAID_BOSS_COUNT).fill(0);
  if (phasePlan && phasePlan.futureByMember) {
    phasePlan.futureByMember.forEach(entry => {
      (entry.knives || []).forEach(knife => {
        if ((knife.stage || 0) !== RAID_PHASE_COUNT) return;
        totals[knife.bossIndex] += knife.effectiveTrue || 0;
      });
    });
  }

  const overflows = [];
  for (let bossIndex = 0; bossIndex < RAID_BOSS_COUNT; bossIndex += 1) {
    const overflowTrue = Math.max(0, (totals[bossIndex] || 0) - (phase3Cap[bossIndex] || 0));
    if (overflowTrue <= 0) continue;
    overflows.push({
      phase: RAID_PHASE_COUNT,
      bossIndex,
      overflowTrue,
      totalDamageTrue: totals[bossIndex] || 0,
      capTrue: phase3Cap[bossIndex] || 0,
    });
  }
  overflows.sort((a, b) => {
    if (b.overflowTrue !== a.overflowTrue) return b.overflowTrue - a.overflowTrue;
    return a.bossIndex - b.bossIndex;
  });
  return overflows;
}

function _getRaidRoundPhase3OverflowTrue(context, phasePlan) {
  return _getRaidRoundPhase3OverflowBosses(context, phasePlan)
    .reduce((sum, item) => sum + (item.overflowTrue || 0), 0);
}

function _buildRaidRoundReassignOptions(context, selectedPlan, candidatePool, simulation) {
  const options = [];
  const membersBySyncAsc = _getRaidMembersBySyncAsc(context.members);
  for (let i = 0; i < membersBySyncAsc.length; i += 1) {
    const member = membersBySyncAsc[i];
    const entry = selectedPlan.futureByMember.get(member.key);
    if (!entry || !entry.knives.length) continue;
    const memberOptions = _buildRaidRoundMemberReassignOptions(context, member, selectedPlan, candidatePool, simulation);
    if (!memberOptions.length) continue;
    memberOptions.sort(_compareRaidRoundReassignOption);
    return [memberOptions[0]];
  }
  return options;
}

function _buildRaidRoundMemberReassignOptions(context, member, selectedPlan, candidatePool, simulation) {
  const options = [];
  const entry = selectedPlan.futureByMember.get(member.key);
  if (!entry) return options;
  const currentOverflowTrue = _getRaidRoundPhase3OverflowTrue(context, simulation.plan);
  const deficitBosses = new Set((simulation.deficitBosses || []).map(item => item.bossIndex));
  const coveredBosses = _getRaidRoundFullyCoveredBossesFromBossStates(simulation.bossStates);
  const replaceableKnives = entry.knives
    .filter(knife => knife.kind !== "fixed")
    .slice()
    .sort((a, b) => {
      if ((b.round || 0) !== (a.round || 0)) return (b.round || 0) - (a.round || 0);
      return (b.sequence || 0) - (a.sequence || 0);
    });

  for (let i = 0; i < replaceableKnives.length; i += 1) {
    const replaceKnife = replaceableKnives[i];
    const candidates = _collectRaidRoundReassignCandidates(
      context,
      member,
      selectedPlan,
      candidatePool,
      replaceKnife,
      deficitBosses,
      coveredBosses
    );
    for (let j = 0; j < candidates.length; j += 1) {
      const candidate = candidates[j];
      const nextPlan = _cloneRaidPlanEntries(selectedPlan);
      const nextEntry = nextPlan.futureByMember.get(member.key);
      if (!nextEntry) continue;
      const index = nextEntry.knives.findIndex(knife => (knife.sequence || 0) === (replaceKnife.sequence || 0));
      if (index < 0) continue;
      nextEntry.knives[index] = Object.assign({}, replaceKnife, {
        bossIndex: candidate.bossIndex,
        stage: 0,
        elementKey: candidate.elementKey,
        groupRank: candidate.groupRank,
        damageYi: candidate.damageYi,
        damageTrue: candidate.damageTrue,
        effectiveTrue: candidate.damageTrue,
      });
      const rerun = _simulateRaidRoundSelectedPlan(context, nextPlan);
      const rerunOverflowTrue = _getRaidRoundPhase3OverflowTrue(context, rerun.plan);
      if (rerun.deficitBosses.length > simulation.deficitBosses.length) continue;
      if (rerun.deficitBosses.length === simulation.deficitBosses.length
        && rerun.deficitRemainingTrue >= simulation.deficitRemainingTrue) continue;
      if (rerunOverflowTrue > currentOverflowTrue) continue;
      options.push({
        memberKey: member.key,
        replacedRound: replaceKnife.round || 0,
        nextPlan,
        simulation: rerun,
        deficitCount: rerun.deficitBosses.length,
        deficitRemainingTrue: rerun.deficitRemainingTrue,
        preferredDeficit: candidate.preferredDeficit,
        groupRank: candidate.groupRank || 0,
      });
    }
  }

  return options;
}

function _collectRaidRoundReassignCandidates(context, member, selectedPlan, candidatePool, replaceKnife, deficitBosses, coveredBosses) {
  const strictCandidates = _collectRaidRoundReassignCandidatesInternal(
    context,
    member,
    selectedPlan,
    candidatePool,
    replaceKnife,
    deficitBosses,
    coveredBosses,
    false
  );
  if (strictCandidates.length) return strictCandidates;
  return _collectRaidRoundReassignCandidatesInternal(
    context,
    member,
    selectedPlan,
    candidatePool,
    replaceKnife,
    deficitBosses,
    coveredBosses,
    true
  );
}

function _collectRaidRoundReassignCandidatesInternal(context, member, selectedPlan, candidatePool, replaceKnife, deficitBosses, coveredBosses, allowElementConflict) {
  const entry = selectedPlan.futureByMember.get(member.key);
  const plannedKnives = entry ? entry.knives.filter(knife => (knife.sequence || 0) !== (replaceKnife.sequence || 0)) : [];
  const groups = _getRaidMemberPlanElementGroups(member, candidatePool, context.bossElements);
  const candidates = [];
  for (let groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
    const groupCandidates = _collectRaidMemberPlanBossCandidates(member, context, plannedKnives, groups[groupIndex], {
      allowElementConflict: !!allowElementConflict,
    }).filter(item => {
      return item.bossIndex !== replaceKnife.bossIndex && !coveredBosses.has(item.bossIndex);
    });
    for (let i = 0; i < groupCandidates.length; i += 1) {
      const candidate = groupCandidates[i];
      candidates.push(Object.assign({
        groupRank: groupIndex,
        preferredDeficit: deficitBosses.has(candidate.bossIndex),
      }, candidate));
    }
  }
  return candidates;
}

function _compareRaidRoundReassignOption(a, b) {
  if (!!a.preferredDeficit !== !!b.preferredDeficit) {
    return !!a.preferredDeficit ? -1 : 1;
  }
  if ((a.groupRank || 0) !== (b.groupRank || 0)) {
    return (a.groupRank || 0) - (b.groupRank || 0);
  }
  if (a.deficitCount !== b.deficitCount) return a.deficitCount - b.deficitCount;
  if (a.deficitRemainingTrue !== b.deficitRemainingTrue) {
    return a.deficitRemainingTrue - b.deficitRemainingTrue;
  }
  if (a.replacedRound !== b.replacedRound) return b.replacedRound - a.replacedRound;
  return String(a.memberKey).localeCompare(String(b.memberKey));
}

function _removeRaidPlannedKnifeBySequence(plan, memberKey, sequence) {
  const entry = plan.futureByMember.get(memberKey);
  if (!entry) return;
  const index = entry.knives.findIndex(knife => (knife.sequence || 0) === sequence);
  if (index >= 0) entry.knives.splice(index, 1);
}

function _getRaidRoundPlanSignature(plan) {
  const parts = [];
  plan.futureByMember.forEach((entry, memberKey) => {
    const knives = entry.knives
      .map(knife => [knife.round || 0, knife.sequence || 0, knife.bossIndex, knife.groupRank || 0].join(":"))
      .sort()
      .join(",");
    parts.push(`${memberKey}=${knives}`);
  });
  parts.sort();
  return parts.join("|");
}

function _getRaidRoundDeficitRemainingTrue(bossStates) {
  return _getRaidFrontDeficitBosses(bossStates).reduce((sum, item) => sum + (item.remainingTrue || 0), 0);
}

function _isBetterRaidRoundSelectionKnife(candidate, current) {
  if (!current) return true;
  if (!!candidate.lockedStage !== !!current.lockedStage) {
    return !!candidate.lockedStage;
  }
  if ((candidate.kind || "") === "fixed" && (current.kind || "") === "fixed"
    && (candidate.slotIndex || 0) !== (current.slotIndex || 0)) {
    return (candidate.slotIndex || 0) < (current.slotIndex || 0);
  }
  if (!!candidate.preferredDeficit !== !!current.preferredDeficit) {
    return !!candidate.preferredDeficit;
  }
  if ((candidate.groupRank || 0) !== (current.groupRank || 0)) {
    return (candidate.groupRank || 0) < (current.groupRank || 0);
  }
  if ((candidate.damageTrue || 0) !== (current.damageTrue || 0)) {
    return (candidate.damageTrue || 0) > (current.damageTrue || 0);
  }
  return (candidate.bossIndex || 0) < (current.bossIndex || 0);
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


function normalizeLabel(label) {
  return String(label || "")
    .trim()
    .replace(/\s+/g, "")
    .replace(/：/g, ":");
}
