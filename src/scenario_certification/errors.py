"""场景核证后端的领域错误。"""


class CertificationError(Exception):
    """领域错误基类。"""


class NotFoundError(CertificationError):
    """引用的实体不存在。"""


class ConflictError(CertificationError):
    """标识冲突或试图覆盖不可变记录。"""


class StateError(CertificationError):
    """当前状态不允许该操作（如批次已冻结、结论已撤销）。"""


class CoverageError(CertificationError):
    """签发能力声明时覆盖证据不足。"""
