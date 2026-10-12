# 统一数据模型设计

## 1. 设计原则

统一数据模型服务于微信、支付宝和银行卡，不把某一来源的列名变成数据库结构。源解析器负责转换，后续分析、预测和预算只认识领域模型。

核心原则：

- 一张统一 `transactions` 表，不为每个平台建立独立交易表。
- 金额以整数最小货币单位保存，不使用普通 `float`。
- 资金流类型与收支方向分离；退款、转账和账户内部移动有明确语义。
- 自动分类与用户分类同时保留，用户确认优先。
- 真实交易对象和账号默认不长期保存。
- 可计算的周编号和月份不在交易表冗余存储。

## 2. Transaction 字段分级

### 2.1 必须持久化字段

| 字段 | 类型/示例 | 说明 |
| --- | --- | --- |
| `id` | bigint/integer | 内部主键，不使用源交易号 |
| `user_id` | FK | 数据所有者 |
| `import_batch_id` | FK | 导入血缘 |
| `source` | enum | `wechat`、`alipay`、`bank` |
| `source_record_fingerprint` | HMAC 字符串 | 同源幂等；不可逆，不保存真实交易号 |
| `occurred_at_utc` | UTC datetime | 规范交易时间 |
| `amount_minor` | bigint/integer | 绝对金额，单位分，必须非负 |
| `currency` | char(3) | V1 为 `CNY` |
| `direction` | enum | `inflow`、`outflow`、`neutral` |
| `flow_type` | enum | 消费、收入候选、消费退款、转账、内部转账、红包、充值/提现、其他 |
| `normalized_status` | enum | `completed`、`refunded`、`partial_refund`、`reversed`、`pending`、`failed`、`unknown` |
| `source_type_code` | 短代码 | 解析器产生的通用源类型，不保存可能含商户名的原文 |
| `suggested_category_id` | nullable FK | 自动映射类别 |
| `user_category_id` | nullable FK | 用户确认或修改类别，优先级更高 |
| `related_transaction_id` | nullable self FK | 已确认的退款与原交易关联 |
| `counterparty_fingerprint` | nullable HMAC | 跨源匹配的不可逆辅助线索 |
| `is_abnormal` | bool | 统计异常标志，不等于计划外或不合理 |
| `created_at` | UTC datetime | 入库时间 |

有效类别按 `COALESCE(user_category_id, suggested_category_id)` 计算。数据库不保存单独 `effective_category_id`，避免三份类别状态不一致。

### 2.2 可选持久化字段

| 字段 | 使用条件 |
| --- | --- |
| `original_category` | 仅当来源提供稳定、非敏感的分类标签时保存；微信含商户名的“退款类型”必须先归一化 |
| `classification_version` | 记录规则或模型版本，支持重新分类和论文复现 |
| `classification_confidence_bps` | 自动分类存在可解释置信度时使用，0—10000；规则分类可为空 |
| `canonical_transaction_id` | 人工确认跨源重复后指向保留记录；未确认候选不填写 |
| `source_account_fingerprint` | 仅在内部转账匹配需要时保存不可逆 HMAC |

### 2.3 仅导入阶段使用

- Excel 工作表名、表头行位置和源列索引；
- 原始交易对象、商品/摘要、支付方式、备注；
- 原始交易单号、商户单号、银行流水号；
- 原始状态文本及其中携带的退款金额；
- 本地临时文件路径；
- 用于分类和匹配的临时关键词特征。

这些值可以在进程内用于解析、分类和 HMAC 生成，但不得进入普通日志。导入结束后只保留标准字段、指纹和聚合错误摘要。

### 2.4 不建议长期保存的敏感字段

- 真实姓名和交易对象；
- 完整商户名称或可识别个人的收款名称；
- 微信号、支付宝账号、银行账号和银行卡号；
- 完整商品描述和详细备注；
- 原始账单文件完整路径；
- 可逆加密但业务不需要的源交易明细。

若后续 Web 明确需要逐笔人工纠错，可增加“用户主动选择的短期私有明细库”，但必须单独授权、加密并设置删除策略；不作为当前 V1 默认设计。

