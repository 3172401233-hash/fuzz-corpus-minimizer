"""从磁盘加载语料清单。

支持两种来源：

- JSON 清单文件：每条记录描述一个样本的覆盖、耗时、crash 签名等；
- 语料目录：按文件名排序后逐个分块计算内容哈希，哈希计算只读不全部
  载入内存，因此可以安全处理超大样本。

两种加载都按确定性顺序产出样本，避免依赖目录遍历顺序。
"""

import hashlib
import json
import os
from typing import Dict, List, Optional

from .exceptions import InvalidCorpusError
from .model import CorpusEntry

_HASH_CHUNK_SIZE = 1024 * 1024


def hash_file(path: str, algorithm: str = "sha256") -> str:
    """分块计算文件内容哈希，避免把超大文件整体读入内存。"""
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: str) -> List[CorpusEntry]:
    """加载 JSON 清单。

    清单可以是样本对象数组，也可以是 ``{"entries": [...]}`` 包装对象。
    支持的字段与 :class:`CorpusEntry` 一致，缺省字段使用默认值。
    """
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    if isinstance(data, dict) and "entries" in data:
        records = data["entries"]
    else:
        records = data

    if not isinstance(records, list):
        raise InvalidCorpusError("清单必须是样本对象数组")

    entries = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InvalidCorpusError(f"清单第 {index} 条记录不是对象")
        if "sample_id" not in record:
            raise InvalidCorpusError(f"清单第 {index} 条记录缺少 sample_id")

        coverage = record.get("coverage")
        if coverage is not None:
            coverage = frozenset(coverage)

        entries.append(
            CorpusEntry(
                sample_id=str(record["sample_id"]),
                coverage=coverage,
                duration=float(record.get("duration", 0.0)),
                crash_signature=record.get("crash_signature"),
                content_hash=record.get("content_hash"),
                size=int(record.get("size", 0)),
                required=bool(record.get("required", False)),
                execution_failed=bool(record.get("execution_failed", False)),
            )
        )

    return entries


def load_corpus_directory(
    directory: str,
    metadata: Optional[Dict[str, dict]] = None,
) -> List[CorpusEntry]:
    """扫描语料目录，按内容哈希构建样本。

    Args:
        directory: 语料目录路径。
        metadata: 可选的 ``文件名 -> 画像信息`` 映射，用于补充覆盖集合、
            耗时、crash 签名、必留标志等；没有画像信息的样本以空覆盖
            （覆盖集合缺失）参与缩减。

    Returns:
        按 sample_id（相对目录的文件名）字典序排列的样本列表。
    """
    metadata = metadata or {}
    names = sorted(
        name
        for name in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, name))
    )

    entries = []
    for name in names:
        full_path = os.path.join(directory, name)
        info = metadata.get(name, {})
        coverage = info.get("coverage")
        if coverage is not None:
            coverage = frozenset(coverage)

        entries.append(
            CorpusEntry(
                sample_id=name,
                coverage=coverage,
                duration=float(info.get("duration", 0.0)),
                crash_signature=info.get("crash_signature"),
                content_hash=hash_file(full_path),
                size=os.path.getsize(full_path),
                required=bool(info.get("required", False)),
                execution_failed=bool(info.get("execution_failed", False)),
            )
        )

    return entries
