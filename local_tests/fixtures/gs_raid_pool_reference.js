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

function _chooseRaidMemberPlanAutoKnife(member, context, plannedKnives, candidatePool) {
  const strictCandidate = _findRaidMemberPlanAutoCandidate(member, context, plannedKnives, candidatePool, false);
  if (strictCandidate) return strictCandidate;
  return _findRaidMemberPlanAutoCandidate(member, context, plannedKnives, candidatePool, true);
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

function _estimateRaidKnifeDamageYi(sync, baseSync, baseDamageYi) {
  if (!Number.isFinite(sync) || sync <= 0) return 0;
  if (!Number.isFinite(baseSync) || baseSync <= 0) return 0;
  if (!Number.isFinite(baseDamageYi) || baseDamageYi <= 0) return 0;
  const ratio = Math.pow(sync, 3) / Math.pow(baseSync, 3);
  return Math.round(ratio * baseDamageYi);
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

function _getRaidAllKnives(member) {
  return member.historyKnives.concat(member.fixedKnives, member.autoKnives);
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

function _getRaidElementRank(member, elementKey) {
  const ranked = member.rankedElements || [];
  const index = ranked.indexOf(elementKey);
  return index >= 0 ? index : ranked.length;
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

function _getRaidMembersBySyncDesc(members) {
  return members.slice().sort((a, b) => {
    if (b.sync !== a.sync) return b.sync - a.sync;
    return a.name.localeCompare(b.name);
  });
}

function _getRaidPendingFixedKnives(member) {
  return member.fixedKnives.filter(knife => !knife.assigned);
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

function _memberBlocksRaidBoss(member, bossIndex) {
  return !!(member && (member.reverseInputs || []).some(input => input.bossIndex === bossIndex));
}

function _memberHasRaidBoss(member, bossIndex, extraKnives) {
  const knives = _getRaidAllKnives(member).concat(extraKnives || []);
  for (let i = 0; i < knives.length; i += 1) {
    if (knives[i].bossIndex === bossIndex) return true;
  }
  return false;
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

function normalizeLabel(label) {
  return String(label || "")
    .trim()
    .replace(/\s+/g, "")
    .replace(/：/g, ":");
}
