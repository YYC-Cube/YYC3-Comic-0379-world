# file: main.py
# description: FastAPI 应用入口文件
# author: YanYuCloudCube Team
# version: v1.0.0
# created: 2026-03-21
# updated: 2026-04-04
# status: active
# tags: [api],[main],[entry]

# @file main.py
# @description FastAPI 主应用 - 模型管理 API 端点
# @author YanYuCloudCube Team <admin@0379.email>
# @version v1.0.0
# @created 2026-03-14
# @updated 2026-03-14
# @status stable
# @license MIT
# @copyright Copyright (c) 2026 YanYuCloudCube Team
# @tags python,fastapi,api

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

import psutil

from app.api import (
    a2a,
    agent,
    chat,
    documents,
    knowledge_base,
    mcp,
    models_meta,
    ops_extras,
    proxy,
    rag,
    video_tasks,
    vk_admin,
    websocket,
)
# 拆分件生命周期钩子（原定义于本文件，2026-10-05 P2 拆分随迁）
from app.api.ops_extras import start_probe_loop, stop_probe_loop  # noqa: E402
from app.api.vk_admin import load_task_prices_from_db  # noqa: E402
from app.config import settings
from app.db import ModelRegistry, async_session

logger = logging.getLogger(__name__)
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.errors.handler import error_handler
from app.middleware import (
    AuthMiddleware,
    PathNormalizeMiddleware,
    RateLimitMiddleware,
    VersioningMiddleware,
)
from app.models import ErrorRecord, ModelConfig, ModelStat, PingResponse, UsageSummary


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期（替代已弃用的 on_event，FastAPI lifespan 口径）

    启动序与原 on_event("startup") 注册序一致；关停序与原反注册序（后启先停）一致。
    生命周期函数定义于模块后段，运行期解析（lifespan 在服务启动时才执行）。
    """
    # ── 启动序（原注册序）──
    await validate_critical_config()
    await load_task_prices_from_db()
    await start_probe_loop()
    await start_agent_worker()
    await start_a2a_registry()
    await start_a2a_result_consumer()
    await start_a2a_audit_shipper()
    yield
    # ── 关停序（原反注册序：后启先停）──
    # best-effort 吞后台任务死亡异常：如 Redis 凭据不符时 stop_a2a_result_consumer
    # 会因 hub 任务 run_forever 重试失败上抛 AuthenticationError，关停不应被阻断
    # （CancelledError 属正常取消语义，仍向上传播）
    for _name, _stop in (
        ("a2a_audit_shipper", stop_a2a_audit_shipper),
        ("a2a_result_consumer", stop_a2a_result_consumer),
        ("a2a_registry", stop_a2a_registry),
        ("agent_worker", stop_agent_worker),
        ("probe_ledger", stop_probe_loop),
    ):
        try:
            await _stop()
        except asyncio.CancelledError:
            raise
        except Exception as _exc:  # noqa: BLE001  关停兜底：记录后继续后续关停步骤
            logger.warning(f"关停 {_name} 异常（best-effort 忽略）: {_exc}")


app = FastAPI(
    lifespan=lifespan,
    title="YYC³ 统一模型网关",
    description="""
## 🎯 核心功能

### 支持的Provider
- **智谱GLM**: glm-4-flash, glm-4-plus（云端API）
- **Ollama**: llama3.2, codegeex4, qwen2.5等（本地部署）

### 主要接口
- `/v1/chat/completions` - 聊天完成（OpenAI兼容）
- `/v1/models` - 模型列表
- `/ws/chat` - WebSocket流式聊天
- `/ws/monitor` - 实时监控

### 知识库功能
- `/v1/knowledge-bases` - 知识库管理（创建、查询、更新、删除）
- `/v1/documents` - 文档管理（上传、解析、切片、向量化）
- `/v1/rag/search` - RAG语义检索
- `/v1/rag/ask` - 基于知识库的问答

### 认证方式
- **API Key**: 在请求头添加 `X-API-Key: YOUR_API_KEY`
- **JWT**: 在请求头添加 `Authorization: Bearer YOUR_JWT_TOKEN`

