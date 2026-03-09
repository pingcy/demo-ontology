"""本体增强的订单管理 Agent
================================
演示如何用本体 (Ontology) + 推理器增强 LLM Agent 的业务理解能力。

架构:
  用户 (自然语言) → LangChain Agent → Tools → 本体 (owlready2 + HermiT)

核心价值:
  - 业务规则由本体形式化定义，推理器自动完成实例分类
  - Agent 不需要硬编码规则，也不依赖 LLM 猜测
  - 规则变更只需修改本体，无需改代码

使用方式:
  export OPENAI_API_KEY=sk-...
  python test_agent.py

  若未设置 API Key，自动进入离线演示模式。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

from owlready2 import ThingClass, get_ontology, sync_reasoner


# ── 本体管理层 ──────────────────────────────────────────────


class OntologyManager:
    """封装本体加载、推理、查询操作。"""

    def __init__(self, ontology_path: str | Path):
        path = Path(ontology_path)
        if not path.exists():
            raise FileNotFoundError(f"本体文件未找到: {path}")
        self.onto = get_ontology(path.resolve().as_uri()).load()
        self._reasoned = False

    def ensure_reasoned(self):
        if not self._reasoned:
            with self.onto:
                sync_reasoner(infer_property_values=True, debug=0)
            self._reasoned = True

    def invalidate(self):
        self._reasoned = False

    # ── 内部工具 ──

    def _cls(self, name: str):
        return self.onto.search_one(iri=f"*#{name}")

    def _class_names(self, entity) -> list[str]:
        return sorted({c.name for c in entity.is_a if isinstance(c, ThingClass)})

    def _order_info(self, order) -> dict:
        ready_cls = self._cls("ReadyToShipOrder")
        expedite_cls = self._cls("ExpediteEligibleOrder")
        info = {
            "订单ID": order.name,
            "推理后类型": self._class_names(order),
            "需求数量": order.requiredQty[0] if order.requiredQty else None,
        }
        if order.hasCustomer:
            cust = order.hasCustomer[0]
            info["客户"] = cust.name
            info["客户类型"] = self._class_names(cust)
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
        customer_type: str,
        required_qty: int,
        available_qty: int,
        qc_passed: bool,
    ) -> dict:
        order_cls = self._cls("Order")
        alloc_cls = self._cls("InventoryAllocation")
        customer = self.onto.search_one(
            iri=f"*#customer_{'vip' if customer_type.upper() == 'VIP' else 'normal'}"
        )

        suffix = uuid4().hex[:8]
        alloc = alloc_cls(f"alloc_NEW_{suffix}")
        alloc.availableQty = [available_qty]
        alloc.qcPassed = [qc_passed]

        order = order_cls(f"order_NEW_{suffix}")
        order.hasAllocation = [alloc]
        order.hasCustomer = [customer]
        order.requiredQty = [required_qty]

        self.invalidate()
        self.ensure_reasoned()
        return self._order_info(order)

    def get_business_rules(self) -> str:
        return (
            "本体定义的业务规则：\n"
            "1. ReadyToShipOrder（满足发货条件的订单）：\n"
            "   Order 且 hasAllocation 关联的 InventoryAllocation 的 qcPassed = true\n"
            "   → 订单关联的库存已通过质检即可发货\n\n"
            "2. ExpediteEligibleOrder（可加急发货的订单）：\n"
            "   ReadyToShipOrder 且 hasCustomer 关联的是 VIPCustomer\n"
            "   → 满足发货条件 + VIP客户 → 自动获得加急资格\n\n"
            "3. VIPCustomer 是 Customer 的子类\n\n"
            "这些规则由本体形式化定义，推理器自动完成分类，不需要硬编码。"
        )


# ── LangChain Agent ────────────────────────────────────────


def build_agent(onto_mgr: OntologyManager):
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langchain.agents import create_agent

    @tool
    def list_orders() -> str:
        """列出系统中所有订单及其状态（包括推理后的业务分类）"""
        orders = onto_mgr.list_orders()
        if not orders:
            return "系统中暂无订单。"
        lines = []
        for o in orders:
            lines.append(
                f"- {o['订单ID']}: 类型={o['推理后类型']}, "
                f"发货条件={'✅' if o['满足发货条件'] else '❌'}, "
                f"可加急={'✅' if o['可加急发货'] else '❌'}"
            )
        return "\n".join(lines)

    @tool
    def get_order_detail(order_id: str) -> str:
        """查询指定订单的详细信息，包括客户、库存、质检状态及推理结果。
        参数 order_id: 订单ID，如 order_A1025"""
        info = onto_mgr.get_order(order_id)
        if info is None:
            return f"未找到订单 {order_id}"
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
        这是基于本体规则的精确判定，不是猜测。
        参数 order_id: 订单ID，如 order_A1025"""
        info = onto_mgr.get_order(order_id)
        if info is None:
            return f"未找到订单 {order_id}"
        return (
            f"订单 {info['订单ID']} 的推理结果（基于本体规则）：\n"
            f"  推理后类型: {info['推理后类型']}\n"
            f"  满足发货条件 (ReadyToShipOrder): {'是' if info['满足发货条件'] else '否'}\n"
            f"  可加急发货 (ExpediteEligibleOrder): {'是' if info['可加急发货'] else '否'}"
        )

    @tool
    def create_new_order(
        customer_type: str,
        required_qty: int,
        available_qty: int,
        qc_passed: bool,
    ) -> str:
        """创建新订单并通过本体推理器自动分类。
        参数:
          customer_type: 客户类型，VIP 或 normal
          required_qty: 需求数量
          available_qty: 库存可用数量
          qc_passed: 质检是否通过"""
        info = onto_mgr.create_order(
            customer_type, required_qty, available_qty, qc_passed
        )
        return (
            f"新订单已创建并完成推理分类：\n"
            f"  订单ID: {info['订单ID']}\n"
            f"  推理后类型: {info['推理后类型']}\n"
            f"  满足发货条件: {'是' if info['满足发货条件'] else '否'}\n"
            f"  可加急发货: {'是' if info['可加急发货'] else '否'}"
        )

    @tool
    def get_business_rules() -> str:
        """查看本体中定义的业务规则（发货条件、加急资格的判定逻辑）"""
        return onto_mgr.get_business_rules()

    tools = [
        list_orders,
        get_order_detail,
        check_order_eligibility,
        create_new_order,
        get_business_rules,
    ]

    system_prompt = (
        "你是一个订单管理助手，背后接入了基于 OWL 本体的业务知识库和推理引擎。\n\n"
        "你的能力：\n"
        "- 查询订单信息、判定订单业务分类（发货条件、加急资格）\n"
        "- 创建新订单并自动推理其业务类别\n"
        "- 解释业务规则的定义\n\n"
        "重要原则：\n"
        "- 所有业务判定必须通过工具调用本体推理器获得，不要自行猜测\n"
        "- 向用户解释结果时，说明判定依据来自本体规则\n"
        "- 用简洁清晰的中文回答"
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    return create_agent(llm, tools, system_prompt=system_prompt)


# ── 离线演示模式 ───────────────────────────────────────────


def _demo_mode(onto_mgr: OntologyManager):
    """无 LLM 时直接展示本体推理工具的输出，证明本体价值。"""
    print("=" * 60)
    print("  离线演示：本体推理工具的直接调用结果")
    print("=" * 60)

    print("\n📋 业务规则（来自本体定义）:")
    print(onto_mgr.get_business_rules())

    print("\n📦 所有订单:")
    for o in onto_mgr.list_orders():
        print(
            f"  {o['订单ID']}: 类型={o['推理后类型']}, "
            f"发货={'✅' if o['满足发货条件'] else '❌'}, "
            f"加急={'✅' if o['可加急发货'] else '❌'}"
        )

    print("\n🔍 订单 order_A1025 详情:")
    detail = onto_mgr.get_order("order_A1025")
    if detail:
        for k, v in detail.items():
            if isinstance(v, dict):
                print(f"  {k}:")
                for sk, sv in v.items():
                    print(f"    {sk}: {sv}")
            else:
                print(f"  {k}: {v}")

    print("\n➕ 创建新订单 (VIP客户, 数量5, 库存10, 质检通过):")
    r = onto_mgr.create_order("VIP", 5, 10, True)
    print(
        f"  {r['订单ID']}: 类型={r['推理后类型']}, "
        f"发货={'✅' if r['满足发货条件'] else '❌'}, "
        f"加急={'✅' if r['可加急发货'] else '❌'}"
    )

    print("\n➕ 创建新订单 (普通客户, 数量5, 库存10, 质检未通过):")
    r2 = onto_mgr.create_order("normal", 5, 10, False)
    print(
        f"  {r2['订单ID']}: 类型={r2['推理后类型']}, "
        f"发货={'✅' if r2['满足发货条件'] else '❌'}, "
        f"加急={'✅' if r2['可加急发货'] else '❌'}"
    )

    print("\n" + "=" * 60)
    print("💡 本体的价值:")
    print("  - 业务规则由本体形式化定义，推理器自动分类")
    print("  - Agent 不需要硬编码规则，也不依赖 LLM 猜测")
    print("  - 规则变更只需修改本体，无需改代码")
    print("=" * 60)


# ── 主入口 ─────────────────────────────────────────────────


def main():
    ontology_path = Path(__file__).with_name("demo-ontology.owx")
    onto_mgr = OntologyManager(ontology_path)

    if not os.environ.get("OPENAI_API_KEY"):
        print("未设置 OPENAI_API_KEY，进入离线演示模式（仅展示工具返回结果）\n")
        _demo_mode(onto_mgr)
        return

    agent = build_agent(onto_mgr)

    print("=== 本体增强订单管理 Agent ===")
    print("输入问题与 Agent 对话，输入 q 退出\n")
    print("示例问题:")
    print("  - 系统里有哪些订单？")
    print("  - 订单A1025能不能加急发货？为什么？")
    print("  - 帮我创建一个VIP客户的新订单，数量10，库存20，质检已通过")
    print("  - 解释一下加急发货的判定规则")
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
