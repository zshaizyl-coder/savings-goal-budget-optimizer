# FastAPI 接口草案

## 1. 通用约定

- 基础路径：`/api/v1`。
- 内容类型：JSON；文件导入使用 `multipart/form-data`。
- 日期：`YYYY-MM-DD`；时间：带时区的 ISO 8601，服务端统一转 UTC。
- 金额：整数最小货币单位，字段名以 `_minor` 结尾；V1 币种为 `CNY`。
- 分页：`page`、`page_size`，返回 `items/total/page/page_size`。
- API 不返回真实交易对象、账号、银行卡号、完整备注或源文件路径。
- 数学上不可行的目标是合法业务结果，返回 HTTP 200 和 `feasible: false`；格式错误、缺字段或违反约束才返回 422。

统一错误结构：

```json
{
  "error": {
    "code": "INVALID_WEEK_START",
    "message": "week_start 必须是用户时区中的周一",
    "details": {"field": "week_start"},
    "request_id": "anonymous-request-id"
  }
}
```

建议错误码：

- `UNSUPPORTED_SOURCE_FORMAT`
- `MISSING_REQUIRED_COLUMNS`
- `DUPLICATE_FILE`
- `IMPORT_NOT_READY`
- `TRANSACTION_NOT_FOUND`
- `INVALID_CATEGORY`
- `INVALID_MONEY_VALUE`
- `INVALID_DATE_RANGE`
- `INVALID_WEEK_START`
- `INSUFFICIENT_HISTORY`
- `GOAL_NOT_FOUND`
- `CONSTRAINT_CONFLICT`
- `PLAN_VERSION_CONFLICT`

## 2. 导入

### POST /api/v1/import-batches

创建一次账单导入。V1 同步完成也返回批次资源；未来异步化时不改变资源模型。

请求：`multipart/form-data`

- `file`：私有账单文件；
- `source`：`wechat`，未来可为 `alipay/bank`；
- `source_timezone`：默认用户时区；
- `force_retry`：默认 false，只允许显式重试相同文件。

响应核心字段：

```json
{
  "id": 12,
  "source": "wechat",
  "status": "completed",
  "raw_count": 2445,
  "success_count": 2445,
  "failed_count": 0,
  "duplicate_count": 0,
  "cross_source_candidate_count": 0,
  "period_start": "2025-10-01T04:39:24Z",
  "period_end": "2026-10-01T15:22:35Z",
  "warnings": []
}
```

错误：415 不支持格式；422 缺少字段或来源不匹配；409 重复文件；500 仅返回匿名错误码。

### GET /api/v1/import-batches/{batch_id}

返回批次状态和聚合计数，不返回本地路径或原始行。

### GET /api/v1/import-batches

按来源、状态、时间分页查询导入历史。

## 3. 交易与人工确认

### GET /api/v1/transactions

过滤参数：`date_from`、`date_to`、`source`、`direction`、`flow_type`、`category_id`、`is_abnormal`、分页。

返回标准字段、系统建议类别和用户类别。默认不返回任何商户/账号明细。

### PATCH /api/v1/transactions/{transaction_id}/category

请求：

```json
{"category_id": 3}
```

返回 `suggested_category_id`、`user_category_id`、`effective_category_id` 和更新时间。404 表示交易或类别不存在；403 表示不属于当前用户。

### GET /api/v1/transaction-links

查询 `pending` 的跨源重复、内部转账或退款候选，返回金额、时间差、来源和证据代码，不返回原始备注。

### POST /api/v1/transaction-links/{link_id}/resolve

请求：

```json
{"resolution": "confirmed"}
```

确认时由服务事务更新链接及相关交易。重复提交必须幂等。

## 4. 分析

### GET /api/v1/analytics/weekly

参数：日期范围、类别、是否包含统计异常、口径版本。

响应：`week_start`、消费毛额、退款额、净额、交易数、完整周标记。所有金额为分。

### GET /api/v1/analytics/monthly

响应自然月毛额、退款、净额和完整月标记。服务层负责跨时区边界。

### GET /api/v1/analytics/categories

返回有效类别、金额、频次、占比基点、活跃周和波动指标，并标注自动/人工分类覆盖率。

### GET /api/v1/analytics/data-quality

返回数据源覆盖、日期范围、失败/重复记录、未分类比例和待确认匹配数量。

## 5. 预测

### POST /api/v1/prediction-runs

请求：

```json
{
  "model_name": "moving_average_4",
  "horizon_weeks": 2,
  "target_keys": ["total", "category:food", "category:shopping"],
  "data_cutoff": "2026-10-02T00:00:00Z"
}
```

服务层查询并聚合历史后调用预测器；预测器不接收用户 ID、数据库会话或 HTTP 对象。

响应：运行 ID、模型版本、训练范围、数据截止点、状态及周度预测项。

