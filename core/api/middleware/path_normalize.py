# file: path_normalize.py
# description: 跨节点路径归一化中间件 — JSON 请求体中 Mac 风格 NAS 路径归一为 /mnt/nas/ 标准
# author: YanYuCloudCube Team
# version: v1.0.0
# created: 2026-09-29
# updated: 2026-09-29
# status: active
# tags: [middleware],[path-normalize],[nas]

"""
@file: app/middleware/path_normalize.py
@description: 网关路径归一中间件（TC-G1-004 E2E 接线件）——请求体 JSON 中
    Mac 风格路径（/Volumes/nas/...）与相对路径归一为全节点标准 /mnt/nas/...，
    归一映射写入网关日志 + 响应头 X-YYC3-Path-Normalized 双留证。
    归一函数语义与 app/api/middleware/path_normalize.py v1.0.1 逐字对齐
    （含 OBS-G1-004-1 边界修复：/mnt/nasdir 不算 NAS 子路径）。
@author: YanYuCloudCube Team <admin@0379.email>
@version: v1.0.0
@created: 2026-09-29
@license: MIT
@tags: middleware,python,path-normalize,nas
"""

import json
import logging
import re
from typing import Any, List, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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

# 路径类字段判定：精确名或 _path/_dir 后缀（保守规则，非路径字段不触碰）
_PATH_KEY_EXACT = {"path", "dir", "out_dir"}
_PATH_KEY_SUFFIXES = ("_path", "_dir")


def normalize_nas_path(raw_path: Optional[str]) -> str:
    """将任意节点风格的 NAS 路径归一为 /mnt/nas/... 标准路径。

    规则（与 app/api/middleware/path_normalize.py v1.0.1 对齐）：
    - 空/None -> 返回 NAS_STANDARD_ROOT
    - 已以 /mnt/nas 开头 -> 原样返回（仅压缩多余斜杠）
    - 命中 Mac 别名前缀 -> 替换为 /mnt/nas 并拼接剩余子路径
    - 相对路径 -> 视为相对 NAS 根，拼接为 /mnt/nas/<relative>
    - 其他绝对路径 -> 原样返回（不篡改非 NAS 路径），并记录 warn
    """
    if not raw_path:
        return NAS_STANDARD_ROOT

    cleaned = re.sub(r"/+", "/", raw_path)

    if cleaned == "/":
        return NAS_STANDARD_ROOT

    if cleaned == NAS_STANDARD_ROOT or cleaned.startswith(NAS_STANDARD_ROOT + "/"):
        return cleaned.rstrip("/") or NAS_STANDARD_ROOT

    m = _ALIAS_RE.match(cleaned)
    if m:
        rest = m.group("rest") or ""
        normalized = NAS_STANDARD_ROOT + rest
        return normalized.rstrip("/") or NAS_STANDARD_ROOT

    if not cleaned.startswith("/"):
        normalized = f"{NAS_STANDARD_ROOT}/{cleaned.lstrip('/')}"
        return normalized.rstrip("/") or NAS_STANDARD_ROOT

    logger.warning(f"[path_normalize] 非 NAS 路径，原样保留：{raw_path!r}")
    return cleaned


def is_nas_path(path: Optional[str]) -> bool:
    """判定是否为 NAS 标准路径（根本身或其子路径；/mnt/nasdir 等不算）。"""
    if not path:
        return False
    normalized = normalize_nas_path(path)
    return normalized == NAS_STANDARD_ROOT or normalized.startswith(NAS_STANDARD_ROOT + "/")


def _is_path_key(key: str) -> bool:
    k = key.lower()
    return k in _PATH_KEY_EXACT or k.endswith(_PATH_KEY_SUFFIXES)


def normalize_json_paths(node: Any, path: str = "") -> Tuple[Any, List[Tuple[str, str, str]]]:
    """递归遍历 JSON 结构，归一路径类字符串字段。

    返回（新结构，[(字段定位, 旧值, 新值)]）；不可解析/非路径字段原样返回。
    """
    changes: List[Tuple[str, str, str]] = []
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            nv, ch = normalize_json_paths(v, f"{path}.{k}" if path else k)
            changes.extend(ch)
            if isinstance(v, str) and _is_path_key(k):
                nn = normalize_nas_path(v)
                if nn != v:
                    changes.append((f"{path}.{k}" if path else k, v, nn))
                    nv = nn
            out[k] = nv
        return out, changes
    if isinstance(node, list):
        out = []
        for i, v in enumerate(node):
            nv, ch = normalize_json_paths(v, f"{path}[{i}]")
            out.append(nv)
            changes.extend(ch)
        return out, changes
    return node, changes


class PathNormalizeMiddleware(BaseHTTPMiddleware):
    """JSON 请求体 NAS 路径归一中间件（TC-G1-004）。

    - 仅处理 POST/PUT/PATCH 且 Content-Type 为 JSON 的请求
    - 归一命中时：改写请求体（Starlette 1.7 wrapped_receive 重放 request._body，
      下游中间件链与端点拿到的即归一后请求体）+ 记录映射日志 +
      响应头 X-YYC3-Path-Normalized: <命中字段数>
    - 非 JSON / 解析失败 / 零命中：原样透传（零侵入）
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method not in ("POST", "PUT", "PATCH"):
            return await call_next(request)
        if "application/json" not in request.headers.get("content-type", ""):
            return await call_next(request)

        body = await request.body()
        try:
            data = json.loads(body)
        except Exception:
            return await call_next(request)

        normalized, changes = normalize_json_paths(data)
        if not changes:
            return await call_next(request)

        # 改写缓存请求体：wrapped_receive 状态 3 直接重放 self._body（见模块 docstring）
        request._body = json.dumps(  # noqa: SLF001 — Starlette 请求体改写的官方唯一通路
            normalized, ensure_ascii=False).encode("utf-8")
        for loc, old, new in changes:
            logger.info(f"[path_normalize] {loc}: {old!r} -> {new!r}")

        response = await call_next(request)
        response.headers["X-YYC3-Path-Normalized"] = str(len(changes))
        return response


__all__ = [
    "PathNormalizeMiddleware",
    "normalize_nas_path",
    "is_nas_path",
    "normalize_json_paths",
    "NAS_STANDARD_ROOT",
    "MAC_NAS_ALIASES",
]
