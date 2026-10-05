# file: models_meta.py
# description: 模型元数据端点（列表/统计/错误/摘要/后端类型）—— main.py 拆分件 2/3
# author: YanYuCloudCube Team <admin@0379.email>
# version: v1.0.0
# created: 2026-10-05
# status: active
# tags: [api],[models],[metadata]
# 拆分说明：自 main.py 原样迁出（2026-10-05 P2 专项，行为零变更）；
#   DEFAULT_MODELS 配置族随迁（DB 动态注册模型自动附加逻辑不变）。

import logging
from typing import List

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.db import ModelRegistry, async_session
from app.models import ErrorRecord, ModelConfig, ModelStat, UsageSummary

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/v1/model/type")
async def get_model_type(model: str = Query(...)):
    """
    获取模型后端类型（用于 Traefik/HAProxy 的 GPU 感知路由）

    返回:
    - local_cpu: Ollama CPU 推理
    - local_gpu: Ollama GPU 推理（DGX Spark）
    - openai: OpenAI API
    - zhipu: 智谱 AI API
    - deepseek: DeepSeek API
    - unknown: 未注册模型
    """
    try:
        async with async_session() as session:
            from sqlalchemy import select

            from app.db import ModelRegistry

            result = await session.execute(
                select(ModelRegistry.backend_type, ModelRegistry.backend_name)
                .where(ModelRegistry.id == model)
                .where(ModelRegistry.enabled.is_(True))
            )
            row = result.first()
            if row:
                return {
                    "model": model,
                    "backend_type": row.backend_type,
                    "backend_name": row.backend_name,
                }
    except Exception:
        pass

    # 回退：根据模型名前缀推断
    if any(model.startswith(p) for p in ["glm-4", "zhipu:"]):
        return {"model": model, "backend_type": "zhipu", "backend_name": model}
    if any(model.startswith(p) for p in ["gpt-", "openai:"]):
        return {"model": model, "backend_type": "openai", "backend_name": model}
    if any(model.startswith(p) for p in ["deepseek-", "deepseek:"]):
        return {"model": model, "backend_type": "deepseek", "backend_name": model}
    if any(model.startswith(p) for p in ["llama", "codegeex", "qwen", "local:", "ollama:"]):
        return {"model": model, "backend_type": "local_cpu", "backend_name": model}

    return JSONResponse(status_code=404, content={"error": "Model not found", "model": model})


# ── 默认模型配置 ──────────────────────────────────────────
# 可通过数据库 model_registry 表动态扩展（Ollama模型）
DEFAULT_MODELS = [
    # 智谱GLM
    ModelConfig(
        id="glm-4-flash",
        display_name="智谱GLM-4 Flash",
        backend="zhipu",
        enabled=True,
        max_tokens=128000,
        temperature=0.7,
        top_p=0.9,
        cost_per_1k_tokens=0.001,
    ),
    ModelConfig(
        id="glm-4-plus",
        display_name="智谱GLM-4 Plus",
        backend="zhipu",
        enabled=True,
        max_tokens=128000,
        temperature=0.7,
        top_p=0.9,
        cost_per_1k_tokens=0.05,
    ),
    ModelConfig(
        id="deepseek-chat",
        display_name="DeepSeek Chat",
        backend="deepseek",
        enabled=True,
        max_tokens=128000,
        temperature=0.7,
        top_p=0.9,
        cost_per_1k_tokens=0.001,
    ),
    ModelConfig(
        id="deepseek-coder",
        display_name="DeepSeek Coder",
        backend="deepseek",
        enabled=True,
        max_tokens=128000,
        temperature=0.7,
        top_p=0.9,
        cost_per_1k_tokens=0.001,
    ),
    # Ollama 本地
    ModelConfig(
        id="llama3.2",
        display_name="Llama 3.2 (本地)",
        backend="ollama",
        enabled=True,
        max_tokens=128000,
        temperature=0.7,
        top_p=0.9,
        cost_per_1k_tokens=0.0,
    ),
    ModelConfig(
        id="codegeex4",
        display_name="CodeGeeX4 (本地)",
        backend="ollama",
        enabled=True,
        max_tokens=128000,
        temperature=0.7,
        top_p=0.9,
        cost_per_1k_tokens=0.0,
    ),
    ModelConfig(
        id="qwen2.5",
        display_name="通义千问 2.5 (本地)",
        backend="ollama",
        enabled=True,
        max_tokens=128000,
        temperature=0.7,
        top_p=0.9,
        cost_per_1k_tokens=0.0,
    ),
]

# DB注册的默认Ollama模型ID（避免重复）
_DEFAULT_OLLAMA_IDS = {"llama3.2", "codegeex4", "qwen2.5"}


