"""
@file: app/middleware/path_normalize.py
@description: 跨节点路径归一化中间件 — 将 Mac 风格路径（/Volumes/nas/...）归一为标准 /mnt/nas/...
@version: v1.0.1
@status: active
@align: G1-TC-G1-004 路径归一中间件
@redline: 全节点强制 /mnt/nas/ 标准路径，杜绝跨节点路径断点
@changelog: v1.0.1 is_nas_path 前缀边界修复（OBS-G1-004-1：/mnt/nasdir 误判）
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# NAS 标准挂载根（全节点唯一事实源）
NAS_STANDARD_ROOT = "/mnt/nas"

# Mac 端常见的 NAS 挂载别名（SMB / Finder 挂载点）
MAC_NAS_ALIASES = (
    "/Volumes/nas",
    "/Volumes/NAS",
    "/Volumes/YYC3-NAS",
    "/Volumes/yyc3-nas",
)

# 正则：捕获 Mac 别名后跟随的子路径，例如 /Volumes/nas/projects/x -> /mnt/nas/projects/x
_ALIAS_RE = re.compile(
    r"^(?P<alias>" + "|".join(re.escape(a) for a in MAC_NAS_ALIASES) + r")(?P<rest>/.*)?$"
)


def normalize_nas_path(raw_path: Optional[str]) -> str:
    """将任意节点风格的 NAS 路径归一为 /mnt/nas/... 标准路径。

    规则：
    - 空/None -> 返回 NAS_STANDARD_ROOT
    - 已以 /mnt/nas 开头 -> 原样返回（仅压缩多余斜杠）
    - 命中 Mac 别名前缀 -> 替换为 /mnt/nas 并拼接剩余子路径
    - 相对路径 -> 视为相对 NAS 根，拼接为 /mnt/nas/<relative>
    - 其他绝对路径 -> 若首段非 /mnt 也非已知别名，原样返回（不篡改非 NAS 路径），并记录 warn
    """
    if not raw_path:
        return NAS_STANDARD_ROOT

    # 压缩多余斜杠
    cleaned = re.sub(r"/+", "/", raw_path)

    if cleaned == "/":
        return NAS_STANDARD_ROOT

    # 已是标准路径
    if cleaned == NAS_STANDARD_ROOT or cleaned.startswith(NAS_STANDARD_ROOT + "/"):
        return cleaned.rstrip("/") or NAS_STANDARD_ROOT

    # Mac 别名匹配
    m = _ALIAS_RE.match(cleaned)
    if m:
        rest = m.group("rest") or ""
        normalized = NAS_STANDARD_ROOT + rest
        logger.info(f"[path_normalize] {raw_path!r} -> {normalized!r}")
        return normalized.rstrip("/") or NAS_STANDARD_ROOT

    # 相对路径 -> 相对 NAS 根
    if not cleaned.startswith("/"):
        normalized = f"{NAS_STANDARD_ROOT}/{cleaned.lstrip('/')}"
        logger.info(f"[path_normalize] relative {raw_path!r} -> {normalized!r}")
        return normalized.rstrip("/") or NAS_STANDARD_ROOT

    # 其他绝对路径：不属 NAS，原样返回（不篡改业务路径）
    logger.warning(f"[path_normalize] 非 NAS 路径，原样保留：{raw_path!r}")
    return cleaned


def is_nas_path(path: Optional[str]) -> bool:
    """判定是否为 NAS 标准路径（归一后为 /mnt/nas 本身或其子路径）"""
    if not path:
        return False
    normalized = normalize_nas_path(path)
    # 边界对齐 normalize_nas_path 口径：根本身算 NAS 路径；
    # /mnt/nasdir 等共享前缀但非子路径的目录不算（OBS-G1-004-1 修复）
    return normalized == NAS_STANDARD_ROOT or normalized.startswith(NAS_STANDARD_ROOT + "/")


__all__ = ["normalize_nas_path", "is_nas_path", "NAS_STANDARD_ROOT", "MAC_NAS_ALIASES"]
