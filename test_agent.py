"""本体增强的订单管理 Agent（CRM 集成版）
==========================================
演示企业级场景：日常查询直接走 CRM 系统，仅在需要判定复杂业务规则时
才将相关数据按需注入本体、调用推理器。

架构:
  ┌─────────────────────────────────────────────────┐
  │  LLM Agent                                      │
  │  ├─ CRM 工具（直接查询，不经过本体）               │
  │  │   query_customer / list_orders / query_order  │
  │  │   query_inventory                             │
  │  └─ 本体推理工具（按需推理，仅用于业务规则判定）     │
  │      check_shipment_eligibility                  │
  │      check_expedite_eligibility                  │
  │      get_business_rules                          │
  └─────────────────────────────────────────────────┘

核心设计:
  - 本体不是数据仓库，而是规则引擎
  - 简单查询直接走 CRM，不需要本体
  - 只有判定"能否发货""能否加急"等规则时，才把那一笔订单的数据
    临时注入本体、推理、返回结果
  - 规则变更只改本体，数据源变更只改 CRM 层

使用方式:
  export OPENAI_API_KEY=sk-...
  python test_agent.py

  若未设置 API Key，自动进入离线演示模式。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from owlready2 import ThingClass, destroy_entity, get_ontology, sync_reasoner


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  模拟 CRM / ERP 数据源
#  企业中这里对应的是数据库查询、REST API 调用等
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class CRMCustomer:
    customer_id: str
    name: str
    tier: str        # "VIP", "STANDARD"
    contact: str     # 联系方式
    region: str      # 所属区域


@dataclass
class CRMAllocation:
    allocation_id: str
    order_id: str
    warehouse: str   # 仓库
    available_qty: int
    qc_passed: bool


@dataclass
class CRMOrder:
    order_id: str
    customer_id: str
    required_qty: int
    product: str     # 产品名称
    status: str      # "pending", "processing", "shipped"


class CRMSystem:
    """模拟 CRM / ERP 系统，提供订单、客户、库存数据。
    在真实场景中替换为数据库连接或 API 调用。"""

    def __init__(self):
        self._customers: dict[str, CRMCustomer] = {}
        self._orders: dict[str, CRMOrder] = {}
        self._allocations: dict[str, CRMAllocation] = {}
        self._load_sample_data()

    def _load_sample_data(self):
        """模拟从数据库加载的真实业务数据。"""
        customers = [
            CRMCustomer("C001", "张总集团", "VIP", "zhang@example.com", "华东"),
            CRMCustomer("C002", "李氏贸易", "STANDARD", "li@example.com", "华南"),
            CRMCustomer("C003", "王氏科技", "VIP", "wang@example.com", "华北"),
            CRMCustomer("C004", "赵记商行", "STANDARD", "zhao@example.com", "西南"),
        ]
        for c in customers:
            self._customers[c.customer_id] = c

        orders = [
            CRMOrder("ORD-2024-001", "C001", 100, "精密轴承-A型", "processing"),
            CRMOrder("ORD-2024-002", "C002", 50, "标准螺栓-M8", "processing"),
            CRMOrder("ORD-2024-003", "C003", 200, "电子元件-IC芯片", "pending"),
            CRMOrder("ORD-2024-004", "C004", 30, "标准螺栓-M10", "processing"),
            CRMOrder("ORD-2024-005", "C001", 80, "液压阀门-B型", "pending"),
        ]
        for o in orders:
            self._orders[o.order_id] = o

        allocations = [
            CRMAllocation("ALLOC-001", "ORD-2024-001", "上海仓", 120, True),
            CRMAllocation("ALLOC-002", "ORD-2024-002", "广州仓", 50, False),
            CRMAllocation("ALLOC-003", "ORD-2024-003", "北京仓", 250, True),
            CRMAllocation("ALLOC-004", "ORD-2024-004", "成都仓", 30, True),
            CRMAllocation("ALLOC-005", "ORD-2024-005", "上海仓", 100, False),
        ]
        for a in allocations:
            self._allocations[a.allocation_id] = a

    # ── 查询接口（企业中对应 SQL 或 API 调用）──

    def get_all_customers(self) -> list[CRMCustomer]:
        return list(self._customers.values())

    def get_customer(self, customer_id: str) -> CRMCustomer | None:
        return self._customers.get(customer_id)

    def get_all_orders(self) -> list[CRMOrder]:
        return list(self._orders.values())

    def get_order(self, order_id: str) -> CRMOrder | None:
        return self._orders.get(order_id)

    def get_allocations_for_order(self, order_id: str) -> list[CRMAllocation]:
        return [a for a in self._allocations.values() if a.order_id == order_id]

    def create_order(
        self, customer_id: str, product: str, required_qty: int,
        available_qty: int, warehouse: str, qc_passed: bool,
    ) -> CRMOrder:
        """模拟在 CRM 中创建新订单及其库存分配。"""
        seq = len(self._orders) + 1
        order = CRMOrder(f"ORD-2024-{seq:03d}", customer_id, required_qty, product, "pending")
        alloc = CRMAllocation(
            f"ALLOC-{seq:03d}", order.order_id, warehouse, available_qty, qc_passed
        )
        self._orders[order.order_id] = order
        self._allocations[alloc.allocation_id] = alloc
        return order


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  本体推理引擎 — 仅在需要判定业务规则时按需调用
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class OntologyReasoner:
    """按需将单笔订单数据注入本体 TBox，推理后返回业务分类结果。

    设计原则:
      - 本体只包含 TBox（类定义 + 规则），不预加载 ABox
      - 每次推理前清空旧的 ABox，注入目标订单数据，推理，返回结果
      - 本体是规则引擎，不是数据仓库
    """

    def __init__(self, ontology_path: str | Path):
        path = Path(ontology_path)
        if not path.exists():
            raise FileNotFoundError(f"本体文件未找到: {path}")
        self._ontology_uri = path.resolve().as_uri()
        # 加载一次并清理掉文件中的测试 ABox
        onto = get_ontology(self._ontology_uri).load()
        for ind in list(onto.individuals()):
            destroy_entity(ind)
        self._base_onto = onto

    def _cls(self, name: str):
        return self._base_onto.search_one(iri=f"*#{name}")

    def _clear_abox(self):
        for ind in list(self._base_onto.individuals()):
            destroy_entity(ind)

    def reason_order(
        self,
        customer_tier: str,
        customer_id: str,
        order_id: str,
        required_qty: int,
        allocations: list[dict],
    ) -> dict:
        """将一笔订单的相关数据注入本体、推理、返回结论。

        参数:
          customer_tier: "VIP" 或 "STANDARD"
          customer_id: 客户标识
          order_id: 订单标识
          required_qty: 需求数量
          allocations: [{"id": ..., "available_qty": ..., "qc_passed": ...}, ...]

        返回:
          {"ready_to_ship": bool, "expedite_eligible": bool, "inferred_types": [...]}
        """
        onto = self._base_onto
        self._clear_abox()

        # 注入客户个体
        cust_cls = self._cls("VIPCustomer") if customer_tier == "VIP" else self._cls("Customer")
        cust_ind = cust_cls(customer_id)

        # 注入库存分配个体
        alloc_cls = self._cls("InventoryAllocation")
        alloc_inds = []
        for a in allocations:
            alloc_ind = alloc_cls(a["id"])
            alloc_ind.availableQty = [a["available_qty"]]
            alloc_ind.qcPassed = [a["qc_passed"]]
            alloc_inds.append(alloc_ind)

        # 注入订单个体
        order_cls = self._cls("Order")
        order_ind = order_cls(order_id)
        order_ind.hasCustomer = [cust_ind]
        order_ind.hasAllocation = alloc_inds
        order_ind.requiredQty = [required_qty]

        # 推理
        with onto:
            sync_reasoner(infer_property_values=True, debug=0)

        ready_cls = self._cls("ReadyToShipOrder")
        expedite_cls = self._cls("ExpediteEligibleOrder")
        inferred = sorted({
            c.name for c in order_ind.is_a if isinstance(c, ThingClass)
        })

        return {
            "ready_to_ship": order_ind in ready_cls.instances(),
            "expedite_eligible": order_ind in expedite_cls.instances(),
            "inferred_types": inferred,
        }

    def get_rules_description(self) -> str:
        return (
            "本体定义的业务规则（TBox）：\n"
            "1. ReadyToShipOrder（满足发货条件的订单）：\n"
            "   Order 且 hasAllocation 关联的 InventoryAllocation 的 qcPassed = true\n"
            "   → 订单关联的库存已通过质检即可发货\n\n"
            "2. ExpediteEligibleOrder（可加急发货的订单）：\n"
            "   ReadyToShipOrder 且 hasCustomer 关联的是 VIPCustomer\n"
            "   → 满足发货条件 + VIP客户 → 自动获得加急资格\n\n"
            "3. VIPCustomer 是 Customer 的子类\n\n"
            "这些规则由本体形式化定义，推理器自动完成分类。\n"
            "Agent 仅在需要判定这些规则时才调用本体推理，日常查询直接走 CRM。"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  辅助函数：从 CRM 收集推理所需数据
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _collect_order_for_reasoning(crm: CRMSystem, order_id: str) -> dict | None:
    """从 CRM 收集一笔订单的全部关联数据，组装为推理器入参。"""
    crm_order = crm.get_order(order_id)
    if crm_order is None:
        return None
    crm_cust = crm.get_customer(crm_order.customer_id)
    if crm_cust is None:
        return None
    crm_allocs = crm.get_allocations_for_order(order_id)
    return {
        "customer_tier": crm_cust.tier,
        "customer_id": crm_cust.customer_id,
        "order_id": crm_order.order_id,
        "required_qty": crm_order.required_qty,
        "allocations": [
            {"id": a.allocation_id, "available_qty": a.available_qty, "qc_passed": a.qc_passed}
            for a in crm_allocs
        ],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LangChain Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def build_agent(crm: CRMSystem, reasoner: OntologyReasoner):
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langchain.agents import create_agent

    # ── CRM 直接查询工具（不经过本体）──

    @tool
    def query_customer(customer_id: str) -> str:
        """查询客户信息。直接从CRM系统获取，不经过本体。
        参数 customer_id: 客户ID，如 C001"""
        c = crm.get_customer(customer_id)
        if c is None:
            return f"未找到客户 {customer_id}"
        return (
            f"客户信息（来自CRM）：\n"
            f"  客户ID: {c.customer_id}\n"
            f"  名称: {c.name}\n"
            f"  等级: {c.tier}\n"
            f"  联系方式: {c.contact}\n"
            f"  区域: {c.region}"
        )

    @tool
    def list_orders() -> str:
        """列出所有订单。直接从CRM系统获取，不经过本体。"""
        orders = crm.get_all_orders()
        if not orders:
            return "暂无订单。"
        lines = []
        for o in orders:
            c = crm.get_customer(o.customer_id)
            lines.append(
                f"- {o.order_id} | {o.product} | 客户:{c.name if c else o.customer_id} | "
                f"数量:{o.required_qty} | 状态:{o.status}"
            )
        return "订单列表（来自CRM）：\n" + "\n".join(lines)

    @tool
    def query_order(order_id: str) -> str:
        """查询某个订单的详细信息。直接从CRM系统获取，不经过本体。
        参数 order_id: 订单ID，如 ORD-2024-001"""
        o = crm.get_order(order_id)
        if o is None:
            return f"未找到订单 {order_id}"
        c = crm.get_customer(o.customer_id)
        return (
            f"订单信息（来自CRM）：\n"
            f"  订单ID: {o.order_id}\n"
            f"  产品: {o.product}\n"
            f"  客户: {c.name if c else o.customer_id} ({c.tier if c else 'N/A'})\n"
            f"  需求数量: {o.required_qty}\n"
            f"  状态: {o.status}"
        )

    @tool
    def query_inventory(order_id: str) -> str:
        """查询某个订单的库存分配情况。直接从库存系统获取，不经过本体。
        参数 order_id: 订单ID，如 ORD-2024-001"""
        allocs = crm.get_allocations_for_order(order_id)
        if not allocs:
            return f"订单 {order_id} 暂无库存分配记录。"
        lines = [f"库存分配信息（来自库存系统）- 订单 {order_id}："]
        for a in allocs:
            lines.append(
                f"  分配ID: {a.allocation_id} | 仓库: {a.warehouse} | "
                f"可用数量: {a.available_qty} | 质检: {'通过' if a.qc_passed else '未通过'}"
            )
        return "\n".join(lines)

    # ── 本体推理工具（仅在需要判定业务规则时调用）──

    @tool
    def check_shipment_eligibility(order_id: str) -> str:
        """【需要本体推理】判定订单是否满足发货条件（ReadyToShipOrder）。
        这个工具会从CRM获取订单数据，注入本体推理器，基于本体规则判定。
        仅在需要判定"能否发货"时调用，普通查询请用其他CRM工具。
        参数 order_id: 订单ID，如 ORD-2024-001"""
        data = _collect_order_for_reasoning(crm, order_id)
        if data is None:
            return f"未找到订单 {order_id} 或其关联数据"
        result = reasoner.reason_order(**data)
        o = crm.get_order(order_id)
        c = crm.get_customer(o.customer_id) if o else None
        allocs = crm.get_allocations_for_order(order_id)
        qc_info = ", ".join(
            f"{a.allocation_id}({'通过' if a.qc_passed else '未通过'})"
            for a in allocs
        )
        return (
            f"发货条件判定结果（数据来自CRM，规则来自本体推理）：\n"
            f"  订单: {order_id} ({o.product if o else 'N/A'})\n"
            f"  客户: {c.name if c else 'N/A'} (等级:{c.tier if c else 'N/A'})\n"
            f"  库存质检: {qc_info}\n"
            f"  推理结论: {'✅ 满足发货条件' if result['ready_to_ship'] else '❌ 不满足发货条件'}\n"
            f"  判定依据: 本体规则 ReadyToShipOrder = Order ∩ hasAllocation some (qcPassed=true)"
        )

    @tool
    def check_expedite_eligibility(order_id: str) -> str:
        """【需要本体推理】判定订单是否可加急发货（ExpediteEligibleOrder）。
        这个工具会从CRM获取订单数据，注入本体推理器，基于本体规则判定。
        仅在需要判定"能否加急"时调用，普通查询请用其他CRM工具。
        参数 order_id: 订单ID，如 ORD-2024-001"""
        data = _collect_order_for_reasoning(crm, order_id)
        if data is None:
            return f"未找到订单 {order_id} 或其关联数据"
        result = reasoner.reason_order(**data)
        o = crm.get_order(order_id)
        c = crm.get_customer(o.customer_id) if o else None
        allocs = crm.get_allocations_for_order(order_id)
        qc_info = ", ".join(
            f"{a.allocation_id}({'通过' if a.qc_passed else '未通过'})"
            for a in allocs
        )
        lines = [
            f"加急发货判定结果（数据来自CRM，规则来自本体推理）：",
            f"  订单: {order_id} ({o.product if o else 'N/A'})",
            f"  客户: {c.name if c else 'N/A'} (等级:{c.tier if c else 'N/A'})",
            f"  库存质检: {qc_info}",
            f"  满足发货条件: {'是' if result['ready_to_ship'] else '否'}",
            f"  可加急发货: {'✅ 是' if result['expedite_eligible'] else '❌ 否'}",
        ]
        if not result['ready_to_ship']:
            lines.append("  原因: 库存未通过质检，不满足基本发货条件，因此也无法加急")
        elif not result['expedite_eligible']:
            lines.append("  原因: 满足发货条件，但客户不是VIP等级，不符合加急资格")
        else:
            lines.append("  原因: 满足发货条件 + VIP客户，符合加急资格")
        lines.append(
            "  判定依据: 本体规则 ExpediteEligibleOrder = ReadyToShipOrder ∩ hasCustomer some VIPCustomer"
        )
        return "\n".join(lines)

    @tool
    def get_business_rules() -> str:
        """查看本体中定义的业务规则（TBox），了解发货条件和加急资格的判定逻辑"""
        return reasoner.get_rules_description()

    tools = [
        # CRM 直接查询（不经过本体）
        query_customer,
        list_orders,
        query_order,
        query_inventory,
        # 本体推理（仅在判定业务规则时）
        check_shipment_eligibility,
        check_expedite_eligibility,
        get_business_rules,
    ]

    system_prompt = (
        "你是一个订单管理助手，背后接入了 CRM/ERP 系统和基于 OWL 本体的业务推理引擎。\n\n"
        "##重要：工具选择原则\n"
        "- 日常查询（客户信息、订单列表、库存情况）→ 直接用 CRM 工具，不需要本体\n"
        "- 业务规则判定（能否发货、能否加急）→ 使用本体推理工具\n"
        "- 本体是规则引擎，不是数据仓库。不要为了简单查询而调用推理工具\n\n"
        "##工具分类\n"
        "CRM直接查询: query_customer, list_orders, query_order, query_inventory\n"
        "本体推理判定: check_shipment_eligibility, check_expedite_eligibility, get_business_rules\n\n"
        "##系统客户\n"
        "C001(张总集团/VIP), C002(李氏贸易/普通), C003(王氏科技/VIP), C004(赵记商行/普通)\n\n"
        "##回答原则\n"
        "- 说明数据来源（CRM直接查询 还是 本体推理判定）\n"
        "- 推理结果要说明判定依据（来自本体规则）\n"
        "- 用简洁清晰的中文回答"
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    return create_agent(llm, tools, system_prompt=system_prompt)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  离线演示模式
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _demo_mode(crm: CRMSystem, reasoner: OntologyReasoner):
    """无 LLM 时展示 CRM 直接查询与本体按需推理的区别。"""
    print("=" * 64)
    print("  离线演示：CRM 直接查询 vs 本体按需推理")
    print("=" * 64)

    # ── 1. CRM 直接查询（不经过本体）──
    print("\n┌─────────────────────────────────────────────┐")
    print("│  CRM 直接查询（不经过本体）                    │")
    print("└─────────────────────────────────────────────┘")

    print("\n📊 查客户 C001:")
    c = crm.get_customer("C001")
    print(f"  {c.name} | 等级:{c.tier} | 区域:{c.region} | 联系:{c.contact}")

    print("\n📦 查所有订单:")
    for o in crm.get_all_orders():
        cust = crm.get_customer(o.customer_id)
        print(f"  {o.order_id} | {o.product} | 客户:{cust.name} | 数量:{o.required_qty} | 状态:{o.status}")

    print("\n📦 查订单 ORD-2024-001 的库存:")
    for a in crm.get_allocations_for_order("ORD-2024-001"):
        print(f"  {a.allocation_id} | 仓库:{a.warehouse} | 可用:{a.available_qty} | 质检:{'通过' if a.qc_passed else '未通过'}")

    # ── 2. 本体按需推理（仅在判定业务规则时）──
    print("\n┌─────────────────────────────────────────────┐")
    print("│  本体按需推理（仅在判定业务规则时）              │")
    print("└─────────────────────────────────────────────┘")

    print("\n📋 本体业务规则:")
    print(reasoner.get_rules_description())

    test_orders = ["ORD-2024-001", "ORD-2024-002", "ORD-2024-003", "ORD-2024-004", "ORD-2024-005"]
    print("🔍 对每笔订单按需推理（从CRM取数据 → 注入本体 → 推理）:")
    for oid in test_orders:
        data = _collect_order_for_reasoning(crm, oid)
        result = reasoner.reason_order(**data)
        o = crm.get_order(oid)
        c = crm.get_customer(o.customer_id)
        allocs = crm.get_allocations_for_order(oid)
        qc = "通过" if allocs and allocs[0].qc_passed else "未通过"
        print(
            f"  {oid} | {o.product} | {c.name}({c.tier}) | 质检:{qc} | "
            f"发货:{'✅' if result['ready_to_ship'] else '❌'} | "
            f"加急:{'✅' if result['expedite_eligible'] else '❌'}"
        )

    print("\n" + "=" * 64)
    print("💡 关键区别:")
    print("  - 查客户/查订单/查库存 → 直接走CRM，不需要本体")
    print("  - 判定能否发货/能否加急 → 按需调用本体推理器")
    print("  - 本体是规则引擎，不是数据仓库")
    print("=" * 64)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  主入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def main():
    ontology_path = Path(__file__).with_name("demo-ontology.owx")

    crm = CRMSystem()
    print(f"✅ CRM 系统已初始化，{len(crm.get_all_customers())}个客户, {len(crm.get_all_orders())}个订单")

    reasoner = OntologyReasoner(ontology_path)
    print("✅ 本体推理引擎已加载（仅 TBox，ABox 按需注入）\n")

    if not os.environ.get("OPENAI_API_KEY"):
        print("未设置 OPENAI_API_KEY，进入离线演示模式\n")
        _demo_mode(crm, reasoner)
        return

    agent = build_agent(crm, reasoner)

    print("=== 本体增强订单管理 Agent（CRM 集成版）===")
    print("输入问题与 Agent 对话，输入 q 退出\n")
    print("示例问题:")
    print("  - 查一下客户 C001 的信息")
    print("  - 系统里有哪些订单？")
    print("  - ORD-2024-001 的库存情况怎样？")
    print("  - ORD-2024-001 能不能加急发货？（这个会调用本体推理）")
    print("  - 哪些订单可以发货？（会逐个调用本体推理）")
    print("  - 解释一下发货和加急的判定规则")
    print()

    history = []
    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        if not user_input or user_input.lower() == "q":
            print("再见！")
            break

        from langchain_core.messages import AIMessage, HumanMessage

        history.append(HumanMessage(content=user_input))
        result = agent.invoke({"messages": history})
        ai_msg = result["messages"][-1]
        print(f"\nAgent: {ai_msg.content}\n")
        history.append(AIMessage(content=ai_msg.content))


if __name__ == "__main__":
    main()
