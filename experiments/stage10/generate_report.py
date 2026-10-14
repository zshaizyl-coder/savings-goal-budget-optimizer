from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results"
REPORT = ROOT / "docs" / "experiment-report.md"


def money(value) -> str:
    return "—" if pd.isna(value) else f"¥{value / 100:,.2f}"


def number(value, digits=2) -> str:
    return "—" if pd.isna(value) else f"{value:.{digits}f}"


def table(headers: list[str], rows: list[list[object]]) -> str:
    def safe(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    return "\n".join([
        "| " + " | ".join(map(safe, headers)) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        *("| " + " | ".join(map(safe, row)) + " |" for row in rows),
    ])


def main() -> None:
    a = pd.read_csv(RESULTS / "experiment_a_budget_methods.csv")
    b = pd.read_csv(RESULTS / "experiment_b_replanning.csv")
    c = pd.read_csv(RESULTS / "experiment_c_forecast_robustness.csv")
    d = pd.read_csv(RESULTS / "experiment_d_thresholds.csv")
    h = pd.read_csv(RESULTS / "experiment_d_recovery_horizons.csv")
    stats = pd.read_csv(RESULTS / "statistical_tests.csv")
    metadata = json.loads((RESULTS / "run_metadata.json").read_text(encoding="utf-8"))

    usable = a[(a.feasible) & (a.pressure != "INFEASIBLE")]
    method_summary = usable.groupby("method").agg(
        achievement=("goal_achieved", "mean"), habit=("habit_deviation_bps", "mean"),
        volatility=("weekly_budget_volatility_minor", "mean"),
        max_category=("max_category_adjustment_ratio", "mean"), violations=("minimum_violations", "sum"),
    ).reset_index()
    method_rows = [[row.method, f"{row.achievement:.1%}", f"{row.habit:.1f}", money(row.volatility),
                    f"{row.max_category:.1%}", int(row.violations)] for row in method_summary.itertuples()]

    real = a[a.user_type == "REAL_AGGREGATE"]
    real_rows = [[row.pressure, row.method, "可行" if row.feasible else f"不可行（{row.reason_code}）",
                  money(row.final_savings_minor), number(row.habit_deviation_bps, 0),
                  f"{row.max_category_adjustment_ratio:.1%}" if not pd.isna(row.max_category_adjustment_ratio) else "—"]
                 for row in real.itertuples()]

    medium = a[(a.pressure == "MEDIUM") & (a.user_type != "REAL_AGGREGATE")]
    pivot = medium.pivot_table(index=["user_type", "repetition"], columns="method",
                               values="habit_deviation_bps", aggfunc="first")
    user_rows = []
    for user in sorted(medium.user_type.unique()):
        block = pivot.loc[user].dropna()
        improvement = ((block.UNIFORM_REDUCTION - block.PERSONALIZED) /
                       block.UNIFORM_REDUCTION).mean() if len(block) else float("nan")
        best = medium[(medium.user_type == user) & medium.feasible].groupby("method").habit_deviation_bps.mean().idxmin()
        note = "30/30配对可行" if len(block) == 30 else f"{len(block)}/30配对可行"
        user_rows.append([user, best, f"{improvement:.1%}" if not pd.isna(improvement) else "—", note])

    shocks = b[(b.scenario_type == "SHOCK") & (b.timing == "EARLY")]
    shock_rows = [[row.scenario.replace("SHOCK_", "¥"), row.method,
                   "可行" if row.feasible else f"不可行（{row.reason_code}）",
                   money(row.final_savings_minor), money(row.savings_gap_minor),
                   "NOT_RECOVERED" if pd.isna(row.weeks_to_recover) else int(row.weeks_to_recover),
                   number(row.habit_deviation_bps, 0)] for row in shocks.itertuples()]

    forecast_rows = []
    for error in sorted(c.prediction_error.unique()):
        static = c[(c.prediction_error == error) & (c.method == "STATIC")].iloc[0]
        rolling = c[(c.prediction_error == error) & (c.method == "ROLLING")].iloc[0]
        forecast_rows.append([f"{error:+.0%}", money(static.savings_gap_minor),
                              money(rolling.savings_gap_minor), int(rolling.replan_count),
                              number(static.habit_deviation_bps, 0), number(rolling.habit_deviation_bps, 0)])

    threshold_rows = [[row.threshold, int(row.replan_count), int(row.unnecessary_replans),
                       money(row.savings_gap_minor), number(row.habit_deviation_bps, 0)]
                      for row in d.itertuples()]
    horizon_rows = [[row.recovery_horizon, "可行" if row.feasible else "不可行",
                     "NOT_RECOVERED" if pd.isna(row.weeks_to_recover) else int(row.weeks_to_recover),
                     money(row.max_weekly_adjustment_minor), number(row.habit_deviation_bps, 0),
                     money(row.weekly_volatility_minor)] for row in h.itertuples()]

    statistical_rows = [[row.comparison, row.test, int(row.n), f"{row.p_value:.3e}",
                         f"{row.effect_name}={row.effect_size:.3f}", f"{row.mean_improvement_ratio:.1%}"]
                        for row in stats.itertuples()]
    runtime = metadata["runtime_seconds"]
    text = f"""# 阶段10：个性化预算优化与滚动再规划对照实验

## 1. 实验目的与结论摘要

本阶段只验证阶段7—8既有算法，不新增预测器或优化方法。结果表明：在本研究设置的真实聚合案例和六类模拟用户中，个性化方法在共同可行的中等/困难场景中平均降低 Habit Deviation，但不在所有指标上占优；它会把更多调整集中到高可调类别，因此最大类别削减通常高于统一比例法。滚动再规划能修复早期可恢复冲击，也能在大额或晚期冲击时更早返回不可行，但不能保证所有目标都能恢复。

## 2. 实验数据与预注册配置

- 真实部分仅使用阶段5/6产生的脱敏周度和类别聚合，不读取逐笔交易文本。
- 当前存款、周收入、目标、最低预算和 adjustability 均为**实验假设**，不是用户真实财务信息。
- 模拟用户为 A稳定、B高波动、C高固定支出、D高弹性、E收入波动、F突发消费六型；每型30次，master seed为 `{metadata['master_seed']}`。
- 配置哈希：`{metadata['config_sha256']}`；数据集版本：`{metadata['dataset_version']}`。
- A实验把用户波动用于生成重复样本的个体基线，规划期仅保留小幅周波动；未知的未来突发事件没有泄露给预算器。

## 3. 对照方法与公平性

Uniform Reduction、Historical Share Allocation 与阶段7 `PersonalizedBudgetOptimizer` 使用完全相同的存款、收入、目标、期限、自然消费、最低预算及固定支出。NO_REPLAN与阶段8 `RollingBudgetReplanner` 使用相同初始方案和事件。INFEASIBLE场景不生成伪预算。

阶段10发现并用失败测试证明了两个实现缺陷：历史占比分配可能因整数四舍五入多出数分而失败；SLSQP在明确可行的困难输入下会达到500次迭代上限。修复仅增加整数回收并把迭代上限调到1500，没有改目标函数、权重或约束。

## 4. 指标定义

- Savings Gap = `max(target - final_savings, 0)`；Goal Achievement Ratio = `final_savings / target`。
- Habit Deviation完全复用阶段7的归一化平方偏差公式。
- Total Adjustment Ratio为自然总额与预算总额之差占自然总额比例。
- Maximum Category Adjustment为类别聚合削减比例最大值；Category Adjustment Dispersion为类别削减比例总体标准差。
- Weekly Budget Volatility为相邻周预算总额绝对变化的平均值。
- 不可行方案的 Final Savings、Gap 和恢复周数留空，不用0伪装；恢复失败记为 `NOT_RECOVERED`。

## 5. Experiment A：预算方法总体对比

以下为排除INFEASIBLE且算法成功返回方案后的总体均值。三种方法最低预算违反数均为0。

{table(['Method', 'Goal Achievement', 'Habit Deviation', 'Weekly Volatility', 'Max Category Adjustment', 'Violations'], method_rows)}

个性化方法的优势集中在 Habit Deviation（总体均值较低）和相对统一比例法的周预算平滑；代价是最大类别调整更大，因为 adjustability 明确允许压力集中到购物、娱乐等高弹性类别。Historical Share在本实验中周波动最低，因此不能声称个性化方法全面优于基线。

## 6. 真实聚合案例

{table(['Pressure', 'Method', 'Status', 'Final Savings', 'Habit', 'Max Category Adjustment'], real_rows)}

EASY差异很小；MEDIUM下三种方法都达标，Personalized的Habit为1484 bps，低于Uniform的1705和Historical的1709，但最大类别调整为35.1%，高于约18.9%。HARD下Personalized超额储蓄且最大类别调整达55.0%，说明其目标优先模式可能过度集中削减；这是负面结果。三种方法均正确拒绝INFEASIBLE。

## 7. 六类模拟用户

{table(['User Type', 'Best Habit Method', 'Personalized vs Uniform', 'Notes'], user_rows)}

MEDIUM场景中六类用户均有30/30有效配对，个性化Habit改善约5.4%—17.2%。高弹性型改善最小，说明当大量类别都可调整时，简单比例法已经较接近；个性化方法的最大类别削减在各型均更高约15—18个百分点。HARD下28/181个Personalized样本因用户调整意愿边界返回 `MODE_CONSTRAINT_INFEASIBLE`，这属于约束语义而非数学最低预算不可行。

## 8. Experiment B：固定预算与滚动再规划

以下表列出早期Shock；中期和晚期完整结果见CSV。

{table(['Shock', 'Method', 'Status', 'Final Savings', 'Gap', 'Recovery Weeks', 'Habit'], shock_rows)}

早期500元和1000元可通过未来9周恢复；早期3000/5000元被及时判为不可行。中期仅500元可在6周恢复；晚期500元也因剩余调整空间不足而不可行。相同冲击越晚发生，可恢复性越差。NO_REPLAN不会识别可行性变化，只留下与冲击等额的储蓄缺口。

收入变化结果：-10%在6周滚动调整后可达标，-30%被识别为不可行；+10%和+30%均可行，并降低Habit压力（分别132和0 bps），没有把全部额外收入自动转成消费。

## 9. Experiment C：预测误差鲁棒性

{table(['Prediction Error', 'Static Gap', 'Rolling Gap', 'Replans', 'Static Habit', 'Rolling Habit'], forecast_rows)}

在本实验假设下，低估10%—20%会形成更紧预算，静态方案已达标但可能过度压缩生活；0至+20%时静态方案出现¥40.66—¥86.14缺口，单次滚动反馈将缺口归零，但Habit偏离上升。结论不是“预测误差无影响”，而是滚动反馈能在这组可恢复误差中修正储蓄缺口，同时付出额外调整成本。

阶段6总消费WAPE约50.68%，因此预测只能作为自然消费基线估计，不是精确承诺。购物WAPE约80.47%，购物预测只用于低置信参考，最终预算仍由历史结构、最低预算、adjustability和储蓄目标共同约束。

## 10. Experiment D：阈值敏感性

{table(['Threshold', 'Replans', 'Unnecessary', 'Residual Gap Proxy', 'Habit'], threshold_rows)}

T1降低未触发超支，但产生2次低于100元的额外触发；T3仅触发2次但残留超支代理最大。T2_AND在该路径上是折中方案，继续推荐100元+20% AND。T2_OR与AND相同，是因为本路径达到一个阈值时也同时达到另一个；不能据此声称AND/OR普遍等价。这里的Gap是“假设触发后能完整回收”的残留未触发超支代理，不替代正式滚动优化结果。

## 11. 恢复周期敏感性

{table(['Horizon', 'Status', 'Weeks To Recover', 'Avg Weekly Adjustment', 'Habit', 'Weekly Volatility'], horizon_rows)}

2周恢复违反当前调整意愿边界而不可行；4周可行且恢复快，但Habit和周波动更高；全部剩余周期需8周，Habit更低且周预算最平稳。默认4周适合强调恢复速度，但不应作为所有用户的唯一选择，生活稳定优先时应使用更长周期。

## 12. 统计分析

{table(['Comparison', 'Test', 'n', 'p', 'Effect', 'Mean Improvement'], statistical_rows)}

差值经Shapiro检验不满足近似正态，故使用配对Wilcoxon并报告rank-biserial效应量。检验仅针对核心Habit指标，避免对大量次要指标滥用p值。Personalized相对两种基线的配对平均改善均约6.7%，统计差异显著且效应量大；但该统计结果不覆盖算法返回不可行的配对。

## 13. 消融实验

本阶段未执行可选的No Adjustability / No Smoothing消融。原因是核心问题已由三种正式方法、滚动实验和敏感性实验覆盖，且本阶段禁止扩张实验范围。阶段7已有模式差异测试，不能冒充正式消融结果。

## 14. 失败与边界情况

1. EASY场景三种方法差异很小，复杂优化的收益有限。
2. Personalized并未降低最大类别调整；相反，它把压力集中到高adjustability类别。
3. HARD模拟中15.5%的Personalized运行因用户意愿边界不可行，不能强行生成方案。
4. 3000/5000元冲击、晚期冲击和-30%收入下降无法靠滚动预算解决；滚动系统的价值是提前识别不可行。
5. 预测低估会通过更严格预算获得较高储蓄，但不代表预测更好；它可能损害生活稳定性。
6. 阈值敏感性使用一条可复现偏差路径，外部效度有限。

## 15. 研究结论与局限性

在本研究设置中，个性化优化的可验证优势是“以更低习惯偏离实现同一储蓄目标”，而不是全面提升所有指标。滚动再规划对早期、小中等且仍有调整空间的变化有实际价值；对于超过可行边界的冲击，其正确行为是拒绝不现实方案。结论仅覆盖一年微信聚合数据和六类模拟结构，真实收入与多源账单仍不足，不能推广到所有用户。

## 16. 复现与产物

运行：`python -m experiments.stage10.run_all`。完整核心运行约 `{runtime:.2f}` 秒（当前metadata的最后一次入口运行；首次正式求解耗时另见development log），快速重绘可加 `--reuse-results`。结果位于 `experiments/stage10/results/`，图位于 `experiments/stage10/figures/`。核心图包括预算方法Habit箱线图、Shock-Gap曲线、1000元Shock储蓄轨迹、预测误差鲁棒性、恢复周期权衡图。所有输出均为类别/周度聚合或模拟结果，不含商户、账号、姓名、备注或交易ID。
"""
    REPORT.write_text(text, encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
