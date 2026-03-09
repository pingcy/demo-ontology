# demo-ontology

一个最小可运行的本体推理示例，使用 `owlready2` 对订单场景进行规则推断与结果校验。

## 项目内容

- `demo-ontology.owx`：本体文件
- `restaurant.rdf`：示例 RDF 数据
- `test_reasoning.py`：推理测试脚本（含固定订单与新订单测试）

## 快速开始

1. 安装依赖

```bash
pip install owlready2
```

2. 运行测试

```bash
python test_reasoning.py
```

## 你会看到的结果

- 推理前/推理后的订单类型变化
- 固定订单校验结果（是否属于 `ReadyToShipOrder`、`ExpediteEligibleOrder`）
- 新订单推理与校验结果（同样校验是否属于上述两个条件类）

示例（节选）：

```text
=== 校验结果 ===
✅ order_A1025 属于 ReadyToShipOrder
✅ order_A1025 属于 ExpediteEligibleOrder
✅ order_A1024 不属于 ReadyToShipOrder
✅ order_A1024 不属于 ExpediteEligibleOrder

=== 新订单校验结果 ===
✅ order_NEW_xxxxxxxx 属于 ReadyToShipOrder
✅ order_NEW_xxxxxxxx 属于 ExpediteEligibleOrder
```

## 说明

该仓库偏向教学/原型验证。在企业生产环境中，通常会将本体与实例数据部署在图数据库中，并通过 SPARQL + 内置推理能力获取结果。