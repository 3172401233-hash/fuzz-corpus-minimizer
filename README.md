# fuzz-corpus-minimizer

Python Fuzz 语料最小化库。面对几万个大量重复的 fuzz 输入，在不丢掉独特
crash、关键边界覆盖和人工必留样本的前提下，把语料缩减到一个可复现的
最小保留集合，供回归测试使用。

## 覆盖模型

每个语料样本由 `CorpusEntry` 描述，包含以下输入信息：

| 字段 | 含义 |
| --- | --- |
| `sample_id` | 样本稳定唯一标识（通常是文件名），也是所有确定性裁决的最终决胜键 |
| `coverage` | 该样本命中的覆盖元素集合（如边/边界指纹）；`None` 表示覆盖集合缺失，按空集合处理 |
| `duration` | 执行耗时（秒），等价样本间优先保留执行更快的样本 |
| `crash_signature` | crash 签名；`None` 表示未触发 crash |
| `content_hash` | 样本内容哈希，哈希相同视为重复内容 |
| `size` | 样本字节数，配合 `max_sample_size` 过滤超大样本 |
| `required` | 必留样本，无条件保留并豁免大小限制 |
| `execution_failed` | 执行是否失败；失败且无覆盖的非必留样本会被剔除 |

缩减目标是一个覆盖元素集合 `target_coverage`：保留样本的覆盖并集必须完整
包含它。不显式指定时，目标默认可用语料覆盖的并集。

## 缩减目标（硬约束）

缩减结果必须同时满足：

1. **crash 保留**：语料中出现过的每个 crash 签名，至少保留一个同签名样本；
2. **覆盖完整**：保留样本的覆盖并集完整覆盖 `target_coverage`；
3. **必留保留**：所有 `required=True` 的样本无条件保留。

在满足约束的前提下最小化保留样本数，并优先保留体积小、执行快的样本。

## 确定性保证

选择过程不依赖目录遍历顺序或集合迭代顺序：

- 内容去重的物理代表固定取同哈希组内 `sample_id` 最小者，覆盖取组内并集；
- crash 代表按 `(size, duration, sample_id)` 排序选取；
- 贪心集合覆盖每轮按 `(新增覆盖数降序, size, duration, sample_id)` 选取；
- 所有输出（保留列表、剔除原因、合并映射）按键排序，同一输入任意打乱顺序
  都会得到完全一致的结果。

## 处理流水线

1. 归一化与校验：覆盖缺失按空集合处理，拒绝重复 id 和负值字段；
2. 内容去重：相同 `content_hash` 合并为一个代表，重复样本记录到
   `merged_into`；
3. 超大样本过滤：超过 `max_sample_size` 的非必留样本标记为 `oversized`
   剔除，必留样本豁免；若某 crash 签名的全部样本因此不可保留，抛出
   `PreservationError`；
4. 必留与 crash 保留：先固定必留样本，再为每个签名确定性挑选代表；
5. 贪心集合覆盖：补齐尚未满足的目标覆盖；无法满足时抛出
   `CoverageUnreachableError` 并给出缺失元素；
6. 其余样本按 `execution_failed`（失败且无覆盖）或 `redundant` 分类剔除。

空语料是合法输入：无目标覆盖时返回空结果；存在目标覆盖时抛出
`CoverageUnreachableError`。

## 用法

```python
from corpus_minimizer import CorpusEntry, minimize_corpus

entries = [
    CorpusEntry(sample_id="a", coverage=frozenset({"edge/1", "edge/2"}),
                duration=0.4, size=120, content_hash="h1"),
    CorpusEntry(sample_id="b", coverage=frozenset({"edge/2"}),
                duration=1.2, size=120, content_hash="h1"),
    CorpusEntry(sample_id="c", crash_signature="SIG-SEGV", size=8),
    CorpusEntry(sample_id="seed", required=True),
]

result = minimize_corpus(
    entries,
    target_coverage={"edge/1", "edge/2"},
    max_sample_size=4096,
)

result.kept_ids        # 保留样本 id（字典序）
result.removed         # 被剔除 id -> 原因（oversized/execution_failed/redundant）
result.merged_into     # 重复样本 id -> 物理代表 id
```

从磁盘加载同样是确定性的：`load_manifest` 读取 JSON 清单，
`load_corpus_directory` 按文件名排序扫描目录，并用 1 MiB 分块流式计算
SHA-256，避免把超大样本整体读入内存。

```python
from corpus_minimizer import load_corpus_directory, minimize_corpus

entries = load_corpus_directory("./corpus", metadata={
    "case-1": {"coverage": ["edge/1"], "crash_signature": "SIG-X"},
})
result = minimize_corpus(entries)
```

## 运行测试

```bash
python -m unittest
```

测试覆盖空语料、等价样本的确定性选择、互补覆盖的贪心集合覆盖、不同 crash
签名保留、必留文件、重复内容哈希合并、超大样本、执行失败、覆盖集合缺失，
以及清单/目录加载和输入乱序稳定性。
