# file: vk_admin.py
# description: 虚拟密钥/供应商/协同事务价格表管理端点 —— main.py 拆分件 3/3
# author: YanYuCloudCube Team <admin@0379.email>
# version: v1.0.0
# created: 2026-10-05
# status: active
# tags: [api],[admin],[virtual-key],[pricing]
# 拆分说明：自 main.py 原样迁出（2026-10-05 P2 专项，行为零变更）；
#   价格表启动加载（load_task_prices_from_db）随迁，main lifespan 经本模块调用。

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.errors.handler import error_handler

logger = logging.getLogger(__name__)
router = APIRouter()


# ── P0-1 收口：虚拟密钥管理端点（学 litellm /key 管理面）──────


class VKCreateRequest(BaseModel):
    name: str
    owner: str = "yanyu"
    model_whitelist: List[str] = []
    monthly_budget_usd: float = 0.0
    rate_limit_tpm: int = 0
    expires_at: Optional[str] = None
    metadata: Optional[dict] = None


@router.get("/v1/providers")
async def list_providers():
    """供应商运维面：已登记的 provider 实现（配置 OPENAI_COMPATIBLE_UPSTREAMS.provider 用）"""
    from app.services.providers.registry import known_providers

    return {"providers": known_providers()}


@router.post("/v1/admin/virtual-keys")
async def admin_vk_create(req: VKCreateRequest):
    """创建虚拟密钥。明文只在本响应出现一次，请妥善保管"""
    from app.services.virtual_key_manager import vk_create

    try:
        return await vk_create(
            name=req.name,
            owner=req.owner,
            model_whitelist=req.model_whitelist,
            monthly_budget_usd=req.monthly_budget_usd,
            rate_limit_tpm=req.rate_limit_tpm,
            expires_at=req.expires_at,
            metadata=req.metadata,
        )
    except Exception as e:
        error_response = await error_handler.handle(e, context={"operation": "vk_create"})
        raise HTTPException(status_code=error_response["status_code"], detail=error_response)


@router.get("/v1/admin/virtual-keys")
async def admin_vk_list(owner: Optional[str] = None, active_only: bool = False):
    """列虚拟密钥（key 脱敏为前 8 位 hint）"""
    from app.services.virtual_key_manager import vk_list

    try:
        return {"keys": await vk_list(owner=owner, include_disabled=not active_only)}
    except Exception as e:
        error_response = await error_handler.handle(e, context={"operation": "vk_list"})
        raise HTTPException(status_code=error_response["status_code"], detail=error_response)


class VKUpdateRequest(BaseModel):
    status: Optional[str] = None
    monthly_budget_usd: Optional[float] = None
    rate_limit_tpm: Optional[int] = None
    model_whitelist: Optional[List[str]] = None


@router.patch("/v1/admin/virtual-keys/{key_id}")
async def admin_vk_update(key_id: str, req: VKUpdateRequest):
    """更新虚拟密钥：启停（status=active|disabled）/ 编辑预算、TPM、模型白名单"""
    from app.services.virtual_key_manager import vk_update_fields, vk_update_status

    if req.status is None and (
        req.monthly_budget_usd is None
        and req.rate_limit_tpm is None
        and req.model_whitelist is None
    ):
        raise HTTPException(
            status_code=422,
            detail="至少提供 status / monthly_budget_usd / rate_limit_tpm / model_whitelist 之一",
        )

    try:
        updated = {"status": False, "fields": False}
        if req.status is not None:
            updated["status"] = await vk_update_status(key_id, req.status)
        if (
            req.monthly_budget_usd is not None
            or req.rate_limit_tpm is not None
            or req.model_whitelist is not None
        ):
            updated["fields"] = await vk_update_fields(
                key_id,
                monthly_budget_usd=req.monthly_budget_usd,
                rate_limit_tpm=req.rate_limit_tpm,
                model_whitelist=req.model_whitelist,
            )
        return {"updated": updated, "key_id": key_id}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        error_response = await error_handler.handle(e, context={"operation": "vk_update"})
        raise HTTPException(status_code=error_response["status_code"], detail=error_response)


@router.delete("/v1/admin/virtual-keys/{key_id}")
async def admin_vk_delete(key_id: str):
    """删除虚拟密钥（消费流水保留，key_id 置空以存审计）"""
    from app.services.virtual_key_manager import vk_delete

    try:
        deleted = await vk_delete(key_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"虚拟密钥不存在: {key_id}")
        return {"deleted": True, "key_id": key_id}
    except HTTPException:
        raise
    except Exception as e:
        error_response = await error_handler.handle(e, context={"operation": "vk_delete"})
        raise HTTPException(status_code=error_response["status_code"], detail=error_response)


@router.get("/v1/admin/virtual-keys/{key_id}/usage")
async def admin_vk_usage(key_id: str, days: int = 30):
    """用量查询：近 N 天按模型聚合成本（供看板/预算复盘）"""
    from app.services.virtual_key_manager import vk_usage

    try:
        return await vk_usage(key_id, days=days)
    except Exception as e:
        error_response = await error_handler.handle(e, context={"operation": "vk_usage"})
        raise HTTPException(status_code=error_response["status_code"], detail=error_response)


# ── 协同事务价格表管理（A2A 成本直报运行时覆盖；内存态，对齐 MODEL_PRICES_JSON 语义）──


class TaskPriceRequest(BaseModel):
    price_usd: float = Field(..., ge=0, description="每任务固定成本（USD）")


@router.get("/v1/admin/pricing/task-types")
async def admin_pricing_task_types():
    """列协同事务任务类型价格表（TASK_TYPE_PRICES 运行时态，X-A2A-Cost 取值源）"""
    from app.services.pricing import TASK_TYPE_PRICES

    return {"task_types": dict(TASK_TYPE_PRICES)}


@router.put("/v1/admin/pricing/task-types/{task_type}")
async def admin_pricing_task_type_upsert(task_type: str, req: TaskPriceRequest):
    """登记/更新任务类型单价（内存即时生效 + PG task_prices 持久；未知类型也可预置）

    persisted=false 表示 PG 未落库（表未迁移/DB 不可达）——内存价已生效但重启丢失，
    提示运维执行 004_task_prices.sql 迁移。
    """
    from app.services import pricing as pricing_svc

    try:
        persisted = await pricing_svc.upsert_task_price_persisted(task_type, req.price_usd)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "updated": True,
        "task_type": task_type,
        "price_usd": req.price_usd,
        "persisted": persisted,
    }


async def load_task_prices_from_db():
    """协同事务价格表启动加载：PG task_prices 覆盖内存默认（best-effort 不阻断）"""
    from app.services import pricing as pricing_svc

    await pricing_svc.load_task_prices_from_db()
