# 算法模块与稳定接口设计

## 1. 阶段 3 边界

本阶段只定义数据契约、算法边界和版本要求，不实现预测模型、可行性公式、预算目标函数或滚动再规划算法。

所有算法必须满足：

- 输入为领域 DTO 或聚合序列，不接收 FastAPI Request、SQLAlchemy Session 或 Excel 路径。
- 输出为结构化结果，不直接写数据库。
- 业务服务负责读取、调用、事务和持久化。
- 每次运行记录数据截止时间、算法名称和版本。
- 金额边界使用整数分；内部数值计算可以使用浮点，但输出必须按统一规则量化。

## 2. 共用领域契约

```python
MoneyMinor = int

WeeklyActual(
    week_start: date,
    target_key: str,              # total 或 category:<code>
    net_spend_minor: MoneyMinor,
    is_complete_week: bool,
    is_abnormal: bool,
)

WeeklyPredictionItem(
    week_start: date,
    target_key: str,
    predicted_minor: MoneyMinor,
    lower_minor: MoneyMinor | None,
    upper_minor: MoneyMinor | None,
)
```

周起始日必须是用户时区中的周一。算法不自行解释时区，调用前由分析服务应用统一 `CalendarPolicy`。

## 3. 周度预测接口

```python
class WeeklyForecaster(Protocol):
    def forecast(self, request: ForecastRequest) -> ForecastResult: ...

ForecastRequest(
    history: Sequence[WeeklyActual],
    horizon_weeks: int,           # V1 重点 1/2/4
    target_keys: tuple[str, ...],
    data_cutoff_utc: datetime,
    model_name: str,
    model_version: str,
    parameters: Mapping[str, JsonValue],
)

ForecastResult(
    model_name: str,
    model_version: str,
    train_start: date,
    train_end: date,
    data_cutoff_utc: datetime,
    items: tuple[WeeklyPredictionItem, ...],
    warnings: tuple[str, ...],
)
```

约束：

- 历史按周排序且不能包含截止点之后的数据。
- 不完整边界周默认不进入训练。
- 预测总量为首要目标；类别首轮只测试餐饮和购物。
- “其他消费”不作为独立行为序列强行建模。
- 若历史不足，返回结构化 `InsufficientHistory`，不静默改用其他模型。
- 基线和候选模型使用同一接口，服务层可以替换而无需改变 API。

## 4. 模型评价接口

```python
class ForecastEvaluator(Protocol):
    def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...

EvaluationRequest(
    history: Sequence[WeeklyActual],
    forecaster_spec: ForecasterSpec,
    horizon_weeks: int,
    initial_train_weeks: int,
    window_mode: Literal["expanding", "rolling"],
)

EvaluationResult(
    model_name: str,
    model_version: str,
    target_key: str,
    train_range: DateRange,
    test_range: DateRange,
    sample_count: int,
    mae_minor: int,
    rmse_minor: int,
    mape_bps: int | None,
    smape_bps: int | None,
    anomaly_mae_minor: int | None,
)
```

V1 强制比较历史均值、最近 4 周移动平均和简单指数平滑。Holt、低阶 ARIMA、滞后线性回归只有在滚动回测优于基线且稳定时保留。百分比指标遇到零值可以为空，不能用零替代不可计算结果。

## 5. 可行性判断接口

```python
class FeasibilityEvaluator(Protocol):
    def evaluate(self, request: FeasibilityRequest) -> FeasibilityResult: ...

FeasibilityRequest(
    goal: GoalSnapshot,
    financial_snapshot: FinancialSnapshot,
    income_schedule: Sequence[IncomePeriod],
    predictions: Sequence[WeeklyPredictionItem],
    constraints: Sequence[BudgetConstraintSpec],
    planning_weeks: Sequence[date],
)

FeasibilityResult(
    feasible: bool,
    reason_code: str | None,
    maximum_achievable_target_minor: int | None,
    required_additional_income_minor: int | None,
    recommended_deadline: date | None,
    binding_constraints: tuple[ConstraintReference, ...],
    explanations: tuple[str, ...],
)
```

可行性判断必须先于优化。不可行时不得通过突破最低预算、忽略固定支出或伪造收入来继续生成“可行”明细。

## 6. 预算优化接口

```python
class BudgetOptimizer(Protocol):
    strategy_name: str
    algorithm_version: str

    def optimize(self, request: BudgetOptimizationRequest) -> BudgetOptimizationResult: ...

BudgetOptimizationRequest(
    goal: GoalSnapshot,
    financial_snapshot: FinancialSnapshot,
    income_schedule: Sequence[IncomePeriod],
    predictions: Sequence[WeeklyPredictionItem],
    constraints: Sequence[BudgetConstraintSpec],
    feasibility: FeasibilityResult,
    adjustment_mode: Literal[
        "goal_priority", "balanced", "life_stability", "custom"
    ],
    planning_weeks: Sequence[date],
)

BudgetOptimizationResult(
    feasible: bool,
    strategy_name: str,
    algorithm_version: str,
    expected_final_savings_minor: int | None,
    items: tuple[BudgetRecommendation, ...],
    diagnostics: tuple[Diagnostic, ...],
    infeasibility: FeasibilityResult | None,
)

BudgetRecommendation(
    week_start: date,
    category_code: str,
    natural_spend_minor: int,
    recommended_budget_minor: int,
    adjustment_minor: int,
    applied_minimum_minor: int,
    applied_maximum_minor: int | None,
    adjustability_bps: int,
    explanation_code: str,
)
```

同一接口实现三种策略：

- `ProportionalReductionOptimizer`：统一比例削减；
- `HistoricalShareOptimizer`：按历史占比分配；
- `PersonalizedOptimizer`：候选个性化约束优化。