@router.get("/v1/models", response_model=List[ModelConfig])
async def list_models():
    """
    获取可用模型列表

    支持的Provider：
    - zhipu: 智谱GLM（glm-4-flash, glm-4-plus）
    - deepseek: DeepSeek（deepseek-chat, deepseek-coder）
    - ollama: 本地模型（llama3.2, codegeex4, qwen2.5等）
    数据库动态注册的模型自动附加
    """
    models = list(DEFAULT_MODELS)

    # 上游池模型（OPENAI_COMPATIBLE_UPSTREAMS 注入）
    from app.services.upstream_registry import registry as upstream_registry

    for u in upstream_registry.upstreams.values():
        if u.capability != "chat":
            continue
        for m in u.models:
            if "*" in m or "?" in m:
                m = m.replace("*", "").replace("?", "") or u.name
            models.append(
                ModelConfig(
                    id=m,
                    display_name=f"{m} @ {u.name}",
                    backend="upstream",
                    enabled=True,
                    max_tokens=128000,
                    temperature=0.7,
                    top_p=0.9,
                    cost_per_1k_tokens=0.0,
                )
            )

    # 从数据库加载动态注册的Ollama模型
    try:
        async with async_session() as session:
            result = await session.execute(
                select(ModelRegistry.id, ModelRegistry.display_name)
                .where(ModelRegistry.enabled.is_(True))
                .where(ModelRegistry.backend_type == "ollama")
            )
            for row in result:
                if row.id not in _DEFAULT_OLLAMA_IDS:
                    models.append(
                        ModelConfig(
                            id=row.id,
                            display_name=row.display_name,
                            backend="ollama",
                            enabled=True,
                            max_tokens=128000,
                            temperature=0.7,
                            top_p=0.9,
                            cost_per_1k_tokens=0.0,
                        )
                    )
    except Exception:
        pass

    return models


@router.get("/v1/models/stats", response_model=List[ModelStat])
async def get_stats():
    """获取所有模型统计信息（上游池为真实 EWMA 数据，云/Ollama 为 DB 用量）"""
    from app.services.upstream_registry import registry as upstream_registry

    stats: dict = {}
    # 上游池：真实延迟/错误率
    for u in upstream_registry.upstreams.values():
        stats[u.name] = ModelStat(
            model_id=u.name,
            usage_count=u.total_requests,
            avg_latency_ms=round(u.ewma_latency, 1),
            error_rate=round(u.ewma_error_rate, 4),
            total_tokens=0,
        )
    # DB 用量（DB 不可达时仅返回上游数据）
    try:
        async with async_session() as session:
            from sqlalchemy import func

            from app.db import UsageLog

            result = await session.execute(
                select(
                    UsageLog.model,
                    func.count(UsageLog.id).label("usage_count"),
                    func.sum(UsageLog.total_tokens).label("total_tokens"),
                ).group_by(UsageLog.model)
            )
            for row in result:
                if row.model in stats:
                    stats[row.model].usage_count += row.usage_count or 0
                    stats[row.model].total_tokens += row.total_tokens or 0
                else:
                    stats[row.model] = ModelStat(
                        model_id=row.model,
                        usage_count=row.usage_count or 0,
                        avg_latency_ms=0.0,
                        error_rate=0.0,
                        total_tokens=row.total_tokens or 0,
                    )
    except Exception as e:
        logger.warning(f"models/stats DB 查询失败（仅返回上游池数据）: {e}")
    return list(stats.values())


@router.get("/v1/models/errors", response_model=List[ErrorRecord])
async def get_errors():
    """获取上游池错误记录（真实数据）"""
    from datetime import datetime, timezone

    from app.services.upstream_registry import registry as upstream_registry

    out = []
    for i, e in enumerate(upstream_registry.errors()):
        out.append(
            ErrorRecord(
                id=f"upstream-{i}",
                timestamp=datetime.now(timezone.utc),
                model_id=e["upstream"],
                error_type="internal",
                message=e["error"] or "unknown",
                stack=None,
            )
        )
    return out


@router.get("/v1/models/summary", response_model=UsageSummary)
async def get_summary():
    """获取使用摘要"""
    async with async_session() as session:
        from sqlalchemy import func

        from app.db import UsageLog

        result = await session.execute(
            select(
                func.count(UsageLog.id).label("total_requests"),
                func.sum(UsageLog.total_tokens).label("total_tokens"),
            )
        )
        row = result.first()
        total_requests = row.total_requests if row and row.total_requests else 0
        total_tokens = row.total_tokens if row and row.total_tokens else 0
        return UsageSummary(
            total_requests=total_requests,
            total_tokens=total_tokens,
            cost_usd=0.0,
        )