## 3. 资金流语义

`direction` 只描述钱的方向，`flow_type` 描述业务含义。两者不能互相替代。

| 业务情况 | direction | flow_type | 是否进入消费 |
| --- | --- | --- | --- |
| 商户消费 | outflow | consumption | 是 |
| 消费退款 | inflow | expense_refund | 作为负向冲减 |
| 工资或其他确认收入 | inflow | income | 进入收入 |
| 未确认微信入账 | inflow | income_candidate | 需用户确认 |
| 转账 | inflow/outflow | transfer | 默认排除 |
| 确认本人账户间转移 | inflow/outflow | internal_transfer | 排除 |
| 充值/提现 | neutral 或按源方向 | balance_transfer | 排除 |
| 红包 | inflow/outflow | red_packet | 单独统计，默认排除日常消费 |

退款不用 `is_refund` 与 `refund_amount` 两个冗余字段表示。退款本身是一条交易，金额在 `amount_minor`，语义由 `flow_type=expense_refund` 表示；匹配成功后通过 `related_transaction_id` 关联原消费。

## 4. 金额模型

最终选择：**整数“分”**。

理由：

- SQLite 对 `DECIMAL/NUMERIC` 的严格定点语义不如服务器数据库一致；整数可完全避免二进制浮点误差。
- 对人民币账单、预算和储蓄目标，两位小数足够。
- 可直接迁移到 MySQL `BIGINT`，不改变业务语义。
- 求和、约束检查和版本比较都是精确整数运算。

规范：

- 所有交易、收入、预算、储蓄、约束和事件金额均用 `*_minor`。
- 源金额用 Python `Decimal` 解析，按币种小数位量化后转换为整数。
- API 和数据库禁止普通浮点货币字段。
- 模型评价中的 MAE/RMSE 保存为四舍五入后的整数分；百分比指标保存为基点 `*_bps`。

## 5. 时间模型

- `occurred_at_utc`、`imported_at`、`created_at`、`generated_at` 统一为 UTC。
- 用户保存 `timezone`，V1 默认 `Asia/Shanghai`。
- 批次保存 `source_timezone`，解析无时区账单时使用。
- 周度业务对象使用 `week_start` 本地日期；周固定为周一至周日。
- 交易表不保存 `week_number` 或 `month`。查询或分析时通过统一 `CalendarPolicy` 计算。
- 预算项、周预测需要稳定引用业务周期，因此保存 `week_start`，而不是只保存 ISO 周号。

## 6. 统一分类体系

根据真实 EDA 和预算需求，V1 初始消费类别保持适中：

1. 餐饮
2. 购物
3. 交通
4. 居住与缴费
5. 医疗健康
6. 教育
7. 通讯与数字服务
8. 娱乐
9. 人情往来
10. 其他消费

“固定支出”“不可调整”不是消费类别，而是预算约束属性。收入、退款和内部转账由资金流类型表达，不混入消费类别。

映射优先级：

```text
用户对该交易的确认类别
    > 用户级安全指纹映射（后续增强）
    > 来源类别精确映射
    > 版本化通用规则/关键词映射
    > 其他消费
```

`category_mappings` 只保存来源、非敏感源类型代码、目标类别、优先级和版本。需要读取商户/描述的关键词规则在导入进程内执行并版本化，不把真实关键词命中内容存入数据库。

## 7. ImportBatch

`import_batches` 表示一次导入尝试，至少保存：

- 用户、来源平台、文件格式、来源时区；
- 文件内容 SHA-256 指纹，不保存完整文件路径；
- 解析器名称和版本；
- 原始、成功、失败、同源重复、跨源候选记录数；
- 文件内交易起止时间；
- `pending/running/completed/partial/failed/duplicate_file` 状态；
- 创建、开始和完成时间；
- 只含错误代码与计数的聚合错误摘要。

重复导入分两层：

1. 文件指纹相同：提示重复文件，默认不再次写入。
2. 单条来源记录指纹相同：由 `(user_id, source, source_record_fingerprint)` 唯一约束保证幂等。

