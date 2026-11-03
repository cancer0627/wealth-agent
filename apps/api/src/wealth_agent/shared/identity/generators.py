"""生成基于 UUID4（随机通用唯一标识）的不透明标识，可附加调用方前缀。"""

from uuid import uuid4


def new_id(prefix: str | None = None) -> str:
    value = uuid4().hex
    return f"{prefix}_{value}" if prefix else value
