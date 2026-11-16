"""语料最小化器使用的异常类型。"""


class CorpusMinimizerError(Exception):
    """所有最小化器错误的基类。"""


class InvalidCorpusError(CorpusMinimizerError):
    """输入数据不合法（字段类型错误、id 重复等）。"""


class CoverageUnreachableError(CorpusMinimizerError):
    """语料中没有任何样本可以覆盖目标覆盖集合中的某些元素。"""

    def __init__(self, missing):
        self.missing = frozenset(missing)
        super().__init__(
            "目标覆盖集合无法被语料满足，缺失覆盖元素: "
            + ", ".join(sorted(self.missing))
        )


class PreservationError(CorpusMinimizerError):
    """保留约束无法满足（例如必留的 crash 签名只存在于超限样本中）。"""
