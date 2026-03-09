"""本体增强的订单管理 Agent（CRM 集成版）
==========================================
演示企业级场景：本体只定义业务规则 (TBox)，实例数据 (ABox) 从 CRM 系统获取，
通过映射层注入本体后交由推理器分类。

架构:
  CRM系统(模拟) → 数据映射层 → 本体TBox + 动态ABox → 推理器 → Agent Tools → LLM

核心价值:
  - 本体只维护规则，不存储业务数据
  - 数据从真实系统获取，通过映射层转化为本体个体
  - 规则变更只需修改本体，数据源变更只需修改映射层

使用方式:
  export OPENAI_API_KEY=sk-...
  python test_agent.py

  若未设置 API Key，自动进入离线演示模式。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from owlready2 import ThingClass, destroy_entity, get_ontology, sync_reasoner


# ── 模拟 CRM 数据源 ────────────────────────────────────────
# 企业中这里对应的是数据库查询、REST API 调用等


@dataclass
class CRMCustomer:
    customer_id: str
    name: str
    tier: str  # "VIP", "STANDARD"


@dataclass
class CRMAllocation:
    allocation_id: str
    order_id: str
    available_qty: int
    qc_passed: bool


@dataclass
class CRMOrder:
    order_id: str
    customer_id: str
    required_qty: int
    status: str  # "pending", "processing", "shipped"


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
        # ── 客户表 ──
        customers = [
            CRMCustomer("C001", "张总集团", "VIP"),
            CRMCustomer("C002", "李氏贸易", "STANDARD"),
            CRMCustomer("C003", "王氏科技", "VIP"),
            CRMCustomer("C004", "赵记商行", "STANDARD"),
        ]
        for c in customers:
            self._customers[c.customer_id] = c

        # ── 订单表 ──
        orders = [
            CRMOrder("ORD-2024-001", "C001", 100, "processing"),  # VIP + 质检通过
            CRMOrder("ORD-2024-002", "C002", 50, "processing"),   # 普通 + 质检未通过
            CRMOrder("ORD-2024-003", "C003", 200, "pending"),     # VIP + 质检通过
            CRMOrder("ORD-2024-004", "C004", 30, "processing"),   # 普通 + 质检通过
            CRMOrder("ORD-2024-005", "C001", 80, "pending"),      # VIP + 质检未通过
        ]
        for o in orders:
            self._orders[o.order_id] = o

        # ── 库存分配表 ──
        allocations = [
            CRMAllocation("ALLOC-001", "ORD-2024-001", 120, True),
            CRMAllocation("ALLOC-002", "ORD-2024-002", 50, False),
            CRMAllocation("ALLOC-003", "ORD-2024-003", 250, True),
            CRMAllocation("ALLOC-004", "ORD-2024-004", 30, True),
            CRMAllocation("ALLOC-005", "ORD-2024-005", 100, False),
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
        self, customer_id: str, required_qty: int, available_qty: int, qc_passed: bool
    ) -> CRMOrder:
        """模拟在 CRM 中创建新订单及其库存分配。"""
        seq = len(self._orders) + 1
        order = CRMOrder(f"ORD-2024-{seq:03d}", customer_id, required_qty, "pending")
        alloc = CRMAllocation(f"ALLOC-{seq:03d}", order.order_id, available_qty, qc_passed)
        self._orders[order.order_id] = order
        self._allocations[alloc.allocation_id] = alloc
        return order


# ── CRM → 本体 映射层 ──────────────────────────────────────
# 定义 CRM 字段如何转化为本体个体与属性


class CRMToOntologyMapper:
    """将 CRM 业务数据映射为本体 ABox 个体。

    映射规则:
      CRM customers.tier="VIP"  → owl:VIPCustomer
      CRM customers.tier!="VIP" → owl:Customer
      CRM orders 行             → owl:Order + hasCustomer + requiredQty
      CRM allocations 行        → owl:InventoryAllocation + availableQty + qcPassed
      CRM order-allocation 关系 → owl:hasAllocation
    """

    def __init__(self, onto):
        self.onto = onto

    def _cls(self, name: str):
        return self.onto.search_one(iri=f"*#{name}")

    def map_customer(self, crm_cust: CRMCustomer):
        """CRM 客户 → 本体 Customer/VIPCustomer 个体。"""
        cls = self._cls("VIPCustomer") if crm_cust.tier == "VIP" else self._cls("Customer")
        ind = cls(crm_cust.customer_id)
        return ind

    def map_allocation(self, crm_alloc: CRMAllocation):
        """CRM 库存分配 → 本体 InventoryAllocation 个体。"""
        alloc_cls = self._cls("InventoryAllocation")
        ind = alloc_cls(crm_alloc.allocation_id)
        ind.availableQty = [crm_alloc.available_qty]
        ind.qcPassed = [crm_alloc.qc_passed]
        return ind

    def map_order(self, crm_order: CRMOrder, customer_ind, allocation_inds: list):
        """CRM 订单 → 本体 Order 个体，关联客户与库存分配。"""
        order_cls = self._cls("Order")
        ind = order_cls(crm_order.order_id)
        ind.hasCustomer = [customer_ind]
        ind.hasAllocation = allocation_inds
        ind.requiredQty = [crm_order.required_qty]
        return ind


# ── 本体管理层（TBox + 动态 ABox）──────────────────────────


class OntologyManager:
    """加载本体 TBox，从 CRM 获取数据并映射为 ABox，然后推理。"""

    def __init__(self, ontology_path: str | Path, crm: CRMSystem):
        path = Path(ontology_path)
        if not path.exists():
            raise FileNotFoundError(f"本体文件未找到: {path}")
        self.onto = get_ontology(path.resolve().as_uri()).load()
        self.crm = crm
        self._reasoned = False
        self._clear_abox()
        self._sync_from_crm()

    def _clear_abox(self):
        """清除本体文件中的测试用 ABox 数据，只保留 TBox。"""
        for ind in list(self.onto.individuals()):
            destroy_entity(ind)

    def _sync_from_crm(self):
        """从 CRM 拉取全量数据并映射为本体个体。"""
        mapper = CRMToOntologyMapper(self.onto)

        # 1. 映射客户
        customer_map: dict[str, object] = {}
        for crm_cust in self.crm.get_all_customers():
            customer_map[crm_cust.customer_id] = mapper.map_customer(crm_cust)

        # 2. 映射订单及其库存分配
        for crm_order in self.crm.get_all_orders():
            cust_ind = customer_map.get(crm_order.customer_id)
            alloc_inds = [
                mapper.map_allocation(a)
                for a in self.crm.get_allocations_for_order(crm_order.order_id)
            ]
            mapper.map_order(crm_order, cust_ind, alloc_inds)

        self.invalidate()

    def ensure_reasoned(self):
        if not self._reasoned:
            with self.onto:
                sync_reasoner(infer_property_values=True, debug=0)
            self._reasoned = True

    def invalidate(self):
        self._reasoned = False

    def refresh_from_crm(self):
        """重新从 CRM 同步数据（模拟数据变更场景）。"""
        self._clear_abox()
        self._sync_from_crm()

    # ── 内部工具 ──

    def _cls(self, name: str):
        return self.onto.search_one(iri=f"*#{name}")

    def _class_names(self, entity) -> list[str]:
        return sorted({c.name for c in entity.is_a if isinstance(c, ThingClass)})

    def _order_info(self, order) -> dict:
        ready_cls = self._cls("ReadyToShipOrder")
        expedite_cls = self._cls("ExpediteEligibleOrder")
        crm_order = self.crm.get_order(order.name)
        info = {
            "订单ID": order.name,
            "CRM状态": crm_order.status if crm_order else "N/A",
            "推理后类型": self._class_names(order),
            "需求数量": order.requiredQty[0] if order.requiredQty else None,
        }
        if order.hasCustomer:
            cust = order.hasCustomer[0]
            crm_cust = self.crm.get_customer(cust.name)
            info["客户"] = crm_cust.name if crm_cust else cust.name
            info["客户ID"] = cust.name
            info["客户等级"] = crm_cust.tier if crm_cust else "N/A"
            info["客户本体类型"] = self._class_names(cust)
        if order.hasAllocation:
            alloc = order.hasAllocation[0]
            info["库存分配"] = {
                "ID": alloc.name,
                "可用数量": alloc.availableQty[0] if alloc.availableQty else None,
                "质检通过": alloc.qcPassed[0] if alloc.qcPassed else None,
            }
        info["满足发货条件"] = order in ready_cls.instances()
        info["可加急发货"] = order in expedite_cls.instances()
        return info

    # ── 对外 API ──

    def list_orders(self) -> list[dict]:
        self.ensure_reasoned()
        order_cls = self._cls("Order")
        return [self._order_info(o) for o in order_cls.instances()]

    def get_order(self, order_id: str) -> dict | None:
        self.ensure_reasoned()
        order = self.onto.search_one(iri=f"*#{order_id}")
        if order is None:
            return None
        return self._order_info(order)

    def create_order(
        self,
        customer_id: str,
        required_qty: int,
        available_qty: int,
        qc_passed: bool,
    ) -> dict:
        # 1. 先在 CRM 中创建
        crm_order = self.crm.create_order(customer_id, required_qty, available_qty, qc_passed)
        # 2. 重新同步到本体
        self.refresh_from_crm()
        # 3. 推理并返回
        return self.get_order(crm_order.order_id)

    def get_business_rules(self) -> str:
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
            "ABox 数据从 CRM 系统实时获取，通过映射层注入本体。"
        )

    def get_data_mapping_info(self) -> str:
        return (
            "CRM → 本体 数据映射规则：\n"
            "  CRM customers.tier='VIP'   → 本体 VIPCustomer 个体\n"
            "  CRM customers.tier='STANDARD' → 本体 Customer 个体\n"
            "  CRM orders 行              → 本体 Order 个体\n"
            "    order.customer_id         → 本体 hasCustomer 关系\n"
            "    order.required_qty        → 本体 requiredQty 属性\n"
            "  CRM allocations 行         → 本体 InventoryAllocation 个体\n"
            "    allocation.available_qty  → 本体 availableQty 属性\n"
            "    allocation.qc_passed      → 本体 qcPassed 属性\n"
            "    allocation.order_id       → 本体 hasAllocation 关系"
        )


# ── LangChain Agent ────────────────────────────────────────


def build_agent(onto_mgr: OntologyManager):
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langchain.agents import create_agent

    @tool
    def list_orders() -> str:
        """列出系统中所有订单及其推理后的业务分类。数据来源于CRM系统。"""
        orders = onto_mgr.list_orders()
        if not orders:
            return "系统中暂无订单。"
        lines = []
        for o in orders:
            lines.append(
                f"- {o['订单ID']} (CRM状态:{o['CRM状态']}, 客户:{o.get('客户','N/A')}): "
                f"发货条件={'✅' if o['满足发货条件'] else '❌'}, "
                f"可加急={'✅' if o['可加急发货'] else '❌'}"
            )
        return "\n".join(lines)

    @tool
    def get_order_detail(order_id: str) -> str:
        """查询指定订单的详细信息（CRM原始数据+本体推理结果）。
        参数 order_id: CRM订单ID，如 ORD-2024-001"""
        info = onto_mgr.get_order(order_id)
        if info is None:
            return f"未找到订单 {order_id}（请使用CRM订单ID，如 ORD-2024-001）"
        lines = [f"订单详情 - {info['订单ID']}:"]
        for k, v in info.items():
            if isinstance(v, dict):
                lines.append(f"  {k}:")
                for sk, sv in v.items():
                    lines.append(f"    {sk}: {sv}")
            else:
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    @tool
    def check_order_eligibility(order_id: str) -> str:
        """通过本体推理器判定订单是否满足发货条件、是否可加急。
        数据从CRM获取，规则由本体定义，判定由推理器完成。
        参数 order_id: CRM订单ID，如 ORD-2024-001"""
        info = onto_mgr.get_order(order_id)
        if info is None:
            return f"未找到订单 {order_id}"
        return (
            f"订单 {info['订单ID']} 的推理结果（数据来自CRM，规则来自本体）：\n"
            f"  客户: {info.get('客户','N/A')} (等级:{info.get('客户等级','N/A')})\n"
            f"  推理后类型: {info['推理后类型']}\n"
            f"  满足发货条件 (ReadyToShipOrder): {'是' if info['满足发货条件'] else '否'}\n"
            f"  可加急发货 (ExpediteEligibleOrder): {'是' if info['可加急发货'] else '否'}"
        )

    @tool
    def create_new_order(
        customer_id: str,
        required_qty: int,
        available_qty: int,
        qc_passed: bool,
    ) -> str:
        """在CRM中创建新订单，自动同步到本体并推理分类。
        参数:
          customer_id: CRM客户ID，如 C001, C002, C003, C004
          required_qty: 需求数量
          available_qty: 库存可用数量
          qc_passed: 质检是否通过"""
        info = onto_mgr.create_order(
            customer_id, required_qty, available_qty, qc_passed
        )
        if info is None:
            return "创建失败"
        return (
            f"新订单已在CRM创建并完成本体推理分类：\n"
            f"  订单ID: {info['订单ID']}\n"
            f"  客户: {info.get('客户','N/A')} (等级:{info.get('客户等级','N/A')})\n"
            f"  推理后类型: {info['推理后类型']}\n"
            f"  满足发货条件: {'是' if info['满足发货条件'] else '否'}\n"
            f"  可加急发货: {'是' if info['可加急发货'] else '否'}"
        )

    @tool
    def get_business_rules() -> str:
        """查看本体中定义的业务规则（TBox）和CRM数据映射规则"""
        return onto_mgr.get_business_rules() + "\n\n" + onto_mgr.get_data_mapping_info()

    tools = [
        list_orders,
        get_order_detail,
        check_order_eligibility,
        create_new_order,
        get_business_rules,
    ]

    system_prompt = (
        "你是一个订单管理助手，背后接入了 CRM 系统和基于 OWL 本体的业务推理引擎。\n\n"
        "架构说明：\n"
        "- 订单/客户/库存数据从 CRM 系统获取\n"
        "- 业务规则（发货条件、加急资格）由本体 TBox 定义\n"
        "- 数据通过映射层注入本体后，由推理器自动分类\n\n"
        "你的能力：\n"
        "- 查询CRM中的订单信息及本体推理后的业务分类\n"
        "- 在CRM中创建新订单，自动触发本体推理\n"
        "- 解释业务规则和数据映射关系\n\n"
        "系统中的客户：C001(张总集团/VIP), C002(李氏贸易/普通), C003(王氏科技/VIP), C004(赵记商行/普通)\n\n"
        "重要原则：\n"
        "- 所有业务判定必须通过工具调用本体推理器获得，不要自行猜测\n"
        "- 向用户说明数据来自CRM、规则来自本体\n"
        "- 用简洁清晰的中文回答"
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    return create_agent(llm, tools, system_prompt=system_prompt)


# ── 离线演示模式 ───────────────────────────────────────────


def _demo_mode(onto_mgr: OntologyManager):
    """无 LLM 时直接展示 CRM→本体→推理 的完整流程。"""
    print("=" * 60)
    print("  离线演示：CRM → 本体映射 → 推理器分类")
    print("=" * 60)

    print("\n📋 业务规则（本体 TBox）:")
    print(onto_mgr.get_business_rules())

    print("\n🔗 数据映射规则:")
    print(onto_mgr.get_data_mapping_info())

    print("\n📊 CRM 客户数据:")
    for c in onto_mgr.crm.get_all_customers():
        print(f"  {c.customer_id}: {c.name} (等级: {c.tier})")

    print("\n📦 CRM 订单 → 本体推理结果:")
    for o in onto_mgr.list_orders():
        alloc = o.get("库存分配", {})
        print(
            f"  {o['订单ID']} | 客户:{o.get('客户','N/A')}({o.get('客户等级','')}) | "
            f"CRM状态:{o['CRM状态']} | "
            f"质检:{'✅' if alloc.get('质检通过') else '❌'} | "
            f"发货:{'✅' if o['满足发货条件'] else '❌'} | "
            f"加急:{'✅' if o['可加急发货'] else '❌'}"
        )

    print("\n➕ 模拟在CRM创建新订单 (客户C001/VIP, 数量50, 库存60, 质检通过):")
    r = onto_mgr.create_order("C001", 50, 60, True)
    print(
        f"  {r['订单ID']} | 客户:{r.get('客户','N/A')}({r.get('客户等级','')}) | "
        f"发货:{'✅' if r['满足发货条件'] else '❌'} | "
        f"加急:{'✅' if r['可加急发货'] else '❌'}"
    )

    print("\n➕ 模拟在CRM创建新订单 (客户C002/普通, 数量30, 库存40, 质检未通过):")
    r2 = onto_mgr.create_order("C002", 30, 40, False)
    print(
        f"  {r2['订单ID']} | 客户:{r2.get('客户','N/A')}({r2.get('客户等级','')}) | "
        f"发货:{'✅' if r2['满足发货条件'] else '❌'} | "
        f"加急:{'✅' if r2['可加急发货'] else '❌'}"
    )

    print("\n" + "=" * 60)
    print("💡 架构价值:")
    print("  - 本体只定义规则 (TBox)，不存储业务数据")
    print("  - 数据从 CRM 系统获取，通过映射层注入本体")
    print("  - 推理器基于规则自动分类，与数据来源解耦")
    print("  - 规则变更改本体，数据源变更改映射层")
    print("=" * 60)


# ── 主入口 ─────────────────────────────────────────────────


def main():
    ontology_path = Path(__file__).with_name("demo-ontology.owx")

    # 初始化模拟 CRM 系统
    crm = CRMSystem()
    print("✅ CRM 系统已初始化（模拟），加载了",
          f"{len(crm.get_all_customers())}个客户, {len(crm.get_all_orders())}个订单")

    # 加载本体 TBox，从 CRM 映射数据到 ABox
    onto_mgr = OntologyManager(ontology_path, crm)
    print("✅ 本体 TBox 已加载，CRM 数据已映射为 ABox 个体\n")

    if not os.environ.get("OPENAI_API_KEY"):
        print("未设置 OPENAI_API_KEY，进入离线演示模式\n")
        _demo_mode(onto_mgr)
        return

    agent = build_agent(onto_mgr)

    print("=== 本体增强订单管理 Agent（CRM 集成版）===")
    print("输入问题与 Agent 对话，输入 q 退出\n")
    print("示例问题:")
    print("  - 系统里有哪些订单？哪些可以加急？")
    print("  - ORD-2024-001 能不能加急发货？为什么？")
    print("  - 帮客户C003创建一个新订单，数量100，库存120，质检已通过")
    print("  - 解释一下业务规则和数据映射关系")
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
