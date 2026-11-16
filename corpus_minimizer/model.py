"""核心数据模型。"""

from dataclasses import dataclass, field
from typing import FrozenSet, Optional


class DropReason:
    """样本被剔除的原因分类。"""

    OVERSIZED = "oversized"
    EXECUTION_FAILED = "execution_failed"
    REDUNDANT = "redundant"


@dataclass(frozen=True)
class CorpusEntry:
    """单个语料样本的执行画像。

    Attributes:
        sample_id: 样本的稳定唯一标识（通常是文件名或相对路径）。
        coverage: 该样本命中的覆盖元素集合；None 表示覆盖集合缺失
            （执行失败或采集器未上报），按空覆盖参与选择。
        duration: 执行耗时，单位秒；用于在等价样本间优先保留更便宜的样本。
        crash_signature: crash 签名；None 表示该样本没有触发 crash。
        content_hash: 样本内容哈希；哈希相同的样本视为重复内容，
            合并为一个物理代表。
        size: 样本字节大小，用于超大样本过滤。
        required: 必留样本，始终保留。
        execution_failed: 执行是否失败；失败且无覆盖的非必留样本会被剔除。
    """

    sample_id: str
    coverage: Optional[FrozenSet[str]] = None
    duration: float = 0.0
    crash_signature: Optional[str] = None
    content_hash: Optional[str] = None
    size: int = 0
    required: bool = False
    execution_failed: bool = False

    @property
    def effective_coverage(self) -> FrozenSet[str]:
        """缺失覆盖集合（None）按空集合处理。"""
        return self.coverage if self.coverage is not None else frozenset()


@dataclass(frozen=True)
class MinimizationResult:
    """一次缩减的结果。

    Attributes:
        kept: 保留下来的样本，按 sample_id 字典序排列。
        removed: 被剔除样本的 sample_id -> 剔除原因。
        merged_into: 重复样本 sample_id -> 其保留代表的 sample_id。
        target_coverage: 本次缩减实际保证的目标覆盖集合。
        preserved_signatures: 被保证保留的全部 crash 签名。
        unreachable_coverage: 语料无法满足的覆盖元素；缩减成功时为空。
    """

    kept: tuple = ()
    removed: dict = field(default_factory=dict)
    merged_into: dict = field(default_factory=dict)
    target_coverage: frozenset = frozenset()
    preserved_signatures: frozenset = frozenset()
    unreachable_coverage: frozenset = frozenset()

    @property
    def kept_ids(self):
        return tuple(entry.sample_id for entry in self.kept)

    @property
    def removed_count(self):
        return len(self.removed)

    @property
    def duplicate_count(self):
        return len(self.merged_into)

    @property
    def original_size(self):
        return len(self.kept) + len(self.removed)