## 8. 跨源重复与内部转账

阶段 3 只定义候选机制，不实现最终算法。

候选匹配特征：

- 时间差窗口；
- 金额与币种；
- 相反或相同方向；
- 来源组合；
- 归一化交易类型；
- 账户和交易对象 HMAC 指纹；
- 仅在内存中使用的备注特征。

`transaction_links` 保存交易 A、交易 B、关系类型、置信度、状态和人工确认时间：

- `duplicate_candidate`：可能是同一消费的两个来源视图；
- `internal_transfer_candidate`：可能是本人账户间移动；
- `refund_candidate`：可能是退款与原交易；
- `confirmed/rejected/pending`：人工或规则决议。

只有确认后才设置 `canonical_transaction_id`、`related_transaction_id` 或 `flow_type=internal_transfer`。低置信候选不得自动删除或排除。

## 9. 储蓄目标与财务基准

`saving_goals` 保存当前有效目标：开始日期、目标日期、目标金额、状态和版本号，不把不断变化的当前存款直接覆盖进目标。

当前存款使用 `financial_snapshots`：

- `as_of_date`；
- `available_savings_minor`；
- 来源 `user_input/imported/derived`；
- 创建时间。

目标通过 `baseline_snapshot_id` 引用创建时基准。每次滚动再规划可创建新快照，预算计划记录所用快照，从而避免“当前存款”变化后无法复现旧计划。

未来收入使用 `income_assumptions` 表示简单的单次、周度或月度假设及生效区间。V1 不构建工资预测模型。

## 10. 预算约束

`budget_constraints` 按目标和类别保存：

- `minimum_budget_minor`；
- 可选 `maximum_budget_minor`；
- `is_fixed`；
- `adjustability_bps`，范围 0—10000；
- 生效起止日期和版本。

解释：0 表示不可调整，10000 表示算法可在最低/最高约束内充分调整。UI 的低/中/高只是可解释预设值，自定义模式可直接设置；它是优化权重/边界的输入，不声称衡量心理特征。`is_fixed=true` 时要求最低值等于最高值且可调整度为 0。

## 11. 预测、预算与再规划

- `prediction_runs` 保存一次模型运行的模型名称、版本、数据截止点、训练区间、预测跨度和参数版本。
- `weekly_predictions` 一行表示一个目标周和一个目标序列，总量或某一类别通过 `target_key` 区分，不建立固定类别列。
- `budget_plans` 保存版本、父计划、目标快照、策略、调整模式、可行性和建议；触发事件通过关联表保存，可支持一次再规划由多个事件共同触发。
- `budget_plan_items` 一行表示某周某类别的自然预测、推荐预算、调整额和当时适用约束。
- `financial_events` 保存突发支出、临时收入、收入变化、目标变化和约束变化；预算计划通过 `budget_plan_events` 和 `parent_plan_id` 形成可追踪链。
- 不可行预算仍保存 `budget_plan`，`is_feasible=false`，并保存原因代码、最高可达目标、收入缺口或建议日期；不生成虚假的明细预算。

## 12. 阶段 4 已实现字段与规则

首个 ORM/迁移只实现导入链必需实体：User、ImportBatch、Category、CategoryMapping、Transaction 和 TransactionLink。统一 Transaction 已实际承载真实微信数据；未来支付宝和银行卡仍写入同一表。

来源层字段只存在于 `WechatSourceTransaction`：完整交易对象、商品、备注、支付方式和原始交易号在生成 HMAC 与分类后丢弃。持久化交易保留 `amount_minor`、UTC 时间、方向、业务流、来源类型代码、自动/用户类别引用、批次、指纹、退款关系和候选规范交易关系。

真实退款验证表明，来源文件没有可直接证明原消费关系的统一原交易 ID。当前唯一高置信候选才设置 `related_transaction_id`；无法确认的退款仍以独立负向交易保留。该实现比用时间和金额强行一一匹配更保守。

分类质量报告同时输出 `other` 的笔数比例和消费毛额比例，避免只看一个口径误判。用户类别字段已预留但阶段 4 没有实现前端修改功能。
