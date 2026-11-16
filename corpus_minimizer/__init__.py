"""Fuzz 语料最小化库。

在保留以下三个硬约束的前提下缩减语料：

1. 每个 crash 签名至少保留一个同签名样本；
2. 完整覆盖给定的目标覆盖集合；
3. 所有必留样本（required）无条件保留。

选择过程完全确定性：所有并列裁决均按样本 id 等可复现的键排序，
不依赖目录遍历顺序或集合迭代顺序。
"""

from .exceptions import (
    CoverageUnreachableError,
    CorpusMinimizerError,
    InvalidCorpusError,
    PreservationError,
)
from .manifest import hash_file, load_corpus_directory, load_manifest
from .minimizer import minimize_corpus
from .model import CorpusEntry, DropReason, MinimizationResult

__all__ = [
    "CorpusEntry",
    "CorpusMinimizerError",
    "CoverageUnreachableError",
    "DropReason",
    "InvalidCorpusError",
    "MinimizationResult",
    "PreservationError",
    "hash_file",
    "load_corpus_directory",
    "load_manifest",
    "minimize_corpus",
]

__version__ = "1.0.0"
