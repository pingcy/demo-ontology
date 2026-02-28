from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from owlready2 import ThingClass, get_ontology, sync_reasoner


def _class_names(classes: Iterable[object]) -> list[str]:
    names: list[str] = []
    for item in classes:
        if isinstance(item, ThingClass):
            names.append(item.name)
    return sorted(set(names))


def test_new_order_expedite_inference(onto) -> None:
    order_cls = onto.search_one(iri="*#Order")
    alloc_cls = onto.search_one(iri="*#InventoryAllocation")
    vip_customer = onto.search_one(iri="*#customer_vip")
    ready_cls = onto.search_one(iri="*#ReadyToShipOrder")
    expedite_cls = onto.search_one(iri="*#ExpediteEligibleOrder")

    if (
        order_cls is None
        or alloc_cls is None
        or vip_customer is None
        or ready_cls is None
        or expedite_cls is None
    ):
        raise ValueError("缺少新订单推理测试所需的类或个体")

    suffix = uuid4().hex[:8]
    new_alloc = alloc_cls(f"alloc_for_NEW_{suffix}")
    new_alloc.availableQty = [10]
    new_alloc.qcPassed = [True]

    new_order = order_cls(f"order_NEW_{suffix}")
    new_order.hasAllocation = [new_alloc]
    new_order.hasCustomer = [vip_customer]
    new_order.requiredQty = [5]

    print("\n=== 新订单推理前详情 ===")
    print(f"订单ID: {new_order.name}")
    print(f"订单类型(显式): {_class_names(new_order.is_a)}")
    print(f"requiredQty: {new_order.requiredQty[0] if new_order.requiredQty else 'N/A'}")
    print(
        f"客户: {new_order.hasCustomer[0].name if new_order.hasCustomer else 'N/A'}"
    )
    if new_order.hasAllocation:
        allocation = new_order.hasAllocation[0]
        print(f"关联库存分配: {allocation.name}")
        print(
            f"  availableQty: {allocation.availableQty[0] if allocation.availableQty else 'N/A'}"
        )
        print(f"  qcPassed: {allocation.qcPassed[0] if allocation.qcPassed else 'N/A'}")
    else:
        print("关联库存分配: N/A")

    with onto:
        sync_reasoner(infer_property_values=True, debug=0)

    inferred_classes = _class_names(new_order.is_a)
    can_ready = new_order in ready_cls.instances()
    can_expedite = new_order in expedite_cls.instances()

    print("\n=== 新订单加急推理测试 ===")
    print(f"{new_order.name} 推理后类型: {inferred_classes}")
    print(f"是否可加急发货: {'是' if can_expedite else '否'}")

    print("\n=== 新订单校验结果 ===")
    print(
        f"✅ {new_order.name} {'属于' if can_ready else '不属于'} ReadyToShipOrder"
    )
    print(
        f"✅ {new_order.name} {'属于' if can_expedite else '不属于'} ExpediteEligibleOrder"
    )

    if not can_expedite:
        raise AssertionError("新订单应可加急发货，但推理结果为否")


def run_reasoning(ontology_file: Path) -> None:
    if not ontology_file.exists():
        raise FileNotFoundError(f"未找到本体文件: {ontology_file}")

    onto = get_ontology(ontology_file.resolve().as_uri()).load()

    print([cls.name for cls in onto.classes()])

    order_a1024 = onto.search_one(iri="*#order_A1024")
    order_a1025 = onto.search_one(iri="*#order_A1025")

    if order_a1024 is None or order_a1025 is None:
        raise ValueError("未找到测试个体 order_A1024 或 order_A1025")

    print("=== 推理前（显式断言类型）===")
    print(f"order_A1024: {_class_names(order_a1024.is_a)}")
    print(f"order_A1025: {_class_names(order_a1025.is_a)}")

    with onto:
        sync_reasoner(infer_property_values=True, debug=0)

    inferred_1024 = _class_names(order_a1024.is_a)
    inferred_1025 = _class_names(order_a1025.is_a)

    print("\n=== 推理后（含推断类型）===")
    print(f"order_A1024: {inferred_1024}")
    print(f"order_A1025: {inferred_1025}")

    ready_cls = onto.search_one(iri="*#ReadyToShipOrder")
    expedite_cls = onto.search_one(iri="*#ExpediteEligibleOrder")

    if ready_cls is None or expedite_cls is None:
        raise ValueError("未找到类 ReadyToShipOrder 或 ExpediteEligibleOrder")

    checks = {
        "order_A1025 属于 ReadyToShipOrder": order_a1025 in ready_cls.instances(),
        "order_A1025 属于 ExpediteEligibleOrder": order_a1025 in expedite_cls.instances(),
        "order_A1024 不属于 ReadyToShipOrder": order_a1024 not in ready_cls.instances(),
        "order_A1024 不属于 ExpediteEligibleOrder": order_a1024 not in expedite_cls.instances(),
    }

    print("\n=== 校验结果 ===")
    all_passed = True
    for label, passed in checks.items():
        mark = "✅" if passed else "❌"
        print(f"{mark} {label}")
        all_passed = all_passed and passed

    if not all_passed:
        raise AssertionError("推理结果与预期不符，请检查本体或推理器配置")

    test_new_order_expedite_inference(onto)

    print("\n全部校验通过。")


def main() -> None:
    parser = argparse.ArgumentParser(description="使用 Owlready2 对本体执行推理测试")
    parser.add_argument(
        "--ontology",
        type=Path,
        default=Path(__file__).with_name("demo-ontology.owx"),
        help="本体文件路径（默认: 当前目录下 demo-ontology.owx）",
    )
    args = parser.parse_args()
    run_reasoning(args.ontology)


if __name__ == "__main__":
    main()