加权平方偏差仍只是候选目标，阶段 7 通过实验和敏感性分析决定。接口不把目标函数写死。

调整度 `adjustability_bps` 的语义：

- 0：不得偏离固定/自然基准（同时受最小最大约束）；
- 数值越大：允许优化器在约束内承担更多调整；
- UI 低/中/高是预设映射，自定义使用明确整数值；
- 它是优化权重或边界输入，不是心理学分数。

## 7. 滚动再规划接口

```python
class BudgetReplanner(Protocol):
    def replan(self, request: ReplanRequest) -> ReplanResult: ...

ReplanRequest(
    previous_plan: BudgetPlanSnapshot,
    locked_actuals: Sequence[WeeklyActual],
    events: Sequence[FinancialEventSpec],
    current_snapshot: FinancialSnapshot,
    remaining_goal: GoalSnapshot,
    current_constraints: Sequence[BudgetConstraintSpec],
    latest_predictions: Sequence[WeeklyPredictionItem],
    next_open_week: date,
    strategy_name: str,
    adjustment_mode: str,
)

ReplanResult(
    parent_plan_id: int,
    new_version: int,
    trigger_event_ids: tuple[int, ...],
    locked_through_week: date,
    optimization_result: BudgetOptimizationResult,
    future_delta_minor: int,
    explanations: tuple[str, ...],
)
```

不变量：

1. `next_open_week` 之前的预算和实际数据完全锁定。
2. 新版本必须指向旧版本，旧版本保留。
3. 事件、快照、目标和约束必须属于同一用户。
4. 重新规划重新执行可行性判断。
5. 收入或目标变化不能被伪装成消费预测变化。

## 8. 分析与算法的数据访问

业务服务负责：

1. 通过仓储按用户和截止点读取标准交易；
2. 生成无隐私的日/周聚合；
3. 构造算法请求 DTO；
4. 调用算法接口；
5. 在事务内保存运行、结果和版本关系。

算法模块不得：

- 自己打开 SQLite；
- 查询 ORM 模型；
- 读取上传文件；
- 根据前端字段名分支；
- 写入日志中的交易明细；
- 修改历史 Transaction 或 BudgetPlan。

## 9. 版本与可复现性

每个结果至少携带：

- `algorithm_name/model_name`；
- `algorithm_version/model_version`；
- 数据截止时间；
- 训练/规划区间；
- 参数版本；
- 分类规则版本；
- 周口径版本；
- 输入目标和约束快照引用。

数据库保存结构化元数据和聚合结果，不保存完整训练数据副本。相同数据截止点、算法版本和参数应能重现同一确定性结果；随机算法必须记录种子，但 V1 不优先使用随机复杂模型。

## 10. 当前未冻结事项

- 个性化优化的最终目标函数和类别权重；
- 大额异常在“习惯基准”中的降权方式；
- 周预测到跨月预算的最终分摊权重；
- 预测区间计算方法；
- SciPy 与 CVXPY 的最终选择。

这些事项只能由后续真实回测、优化实验和敏感性分析确定。

阶段 7 已冻结可行性公式、三种模式、SLSQP 求解、整数分回收规则与两个预算基准方法。完整且与代码一致的公式、参数和实验见 `docs/budget-optimization-model.md`；阶段 3 的协议边界保持向后兼容，仅为结果 DTO 增加了带默认值的诊断字段。

## 11. 阶段 5 冻结的预测输入契约

阶段 6 所有模型必须调用 `build_weekly_forecast_dataset()`，不得自行从交易表重新聚合。数据集版本为 `weekly-forecast-v1`，默认仅包含完整的周一至周日业务周。

目标序列可为总净消费或已通过资格审查的单一类别；当前准入类别只有 `food` 和 `shopping`。特征限定为 1—4 周滞后、过去 2/4 周均值、过去 4 周标准差、月份和 ISO 周序号。所有金额特征先对目标 `shift(1)`，当前周实际值不能进入当前周特征。

退款口径为：总量冲减全部退款；类别只回冲可靠关联退款，未关联退款维持独立桶。总量模型和类别模型是并行实验，类别预测之和不得替代总量预测。收入不属于本数据集。

## 12. 阶段 6 冻结的预测实现

- 总消费正式模型：历史扩展窗口均值，版本 `total-weekly-forecast-v1`；
- 餐饮正式辅助模型：历史扩展窗口中位数，版本 `food-weekly-forecast-v1`；
- 购物正式辅助模型：历史扩展窗口中位数，版本 `shopping-weekly-forecast-v1`；
- 初始回测窗口24周，分别评价1/2/4周ahead；
- 区间使用过去滚动残差的经验绝对误差分位数，阶段7默认80%区间；
- 线性模型负预测保留原值用于审计，业务输出下界为0；
- 周预测按7个日历日等比例分配到自然月并保持整数分守恒。

ARIMA(1,0,1)虽在总消费1周MAE上比历史均值低4.87%，但没有达到预设5%复杂度门槛，因此不进入V1生产路径。详细证据见 `docs/forecast-experiment.md`。

## 13. 阶段 8 冻结的滚动再规划实现

- 历史事实和 `next_open_week` 之前的预算锁定，只优化开放周；
- 事件不直接重复加减金额，更新后的财务快照、收入、目标和约束是算法输入；
- 每次重算先执行阶段 7 可行性判断，再调用同一预算优化器；
- 阶段 6 已选模型每周只更新参数，新增 8 个完整周后才提示重新比较模型；
- 恢复周期通过累计未来预算上限实现，不能突破最低或固定预算；
- 计划差异、恢复指标、风险变化和 NO_REPLAN 对照均为结构化结果；
- 版本、事件关联和当前计划切换由单个数据库事务保存。

完整流程、公式和脱敏冲击结果见 `docs/rolling-replan.md`。
