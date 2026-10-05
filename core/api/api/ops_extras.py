# file: ops_extras.py
# description: 运维面扩展端点（缓存/路由器/探活/版本）+ 上游探活循环 —— main.py 拆分件 1/3
# author: YanYuCloudCube Team <admin@0379.email>
# version: v1.0.0
# created: 2026-10-05
# status: active
# tags: [api],[ops],[cache],[probe]
# 拆分说明：自 main.py 原样迁出（2026-10-05 P2 专项·巨石拆分，行为零变更）；
#   探活循环与其启停（含 VK 记账管道启停）一并迁移，main lifespan 经本模块调用。

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 缓存管理 ────────────────────────────────────────────────

@router.get("/v1/cache/stats")
async def get_cache_stats():
    """获取缓存统计（命中率、操作计数）"""
    from app.utils import cache_manager

    return cache_manager.get_stats()


@router.get("/v1/cache/info")
async def get_cache_info():
    """获取缓存详细信息（条目数、LRU淘汰状态、TTL配置）"""
    from app.cache import get_cache_info

    return await get_cache_info()


@router.post("/v1/cache/invalidate/{model_name}")
async def invalidate_cache(model_name: str):
    """按模型名主动失效缓存"""
    from app.cache import invalidate_model_cache

    count = await invalidate_model_cache(model_name)
    return {"model": model_name, "invalidated": count}


@router.delete("/v1/cache/all")
async def clear_cache():
    """清空所有LLM缓存"""
    from app.cache import clear_all_cache

    count = await clear_all_cache()
    return {"cleared": count}


# ── 路由器观测 ──────────────────────────────────────────────

@router.get("/v1/router/stats")
async def get_router_stats():
    """获取路由统计（上游池快照 + 节点 EWMA 动态权重）"""
    from app.services.model_router import model_router
    from app.services.upstream_registry import registry as upstream_registry

    return {
        "upstream_pool": upstream_registry.snapshot(),
        "nodes": model_router.get_node_stats(),
    }


@router.get("/v1/router/health")
async def get_router_health():
    """触发一次路由器健康检查并返回结果"""
    from app.services.model_router import model_router

    await model_router.health_check()
    return model_router.get_node_stats()


@router.get("/v1/versions")
async def get_api_versions():
    """获取所有API版本状态（current/deprecated/sunset）"""
    from app.middleware.versioning import VersioningMiddleware

    return VersioningMiddleware.get_version_info()


# ── P0-2: 上游池主动验活（学 one-api 渠道自愈）────────────────

_probe_task: Optional[asyncio.Task] = None


async def _probe_loop():
    """周期探活：probe_interval_seconds 一轮；连续 2 败 degraded 权重减半，恢复复原"""
    from app.services.upstream_registry import registry as upstream_registry

    while True:
        await asyncio.sleep(settings.probe_interval_seconds)
        try:
            results = await upstream_registry.probe_all()
            degraded = [r["name"] for r in results if r["probe_degraded"]]
            if degraded:
                logger.warning(f"验活降级上游: {', '.join(degraded)}")
        except Exception as e:
            logger.warning(f"验活轮次异常（下轮继续）: {e}")


@router.post("/v1/admin/upstreams/probe")
async def probe_upstreams():
    """手动触发一轮上游验活（受全局 Auth 中间件保护），返回各上游健康明细"""
    from app.services.upstream_registry import registry as upstream_registry

    return {"probes": await upstream_registry.probe_all()}


async def start_probe_loop():
    global _probe_task
    if settings.probe_enabled and settings.router_enabled:
        _probe_task = asyncio.create_task(_probe_loop())
        logger.info(
            f"上游验活已启动（间隔 {settings.probe_interval_seconds}s，PROBE_ENABLED=false 可关闭）"
        )
    # ── P0-1: 虚拟密钥记账管道（批量落库 spend_logs）──
    from app.services.virtual_key_manager import vk_manager

    await vk_manager.ensure_tables()  # sqlite 本地模式自建表（PG 跳过，由 003 SQL 迁移管）
    await vk_manager.start_ledger()


async def stop_probe_loop():
    global _probe_task
    if _probe_task is not None:
        _probe_task.cancel()
        _probe_task = None
    from app.services.virtual_key_manager import vk_manager

    await vk_manager.stop_ledger()