### 使用示例
```bash
# 获取模型列表
curl -H "X-API-Key: YOUR_API_KEY" https://api.0379.world/v1/models

# 聊天请求
curl -X POST https://api.0379.world/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -d '{
    "model": "llama3.2",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 创建知识库
curl -X POST https://api.0379.world/v1/knowledge-bases \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -d '{
    "name": "技术文档库",
    "description": "包含所有技术文档"
  }'

# 上传文档
curl -X POST https://api.0379.world/v1/documents/upload?knowledge_base_id=xxx \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -F "file=@document.pdf"

# RAG检索
curl -X POST https://api.0379.world/v1/rag/search \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -d '{
    "query": "如何部署模型？",
    "knowledge_base_ids": ["xxx"],
    "top_k": 5
  }'
```
    """,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    servers=[
        {"url": "https://api.0379.world", "description": "Production"},
    ],
)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# P0: 补全 OpenAPI Security Scheme，让 Swagger UI 出现 Authorize 按钮
from fastapi.openapi.utils import get_openapi


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        servers=app.servers,
    )
    openapi_schema["components"]["securitySchemes"] = {
        "APIKey": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "YYC³ API Key 认证",
        },
        "Bearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "JWT Token 认证",
        },
    }
    openapi_schema["security"] = [{"APIKey": []}, {"Bearer": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


async def validate_critical_config():
    """启动时校验关键配置，防止生产环境使用默认值"""
    logger = logging.getLogger(__name__)

    critical_checks = [
        (
            "JWT_SECRET_KEY",
            settings.jwt_secret_key,
            lambda v: v and v != "change_me_in_production",
        ),
        ("API_KEYS", settings.api_keys, lambda v: bool(v)),
        (
            "POSTGRES_PASSWORD",
            settings.db_password,
            lambda v: v and v != "change_me_in_production",
        ),
        (
            "REDIS_PASSWORD",
            settings.redis_password,
            lambda v: v and v != "change_me_in_production",
        ),
    ]

    errors = []
    warnings = []

    for name, value, check in critical_checks:
        if not check(value):
            errors.append(name)

    if errors:
        msg = f"关键配置缺失或使用默认值: {', '.join(errors)}"
        if settings.auth_enabled:
            logger.critical(msg)
            raise RuntimeError(msg)
        else:
            logger.warning(f"[非生产模式] {msg}")

    # ── 非关键但建议配置的项 ──
    if not settings.zhipu_api_key:
        warnings.append("ZHIPU_API_KEY")
    if not settings.deepseek_api_key:
        warnings.append("DEEPSEEK_API_KEY")
    if not settings.openai_api_key:
        warnings.append("OPENAI_API_KEY")

    if not errors and warnings:
        logger.info(f"以下 API Key 未配置（按需忽略）: {', '.join(warnings)}")

    logger.info("配置校验通过，服务启动正常")


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(VersioningMiddleware)
# TC-G1-004 接线：最后注册 = 最外层，先于鉴权/限流完成请求体路径归一
app.add_middleware(PathNormalizeMiddleware)

START_TIME = time.time()

app.include_router(chat.router, prefix="/v1", tags=["💬 聊天"])
app.include_router(agent.router, tags=["🤖 AI Family Agent编排"])
app.include_router(a2a.router, tags=["🔗 A2A协议"])
app.include_router(mcp.router, prefix="/v1", tags=["🔧 MCP工具"])
app.include_router(websocket.router, tags=["🔌 WebSocket"])
app.include_router(knowledge_base.router, tags=["📚 知识库管理"])
app.include_router(documents.router, tags=["📄 文档管理"])
app.include_router(rag.router, tags=["🔍 RAG检索"])
app.include_router(proxy.router, tags=["🧩 能力代理(embeddings/rerank/asr/ocr)"])
app.include_router(video_tasks.router, tags=["🎬 视频任务(MiniMax-H3异步)"])

# vk 管理看板（零依赖单页，受全局 Auth 中间件保护）
from app.services.admin_ui import router as admin_ui_router  # noqa: E402

app.include_router(admin_ui_router, tags=["📊 管理看板"])


@app.get("/health")
async def health_check():
    """
    健康检查端点

    返回系统状态、服务可用性、资源使用情况（并发检查）
    """
    from app.utils.metrics import metrics_manager

    async def _check_ollama():
        try:
            import httpx

            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get("http://localhost:11434/api/tags")
                return {
                    "status": "healthy" if resp.status_code == 200 else "unhealthy",
                    "latency_ms": int(resp.elapsed.total_seconds() * 1000),
                }
        except Exception:
            return {"status": "unreachable"}

    async def _check_redis():
        try:
            from app.cache import redis_client

            await redis_client.ping()
            return {"status": "healthy"}
        except Exception:
            return {"status": "unreachable"}

    async def _check_postgresql():
        try:
            from sqlalchemy import text as sa_text

            async with async_session() as session:
                await session.execute(sa_text("SELECT 1"))
                return {"status": "healthy"}
        except Exception:
            return {"status": "unreachable"}

    # 并发检查所有外部服务
    import asyncio

    ollama_result, redis_result, pg_result = await asyncio.gather(
        _check_ollama(),
        _check_redis(),
        _check_postgresql(),
        return_exceptions=True,
    )

    services = {
        "ollama": (
            ollama_result if not isinstance(ollama_result, BaseException) else {"status": "error"}
        ),
        "zhipu": {
            "status": "configured" if settings.zhipu_api_key else "not_configured",
        },
        "redis": (
            redis_result if not isinstance(redis_result, BaseException) else {"status": "error"}
        ),
        "postgresql": (
            pg_result if not isinstance(pg_result, BaseException) else {"status": "error"}
        ),
    }

    # 系统资源
    system = {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage("/").percent,
    }

    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "2.0.0",
        "uptime_seconds": int(time.time() - START_TIME),
        "services": services,
        "system": system,
        "metrics": {
            "active_requests": metrics_manager.get_active_requests(),
            "total_requests": metrics_manager.get_total_requests(),
            "cache_hit_rate": metrics_manager.get_cache_hit_rate(),
        },
    }


@app.get("/healthz")
async def healthz():
    """轻量存活探针（供监控/负载均衡高频探活）

    与 /health 的区别：不做外部服务依赖检查，仅确认进程存活，
    开销极小，适合 Prometheus/Traefik/监控探活高频调用。
    """
    return {
        "status": "alive",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": int(time.time() - START_TIME),
    }


@app.get("/v1/ping", response_model=PingResponse)
async def ping():
    """健康检查端点"""
    return PingResponse(status="ok")


# ── 运维/模型元数据/VK 管理端点（2026-10-05 P2 拆分迁出，行为零变更）──
# 缓存·路由器·探活·版本 → api/ops_extras.py；模型列表族·DEFAULT_MODELS →
# api/models_meta.py；VK·供应商·价格表 → api/vk_admin.py
app.include_router(ops_extras.router)
app.include_router(models_meta.router)
app.include_router(vk_admin.router)


# ── DEFAULT_MODELS/模型端点/探活/VK·价格表管理：已拆至 models_meta /
# ops_extras / vk_admin（2026-10-05 P2 拆分，行为零变更；include_router 见上方）──

async def start_agent_worker():
    """AI Family 内置编排 Worker（AGENT_WORKER_ENABLED=false 或外置 Worker 部署时可关闭）"""
    from app.api import agent as agent_module

    agent_module.start_worker()


async def stop_agent_worker():
    from app.api import agent as agent_module

    await agent_module.stop_worker()


async def start_a2a_registry():
    """A2A 启动：审计 sink 注入智云守护 + 内置编队 Agent Card 注册/心跳（A2A_ENABLED 控制）"""
    from app.services import a2a_protocol

    a2a_protocol.start_registry()


async def stop_a2a_registry():
    from app.services import a2a_protocol

    await a2a_protocol.stop_registry()


async def start_a2a_result_consumer():
    """A2A 结果流消费端：编排器聚合回执 + XAUTOCLAIM 挂起回收
    （A2A_ENABLED × A2A_RESULT_CONSUMER_ENABLED 双控；独立编排器部署时可在网关侧关闭）"""
    from app.services import a2a_result

    a2a_result.start_result_consumer()


async def stop_a2a_result_consumer():
    from app.services import a2a_result

    await a2a_result.stop_result_consumer()


async def start_a2a_audit_shipper():
    """A2A 审计流 Loki 消费端：孤儿/死信审计可检索可告警
    （A2A_ENABLED × A2A_AUDIT_LOKI_ENABLED 双控，缺省关闭）"""
    from app.services import a2a_audit

    a2a_audit.start_audit_shipper()


async def stop_a2a_audit_shipper():
    from app.services import a2a_audit

    await a2a_audit.stop_audit_shipper()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
