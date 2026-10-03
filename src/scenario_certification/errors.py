"""领域错误类型。"""


class DomainError(Exception):
    """场景核证领域错误基类。"""


class NotFoundError(DomainError):
    """引用的实体不存在。"""


class ConflictError(DomainError):
    """实体标识冲突或重复注册。"""


class StateError(DomainError):
    """当前状态不允许执行该操作。"""


class ValidationError(DomainError):
    """输入内容不满足领域约束。"""
