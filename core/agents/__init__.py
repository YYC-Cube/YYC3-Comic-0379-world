# file: __init__.py
# description: YYC3 AI Family Agent 包（Phase 0 工程化版）——统一导出入口
# author: YanYuCloudCube Team <admin@0379.email>
# created: 2026-09-25
# status: active
# tags: [agent],[package]

"""YYC³ AI Family 多 Agent 协同包（ReAct-C 九步闭环）。

源：docs/YYC3-多端部署-Agent代码/YYC3-AI-Family-Agent/（Phase 0 工程化迁移）。

事实源分层（2026-10-05 三方统一裁定，替代此前两处互相矛盾的声明）：
- 可运行代码事实源（生产域）= 本包 core/agents/（唯一被 api/agent.py 与
  agent_workers.py 生产 import 的实体；修改此处直接生效到生产）
- yyc3-ai-agent-archive/components/ = 参考快照（只读；G2 验收载体，改动不进生产）
- docs/YYC3-AI-Family-Comic-Drama-Agent/ = 规范+快照（只读展示，文档站渲染源）

已验收缺陷修复须回合本包（先例：G2-003 注入变体 / F3 复检循环 / G2-006
序列化防御 / LLM_MAX_TOKENS，2026-10-05 回灌）。

用法（Mock 模式零配置离线运行）::

    from core.agents import AIFamilyOrchestrator
    result = AIFamilyOrchestrator().execute("分析本季度营收")
    print(result["final_output"])

生产模式：设置 LLM_BASE_URL/LLM_API_KEY 指向网关或 DGX NIM 服务（见 .env.example）。
"""

from .base_agent import BaseAgent
from .chuangxiang_lingyun_agent import ChuangXiangLingYunAgent
from .gewu_zongshi_agent import GeWuZongShiAgent
from .orchestrator import AIFamilyOrchestrator
from .retriever import NullRetriever, Retriever
from .yanqi_qianhang_agent import YanQiQianHangAgent
from .yuanqi_tianshu_agent import YuanQiTianShuAgent
from .yujian_xianzhi_agent import YuJianXianZhiAgent
from .yushu_wanwu_agent import YuShuWanWuAgent
from .zhiyu_bole_agent import ZhiYuBoLeAgent
from .zhiyun_shouhu_agent import ZhiYunShouHuAgent, set_audit_sink

__all__ = [
    "AIFamilyOrchestrator",
    "BaseAgent",
    "ChuangXiangLingYunAgent",
    "GeWuZongShiAgent",
    "NullRetriever",
    "Retriever",
    "YanQiQianHangAgent",
    "YuanQiTianShuAgent",
    "YuJianXianZhiAgent",
    "YuShuWanWuAgent",
    "ZhiYuBoLeAgent",
    "ZhiYunShouHuAgent",
    "set_audit_sink",
]