错误：422 非法跨度；409 数据截止点早于必要历史；422/业务错误 `INSUFFICIENT_HISTORY`。

### GET /api/v1/prediction-runs/{run_id}

返回运行元数据、总量/类别周预测和关联评价。

### GET /api/v1/model-evaluations

按模型、目标序列、预测跨度查询 MAE、RMSE、百分比指标和异常周 MAE。

## 6. 储蓄目标与约束

### POST /api/v1/financial-snapshots

请求：

```json
{
  "as_of_date": "2026-10-12",
  "available_savings_minor": 5000000,
  "currency": "CNY",
  "source": "user_input"
}
```

### POST /api/v1/goals

请求：

```json
{
  "baseline_snapshot_id": 8,
  "start_date": "2026-10-12",
  "target_date": "2027-10-12",
  "target_savings_minor": 10000000,
  "income_assumptions": [
    {
      "frequency": "monthly",
      "amount_minor": 800000,
      "start_date": "2026-10-12",
      "end_date": "2027-10-12",
      "expected_day": 10
    }
  ]
}
```

返回目标、基准快照和收入假设。422 用于日期、币种或金额格式错误。

### PATCH /api/v1/goals/{goal_id}

只修改目标金额、日期或状态；每次修改递增版本并创建 `goal_change` 财务事件。已生成的旧预算不变。

### PUT /api/v1/goals/{goal_id}/constraints

请求为类别约束数组：

```json
{
  "constraints": [
    {
      "category_id": 1,
      "minimum_budget_minor": 30000,
      "maximum_budget_minor": null,
      "is_fixed": false,
      "adjustability_bps": 5000,
      "effective_from": "2026-10-12",
      "effective_to": "2027-10-12"
    }
  ]
}
```

冲突约束返回 422 `CONSTRAINT_CONFLICT`。

## 7. 预算生成

### POST /api/v1/budget-plans

请求：

```json
{
  "goal_id": 4,
  "prediction_run_id": 21,
  "financial_snapshot_id": 8,
  "strategy": "personalized",
  "adjustment_mode": "balanced",
  "plan_start_week": "2026-10-12"
}
```

可行响应：

```json
{
  "plan_id": 30,
  "version": 1,
  "feasible": true,
  "expected_final_savings_minor": 10125000,
  "items": [
    {
      "week_start": "2026-10-12",
      "category_id": 1,
      "natural_spend_minor": 12000,
      "recommended_budget_minor": 10000,
      "adjustment_minor": -2000,
      "explanation_code": "BALANCED_REDUCTION"
    }
  ],
  "explanations": ["MINIMUM_CONSTRAINT_RESPECTED"]
}
```

不可行响应仍为 HTTP 200：

```json
{
  "plan_id": 31,
  "version": 1,
  "feasible": false,
  "reason_code": "MINIMUM_SPEND_EXCEEDS_AVAILABLE_FUNDS",
  "maximum_achievable_target_minor": 8600000,
  "required_additional_income_minor": 1400000,
  "recommended_deadline": "2028-01-15",
  "adjustable_constraints": [
    {"category_id": 2, "minimum_relaxation_minor": 5000}
  ],
  "items": []
}
```

无论可行与否都创建可审计的 `budget_plans` 记录。参数格式错误返回 422；目标不存在返回 404；版本并发冲突返回 409。

### GET /api/v1/budget-plans/{plan_id}

返回计划快照、版本链引用、可行性和明细。

### GET /api/v1/goals/{goal_id}/budget-plans

返回目标的全部计划版本和当前版本标记。

## 8. 财务事件与滚动再规划

### POST /api/v1/financial-events

请求：

```json
{
  "goal_id": 4,
  "event_type": "unexpected_expense",
  "amount_minor": 200000,
  "event_date": "2026-11-03",
  "impact_scope": "actual_spend",
  "reason_code": "USER_CONFIRMED_UNPLANNED",
  "safe_note": null
}
```

### POST /api/v1/budget-plans/{plan_id}/replans

请求：

```json
{
  "event_ids": [15],
  "financial_snapshot_id": 9,
  "actuals_cutoff": "2026-11-09T00:00:00Z",
  "adjustment_mode": "goal_priority",
  "strategy": "personalized"
}
```

响应与预算生成一致，并额外返回：

- `parent_plan_id`；
- `trigger_event_ids`；
- `locked_through_week`；
- 旧计划与新计划未来部分的聚合差异。

服务必须验证：已结束周完全锁定；输入事件属于同一用户/目标；旧计划仍是预期版本。冲突返回 409 `PLAN_VERSION_CONFLICT`。

## 9. 用户接口边界

V1 最多提供：

- `POST /api/v1/auth/login`
- `GET /api/v1/users/me`

账号密码使用安全哈希；不实现 OAuth、第三方登录、RBAC、组织和管理员体系。单机演示模式可配置一个本地用户，但所有业务表仍保留 `user_id`，避免以后重构数据归属。
