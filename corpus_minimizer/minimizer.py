"""语料缩减核心算法。

处理流水线（每一步都是确定性的，不依赖输入顺序）：

1. 归一化与校验：把输入统一成 :class:`CorpusEntry`，覆盖缺失按空集合处理。
2. 内容去重：``content_hash`` 相同的样本合并为一个物理代表，
   覆盖取并集、必留/crash 等标志聚合；代表固定取 sample_id 最小者，
   保证同一批文件无论输入顺序如何，合并映射都完全一致。
3. 超大样本过滤：超过 ``max_sample_size`` 的非必留样本直接剔除；
   必留样本豁免大小限制。
4. crash 保留：每个 crash 签名在可用样本中确定性挑选一个代表保留。
5. 覆盖保留：对剩余未覆盖的目标元素做确定性贪心集合覆盖，
   并列时依次按新增覆盖数、小体积、低耗时、sample_id 裁决。
6. 其余样本按执行失败 / 冗余分类剔除。
"""

from dataclasses import replace
from typing import Iterable, Optional, Sequence, Tuple

from .exceptions import (
    CoverageUnreachableError,
    InvalidCorpusError,
    PreservationError,
)
from .model import CorpusEntry, DropReason, MinimizationResult


def minimize_corpus(
    entries: Sequence[CorpusEntry],
    target_coverage: Optional[Iterable[str]] = None,
    max_sample_size: Optional[int] = None,
) -> MinimizationResult:
    """缩减语料。

    Args:
        entries: 原始语料样本列表，可以为空。
        target_coverage: 必须完整覆盖的目标覆盖集合。``None`` 表示
            保留语料中出现过的全部覆盖元素（并集）。
        max_sample_size: 单个样本允许的最大字节数；``None`` 表示不限制。

    Returns:
        :class:`MinimizationResult`。

    Raises:
        InvalidCorpusError: 输入字段非法或 sample_id 重复。
        CoverageUnreachableError: 目标覆盖集合存在语料无法满足的元素。
        PreservationError: crash 保留约束在大小限制下无法满足。
    """
    normalized = _normalize_entries(entries)

    if not normalized:
        wanted = frozenset(target_coverage) if target_coverage is not None else frozenset()
        if wanted:
            raise CoverageUnreachableError(wanted)
        return MinimizationResult(target_coverage=frozenset())

    if max_sample_size is not None and max_sample_size < 0:
        raise InvalidCorpusError("max_sample_size 不能为负数")

    removed = {}
    representatives, group_members = _deduplicate(normalized)

    available = _filter_oversized(representatives, removed, max_sample_size)

    available_ids = {item.sample_id for item in available}
    merged_into = {}
    for leader_id, members in group_members.items():
        if leader_id not in available_ids:
            for member in members[1:]:
                removed[member.sample_id] = DropReason.OVERSIZED
        else:
            for member in members[1:]:
                merged_into[member.sample_id] = leader_id

    all_signatures = frozenset(
        entry.crash_signature
        for entry in normalized
        if entry.crash_signature is not None
    )

    if target_coverage is None:
        wanted = frozenset(
            element
            for entry in available
            for element in entry.effective_coverage
        )
    else:
        wanted = frozenset(target_coverage)

    selected = {}
    covered = set()

    _preserve_required(available, selected, covered)
    _preserve_crashes(available, selected, covered, all_signatures)

    missing = wanted - covered
    if missing:
        _greedy_cover(available, selected, covered, missing)

    still_missing = wanted - covered
    if still_missing:
        raise CoverageUnreachableError(still_missing)

    selected_ids = set(selected)
    for entry in available:
        if entry.sample_id not in selected_ids:
            if entry.execution_failed and not entry.effective_coverage:
                removed[entry.sample_id] = DropReason.EXECUTION_FAILED
            else:
                removed[entry.sample_id] = DropReason.REDUNDANT

    kept = tuple(sorted(selected.values(), key=lambda item: item.sample_id))
    return MinimizationResult(
        kept=kept,
        removed=removed,
        merged_into=merged_into,
        target_coverage=wanted,
        preserved_signatures=all_signatures,
    )


def _normalize_entries(entries) -> Tuple[CorpusEntry, ...]:
    if entries is None:
        raise InvalidCorpusError("entries 不能为 None")

    normalized = []
    seen_ids = set()
    for index, raw in enumerate(entries):
        if not isinstance(raw, CorpusEntry):
            raise InvalidCorpusError(
                f"第 {index} 个元素不是 CorpusEntry: {type(raw).__name__}"
            )
        if not raw.sample_id or not isinstance(raw.sample_id, str):
            raise InvalidCorpusError(f"第 {index} 个样本缺少合法的 sample_id")
        if raw.sample_id in seen_ids:
            raise InvalidCorpusError(f"sample_id 重复: {raw.sample_id}")
        seen_ids.add(raw.sample_id)

        if raw.coverage is not None:
            coverage = frozenset(raw.coverage)
        else:
            coverage = None

        duration = raw.duration
        if duration < 0:
            raise InvalidCorpusError(
                f"样本 {raw.sample_id} 的 duration 不能为负数"
            )
        if raw.size < 0:
            raise InvalidCorpusError(f"样本 {raw.sample_id} 的 size 不能为负数")

        normalized.append(replace(raw, coverage=coverage, duration=float(duration)))

    return tuple(normalized)


def _deduplicate(entries):
    """按 content_hash 合并重复内容。

    无 content_hash 的样本各自成组，不参与去重。

    Returns:
        (representatives, group_members)：去重后的代表元组，以及
        ``代表 sample_id -> 同组全部成员（按 id 排序）`` 的映射。
    """
    groups = {}
    solo = []
    for entry in entries:
        if entry.content_hash is None:
            solo.append(entry)
        else:
            groups.setdefault(entry.content_hash, []).append(entry)

    representatives = list(solo)
    group_members = {}
    for members in groups.values():
        ordered = sorted(members, key=lambda item: item.sample_id)
        group_members[ordered[0].sample_id] = ordered

        merged_coverage = frozenset(
            element
            for member in members
            for element in member.effective_coverage
        )
        signatures = sorted(
            {
                member.crash_signature
                for member in members
                if member.crash_signature is not None
            }
        )
        merged = replace(
            ordered[0],
            coverage=merged_coverage,
            required=any(member.required for member in members),
            execution_failed=all(member.execution_failed for member in members),
            crash_signature=signatures[0] if signatures else None,
        )
        representatives.append(merged)

    representatives = tuple(
        sorted(representatives, key=lambda item: item.sample_id)
    )
    return representatives, group_members


def _filter_oversized(representatives, removed, max_sample_size):
    if max_sample_size is None:
        return representatives

    available = []
    for entry in representatives:
        if entry.size > max_sample_size and not entry.required:
            removed[entry.sample_id] = DropReason.OVERSIZED
        else:
            available.append(entry)
    return tuple(available)


def _preserve_required(available, selected, covered):
    for entry in available:
        if entry.required:
            selected[entry.sample_id] = entry
            covered.update(entry.effective_coverage)


def _preserve_crashes(available, selected, covered, all_signatures):
    owners = {}
    for entry in available:
        signature = entry.crash_signature
        if signature is not None:
            owners.setdefault(signature, []).append(entry)

    owners = {
        signature: sorted(candidates, key=lambda item: item.sample_id)
        for signature, candidates in owners.items()
    }

    for signature in sorted(all_signatures):
        candidates = owners.get(signature)
        if not candidates:
            raise PreservationError(
                f"crash 签名 {signature!r} 的全部样本均不可保留"
                "（可能因超大样本过滤被剔除）"
            )

        already = next(
            (entry for entry in candidates if entry.sample_id in selected),
            None,
        )
        if already is not None:
            continue

        choice = min(
            candidates,
            key=lambda item: (item.size, item.duration, item.sample_id),
        )
        selected[choice.sample_id] = choice
        covered.update(choice.effective_coverage)


def _greedy_cover(available, selected, covered, missing):
    wanted = set(missing)
    while wanted - covered:
        best = None
        best_key = None
        for entry in available:
            if entry.sample_id in selected:
                continue
            gain = len(wanted.intersection(entry.effective_coverage) - covered)
            if gain == 0:
                continue
            key = (-gain, entry.size, entry.duration, entry.sample_id)
            if best_key is None or key < best_key:
                best_key = key
                best = entry

        if best is None:
            return

        selected[best.sample_id] = best
        covered.update(best.effective_coverage)
