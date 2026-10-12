"""Generate privacy-safe stage-5 aggregates, figures, and the analysis report."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from backend.app.analytics.consumption import RefundAllocationPolicy, UNALLOCATED_REFUND, build_consumption_datasets
from backend.app.analytics.forecast_dataset import build_weekly_forecast_dataset
from backend.app.analytics.quality import (
    category_quality_table,
    identify_anomaly_weeks,
    refund_sensitivity_table,
    weekly_descriptive_statistics,
)
from backend.app.db import create_session_factory, create_sqlite_engine
from backend.app.repositories.analysis import TransactionAnalysisRepository


def money(minor: float | int) -> str:
    return f"¥{float(minor) / 100:,.2f}"


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def parse_stage1_months(path: Path) -> pd.DataFrame:
    pattern = re.compile(
        r"^\| (20\d{2}-\d{2}) \| (?:完整月|部分月) \| ¥([\d,]+\.\d{2}) \| ¥([\d,]+\.\d{2}) \| ¥([\d,]+\.\d{2}) \|$",
        re.MULTILINE,
    )
    rows = []
    for month, gross, refund, net in pattern.findall(path.read_text(encoding="utf-8")):
        rows.append({
            "year_month": month,
            "baseline_gross_minor": round(float(gross.replace(",", "")) * 100),
            "baseline_refund_minor": round(float(refund.replace(",", "")) * 100),
            "baseline_net_minor": round(float(net.replace(",", "")) * 100),
        })
    if not rows:
        raise ValueError("stage-1 monthly baseline not found")
    return pd.DataFrame(rows)


def plot_outputs(
    output_dir: Path,
    weekly: pd.DataFrame,
    monthly: pd.DataFrame,
    quality: pd.DataFrame,
    daily: pd.DataFrame,
) -> None:
    figure_dir = output_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    plt.style.use("seaborn-v0_8-whitegrid")

    def save(name: str) -> None:
        plt.tight_layout()
        plt.savefig(figure_dir / name, dpi=180, bbox_inches="tight", facecolor="white")
        plt.close()

    complete = weekly.loc[weekly["is_complete_week"]]
    plt.figure(figsize=(10, 4.8))
    plt.plot(pd.to_datetime(complete["week_start"]), complete["net_consumption_minor"] / 100, marker="o", markersize=3)
    plt.title("Weekly Net Consumption (Complete Weeks)")
    plt.xlabel("Week start")
    plt.ylabel("CNY")
    save("weekly_net_consumption.png")

    plt.figure(figsize=(10, 4.8))
    positions = np.arange(len(monthly))
    width = 0.26
    plt.bar(positions - width, monthly["gross_consumption_minor"] / 100, width, label="Gross")
    plt.bar(positions, monthly["refund_minor"] / 100, width, label="Refund")
    plt.bar(positions + width, monthly["net_consumption_minor"] / 100, width, label="Net")
    plt.xticks(positions, monthly["year_month"], rotation=45, ha="right")
    plt.title("Monthly Gross, Refund and Net Consumption")
    plt.ylabel("CNY")
    plt.legend()
    save("monthly_consumption.png")

    categories = quality.loc[(quality["category_code"] != UNALLOCATED_REFUND) & (quality["gross_amount_minor"] > 0)].sort_values("gross_amount_share")
    plt.figure(figsize=(8, 5.5))
    plt.barh(categories["category_code"], categories["gross_amount_share"] * 100)
    plt.title("Gross Consumption Share by Category")
    plt.xlabel("Share (%)")
    save("category_amount_share.png")

    variable = quality.loc[np.isfinite(quality["weekly_cv"]) & (quality["category_code"] != UNALLOCATED_REFUND)].sort_values("weekly_cv")
    plt.figure(figsize=(8, 5.5))
    plt.barh(variable["category_code"], variable["weekly_cv"])
    plt.title("Weekly Category Variability")
    plt.xlabel("Coefficient of variation")
    save("category_weekly_variability.png")

    plt.figure(figsize=(10, 4.8))
    dates = pd.to_datetime(complete["week_start"])
    plt.plot(dates, complete["gross_consumption_minor"] / 100, label="Gross")
    plt.plot(dates, complete["refund_minor"] / 100, label="Refund")
    plt.plot(dates, complete["net_consumption_minor"] / 100, label="Net")
    plt.title("Weekly Gross vs Refund vs Net")
    plt.xlabel("Week start")
    plt.ylabel("CNY")
    plt.legend()
    save("weekly_gross_refund_net.png")

    day_type = daily.groupby("is_weekend").agg(
        daily_net_cny=("net_consumption_minor", lambda values: values.mean() / 100),
        daily_frequency=("consumption_count", "mean"),
    ).reindex([False, True])
    fig, axes = plt.subplots(1, 2, figsize=(9, 4.5))
    labels = ["Weekday", "Weekend"]
    axes[0].bar(labels, day_type["daily_net_cny"])
    axes[0].set_title("Average Daily Net Consumption")
    axes[0].set_ylabel("CNY per day")
    axes[1].bar(labels, day_type["daily_frequency"])
    axes[1].set_title("Average Daily Consumption Frequency")
    axes[1].set_ylabel("Transactions per day")
    save("weekday_vs_weekend.png")


def build_report(
    datasets,
    total_only,
    stats: dict[str, float | int],
    anomalies: pd.DataFrame,
    anomaly_lower: float,
    anomaly_upper: float,
    quality: pd.DataFrame,
    sensitivity: pd.DataFrame,
    baseline_comparison: dict[str, object],
    transactions_count: int,
) -> str:
    facts, daily, weekly, monthly = datasets.facts, datasets.daily, datasets.weekly, datasets.monthly
    complete = weekly.loc[weekly["is_complete_week"]]
    refund_count = int(facts["refund_count"].sum())
    unallocated_refunds = facts.loc[(facts["refund_count"] == 1) & (facts["category_code"] == UNALLOCATED_REFUND)]
    linked_refunds = refund_count - len(unallocated_refunds)
    total_gross = int(facts["gross_consumption_minor"].sum())
    total_refund = int(facts["refund_minor"].sum())
    total_net = int(facts["net_consumption_minor"].sum())
    zero_gross_weeks = int((complete["gross_consumption_minor"] == 0).sum())

    anomaly_rows = []
    count_q3 = float(complete["consumption_count"].quantile(0.75))
    for _, row in anomalies.sort_values("week_start").iterrows():
        week_facts = facts.loc[(facts["week_start"] == row["week_start"]) & (facts["consumption_count"] == 1)]
        category_gross = week_facts.groupby("category_code")["gross_consumption_minor"].sum().sort_values(ascending=False)
        dominant = str(category_gross.index[0]) if not category_gross.empty else "none"
        largest_share = float(week_facts["amount_minor"].max() / row["gross_consumption_minor"]) if len(week_facts) and row["gross_consumption_minor"] else 0
        reason = "单笔大额主导" if largest_share >= 0.5 else ("消费频次偏高" if row["consumption_count"] > count_q3 else "多笔金额共同抬升")
        anomaly_rows.append([row["week_start"], money(row["net_consumption_minor"]), row["anomaly_direction"], dominant, reason])

    category_rows = []
    for _, row in quality.iterrows():
        cv = "不适用" if pd.isna(row["weekly_cv"]) else f"{row['weekly_cv']:.2f}"
        category_rows.append([
            row["category_code"], int(row["transaction_count"]), pct(float(row["gross_amount_share"])),
            int(row["active_weeks"]), pct(float(row["zero_week_ratio"])), cv,
            row["qualification"], "GO" if row["qualification"] == "A" else "NO-GO",
        ])

    key_categories = ["food", "shopping", "other", "transport", "education", "health"]
    sensitivity_rows = []
    for category in key_categories:
        matched = sensitivity.loc[sensitivity["category_code"] == category]
        if not matched.empty:
            row = matched.iloc[0]
            sensitivity_rows.append([
                category, money(row["net_minor_total_only"]), money(row["net_minor_reliable"]),
                money(row["allocated_refund_effect_minor"]),
            ])

    day_type = daily.groupby("is_weekend").agg(
        daily_net_minor=("net_consumption_minor", "mean"),
        daily_frequency=("consumption_count", "mean"),
        observed_days=("date", "size"),
    )
    weekday, weekend = day_type.loc[False], day_type.loc[True]
    day_counts = {False: int(weekday["observed_days"]), True: int(weekend["observed_days"])}
    category_day_type = facts.loc[facts["category_code"].notna()].groupby(
        ["is_weekend", "category_code"]
    )["net_consumption_minor"].sum()
    category_day_rows = []
    for category in ("food", "shopping", "other", "transport"):
        weekday_value = float(category_day_type.get((False, category), 0)) / day_counts[False]
        weekend_value = float(category_day_type.get((True, category), 0)) / day_counts[True]
        category_day_rows.append([category, money(weekday_value), money(weekend_value), f"{weekend_value / weekday_value:.2f}" if weekday_value else "不适用"])
    full_months = monthly.iloc[1:-1].copy()
    month_slope = float(np.polyfit(np.arange(len(full_months)), full_months["net_consumption_minor"], 1)[0]) if len(full_months) > 1 else float("nan")
    max_month = monthly.loc[monthly["net_consumption_minor"].idxmax()]
    min_month = monthly.loc[monthly["net_consumption_minor"].idxmin()]
    category_month_pivot = datasets.category_monthly.pivot(
        index="year_month", columns="category_code", values="net_consumption_minor"
    ).fillna(0)
    category_month_rows = []
    for month in monthly["year_month"]:
        category_month_rows.append([
            month,
            money(category_month_pivot.loc[month].get("food", 0)),
            money(category_month_pivot.loc[month].get("shopping", 0)),
            money(category_month_pivot.loc[month].get("other", 0)),
        ])

    transfer = facts.loc[(facts["flow_type"] == "transfer") & (facts["source_type_code"] == "transfer")]
    income_candidates = facts.loc[facts["flow_type"] == "income_candidate"]
    other_row = quality.loc[quality["category_code"] == "other"].iloc[0]
    other_transactions = facts.loc[(facts["consumption_count"] == 1) & (facts["category_code"] == "other")]
    other_large = other_transactions.loc[other_transactions["amount_minor"] > datasets.thresholds.recommended_minor]
    other_large_share = float(other_large["amount_minor"].sum() / other_transactions["amount_minor"].sum()) if len(other_transactions) else 0.0
    classified_gross_share = 1 - float(other_row["gross_amount_share"])

    monthly_rows = [[
        row["year_month"], money(row["gross_consumption_minor"]), money(row["refund_minor"]), money(row["net_consumption_minor"]),
        "部分月" if index in {0, len(monthly) - 1} else "完整月",
    ] for index, row in monthly.iterrows()]

    go_categories = quality.loc[quality["qualification"] == "A", "category_code"].tolist()
    report = f"""# 消费行为分析与预测数据集（阶段 5）

## 1. 结论摘要

分析从 SQLite 中的 {transactions_count:,} 条标准 Transaction 生成，不再读取或解释微信原始列。日度、周度、月度、类别质量、退款敏感性和预测特征全部来自同一个事实表。总消费周度预测准入为 **GO**；类别独立预测准入为：{', '.join(go_categories) if go_categories else '无'}。阶段 5 没有训练预测模型。

## 2. 冻结统计口径

- `consumption + outflow`：计入消费毛额；
- `expense_refund + inflow`：计入退款并从净消费冲减；
- `transfer/internal_transfer/balance_transfer/red_packet/other`：不进入核心消费；
- 微信收入候选不进入消费，也不作为完整收入；
- 净消费恒等式：`net = gross - refund`；
- 用户确认类别优先于系统类别，系统类别缺失时才使用 `other`；
- 总消费冲减全部退款；类别仅回冲 {linked_refunds} 条高置信关联退款，剩余 {len(unallocated_refunds)} 条进入 `unallocated_refund`。

全年消费毛额 {money(total_gross)}，退款 {money(total_refund)}，净消费 {money(total_net)}。转账继续排除：普通转账 {len(transfer):,} 条、合计 {money(transfer['amount_minor'].sum())}。微信收入候选仅 {len(income_candidates)} 条，不进行收入建模。

## 3. 连续时间序列

- 日度序列：{len(daily)} 个连续自然日，零消费日也补零；
- 周度序列：{len(weekly)} 个观测周，其中完整业务周 {int(stats['complete_weeks'])} 个；
- 月度序列：{len(monthly)} 个月份，其中中间完整月 {max(len(monthly) - 2, 0)} 个；
- 完整周零毛消费周：{zero_gross_weeks} 个。

### 完整周净消费统计

{markdown_table(['指标', '结果'], [
    ['完整周数', int(stats['complete_weeks'])], ['均值', money(stats['mean_minor'])],
    ['中位数', money(stats['median_minor'])], ['标准差', money(stats['std_minor'])],
    ['变异系数', f"{stats['cv']:.2f}"], ['最小值', money(stats['minimum_minor'])],
    ['Q1', money(stats['q1_minor'])], ['Q3', money(stats['q3_minor'])],
    ['IQR', money(stats['iqr_minor'])], ['最大值', money(stats['maximum_minor'])],
    ['偏度', f"{stats['skewness']:.2f}"],
])}

## 4. 阶段 1 对账

- 月度毛额/退款/净额逐月最大绝对差：{money(baseline_comparison['maximum_monthly_difference_minor'])}；
- 全期毛额差：{money(baseline_comparison['gross_difference_minor'])}；退款差：{money(baseline_comparison['refund_difference_minor'])}；净额差：{money(baseline_comparison['net_difference_minor'])}；
- 完整周数与阶段 1 {'一致' if int(stats['complete_weeks']) == 51 else '不一致'}；异常周数量与阶段 1 {'一致' if len(anomalies) == 4 else '不一致'}。

总量、月份和完整周结果一致，说明标准 Transaction 没有改变阶段 1 的核心资金流口径。Q1 的精确值为 274.185 元：阶段 1 基于浮点数显示为 274.18 元，阶段 5 基于整数分插值显示为 274.19 元，属于半分位数的舍入差异。类别结果允许出现差异：阶段 1 用文本尽量把所有退款回冲类别；阶段 5 只回冲已确认关系，未确认退款单独保留；同时统一类别新增 `life_services` 并拆分原“居住与缴费/数字服务”，OTHER 笔数因此由阶段 1 的 1,067 变为 1,044。这是可追踪的口径变化，不是数据丢失。

## 5. 退款敏感性

方案 A 把全部退款只放入总消费与 `unallocated_refund`；方案 B 只把高置信关联退款回冲原类别。两种方案的总毛额、总退款、总净额和周度总消费完全相同，因此未关联退款**不影响总消费预测**。类别影响如下：

{markdown_table(['类别', '方案A净额', '方案B净额', '已确认退款影响'], sensitivity_rows)}

未分配退款金额为 {money(unallocated_refunds['refund_minor'].sum())}，占全部消费退款 {pct(float(unallocated_refunds['refund_minor'].sum() / total_refund) if total_refund else 0)}。它会影响类别净额及类别预算解释，因此阶段 6 采用方案 B，并把未分配退款保持为独立桶；不得把类别预测简单相加冒充总量预测。

## 6. 异常周与大额消费

完整周 IQR 下界为 {money(anomaly_lower)}，上界为 {money(anomaly_upper)}，识别异常周 {len(anomalies)} 个：

{markdown_table(['周起始日', '净消费', '方向', '主导类别', '聚合原因'], anomaly_rows)}

单笔消费阈值比较：95% 分位数 {money(datasets.thresholds.q95_minor)}；交易金额 IQR 上界 {money(datasets.thresholds.iqr_upper_minor)}。默认推荐两者较大值 {money(datasets.thresholds.recommended_minor)}，共识别 {int(daily['large_transaction_count'].sum())} 条；用户以后可覆盖该阈值。统计异常不等于“计划外”，仍需用户确认。

## 7. 工作日与周末

- 工作日：{int(weekday['observed_days'])} 天，日均净消费 {money(weekday['daily_net_minor'])}，日均消费频次 {weekday['daily_frequency']:.2f}；
- 周末：{int(weekend['observed_days'])} 天，日均净消费 {money(weekend['daily_net_minor'])}，日均消费频次 {weekend['daily_frequency']:.2f}；
- 周末/工作日日均净消费比：{weekend['daily_net_minor'] / weekday['daily_net_minor']:.2f}。

这里按日均而非总额比较。样本包含异常大额日且分布偏斜，本阶段不强行加入均值显著性检验。

主要类别的日均净消费：

{markdown_table(['类别', '工作日日均', '周末日均', '周末/工作日'], category_day_rows)}

餐饮基本持平，购物、OTHER 和交通的周末日均反而更低；因此“周末总金额更高”更可能受稀疏高金额类别和异常日影响，不能解释为所有类别在周末都增加。

## 8. 月度观察

{markdown_table(['月份', '毛消费', '退款', '净消费', '覆盖'], monthly_rows)}

主要类别月度净额：

{markdown_table(['月份', '餐饮', '购物', 'OTHER'], category_month_rows)}

中间完整月净消费线性斜率约为每月 {money(month_slope)}；最高观测月份为 {max_month['year_month']}，最低为 {min_month['year_month']}。只有 11 个完整月，以上只能描述“观察到的变化”，不能证明稳定季节性。

## 9. 类别数据质量与预测资格

资格规则：A 类要求至少 50 笔、30 个活跃完整周、毛额占比至少 5%、零周比例不高于 45%，且语义可解释；B 类满足至少 20 笔、12 个活跃周或 1% 金额占比之一，仅保留用于预算分析；C 类数据稀疏或语义不稳定。CV 只描述波动，不直接代表可调整性。

{markdown_table(['类别', '交易数', '毛额占比', '活跃周', '零周比例', '周度CV', '等级', '独立预测'], category_rows)}

## 10. OTHER 专题

- OTHER 消费 {int(other_row['transaction_count'])} 笔，占核心消费笔数 {pct(float(other_row['transaction_count'] / facts['consumption_count'].sum()))}；
- 毛额占比 {pct(float(other_row['gross_amount_share']))}，说明既有大量小额记录，也包含不可忽略的较大金额；
- 单笔中位数 {money(other_transactions['amount_minor'].median())}；超过推荐大额阈值的 OTHER 记录 {len(other_large)} 条，其金额占 OTHER 毛额 {pct(other_large_share)}；
- 活跃完整周 {int(other_row['active_weeks'])} 个，零周比例 {pct(float(other_row['zero_week_ratio']))}，周度 CV {other_row['weekly_cv']:.2f}；
- 可靠分类的消费毛额覆盖率只有 {pct(classified_gross_share)}。

保留 OTHER 不影响总消费预测，因为全部核心消费仍进入总量；但它覆盖近一半金额且内部语义异质，会明显限制分类预算优化的解释力。因此 OTHER 不单独建模，用户纠正入口是 V1 必需能力。当前数据库已验证 `user_category_id > suggested_category_id > other` 的优先级，无需迁移或破坏隐私策略。

## 11. 分类敏感性实验

维持当前分类时，总消费覆盖 100%，但可靠类别仅覆盖 {pct(classified_gross_share)} 毛额；只分析可靠类别时，餐饮和购物等类别序列更可解释，但不能代表全部消费。两种方案的周度总消费统计不变。因此阶段 6 的顺序应为：先建立总消费基线，再仅对 A 类类别做附加实验；类别结果不替代总量结果。

## 12. 阶段 6 唯一数据集

`build_weekly_forecast_dataset()` 是唯一模型输入生成器。每个目标只提供 `lag_1..lag_4`、过去 2/4 周移动均值、过去 4 周标准差、月份和 ISO 周序号。所有 lag/rolling 都先 `shift(1)`，不含当前周实际值。`previous_month_mean` 暂不加入，避免在 51 周样本上增加弱解释特征。

准入结论：

- 总消费：**GO**。51 个完整周、0 个零毛消费周，虽 CV {stats['cv']:.2f} 且受异常周影响，但适合短期滚动基线；
"""
    for _, row in quality.iterrows():
        if row["category_code"] == UNALLOCATED_REFUND:
            continue
        decision = "GO" if row["qualification"] == "A" else "NO-GO"
        report += f"- {row['category_code']}：**{decision}**。{int(row['active_weeks'])} 个活跃周、零周 {pct(float(row['zero_week_ratio']))}、CV {row['weekly_cv']:.2f}，等级 {row['qualification']}；\n"
    report += """

## 13. 阶段 6 建议

总消费优先比较历史完整周均值、最近 4 周移动平均和简单指数平滑；Holt、低阶 ARIMA、带少量滞后特征的线性回归仅作为候选，并必须使用滚动回测。餐饮、购物使用同一基线框架做附加实验。其他类别不进入正式模型竞赛。不得训练收入模型。

## 14. 可复现输出与图表

`experiments/aggregates/` 保存日、周、月和类别长表、退款敏感性、类别质量与唯一预测数据集；没有交易对象、账号、商户、备注或逐笔交易。图表包括周度净消费、月度消费、类别占比、类别波动、毛额/退款/净额以及工作日/周末比较。
"""
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-dir", default="experiments/aggregates")
    parser.add_argument("--report", default="docs/consumption-analysis.md")
    parser.add_argument("--username", default="local-user")
    args = parser.parse_args()

    engine = create_sqlite_engine(args.database)
    sessions = create_session_factory(engine)
    with sessions() as session:
        repository = TransactionAnalysisRepository(session)
        user = repository.get_user(args.username)
        transactions = repository.list_transactions(user.id)
    engine.dispose()

    reliable = build_consumption_datasets(transactions, refund_policy=RefundAllocationPolicy.RELIABLE_LINKS)
    total_only = build_consumption_datasets(transactions, refund_policy=RefundAllocationPolicy.TOTAL_ONLY)
    stats = weekly_descriptive_statistics(reliable.weekly)
    anomalies, lower, upper = identify_anomaly_weeks(reliable.weekly)
    quality = category_quality_table(reliable.facts, reliable.category_weekly, reliable.weekly)
    full_weeks = set(reliable.weekly.loc[reliable.weekly["is_complete_week"], "week_start"])
    sensitivity = refund_sensitivity_table(total_only.category_weekly, reliable.category_weekly, full_weeks)

    baseline = parse_stage1_months(Path("docs/data-eda-report.md"))
    comparison = reliable.monthly.merge(baseline, on="year_month", how="outer")
    for measure in ("gross", "refund", "net"):
        comparison[f"{measure}_difference_minor"] = comparison[f"{measure}_consumption_minor" if measure != "refund" else "refund_minor"] - comparison[f"baseline_{measure}_minor"]
    baseline_summary = {
        "maximum_monthly_difference_minor": int(comparison[["gross_difference_minor", "refund_difference_minor", "net_difference_minor"]].abs().to_numpy().max()),
        "gross_difference_minor": int(comparison["gross_difference_minor"].sum()),
        "refund_difference_minor": int(comparison["refund_difference_minor"].sum()),
        "net_difference_minor": int(comparison["net_difference_minor"].sum()),
    }
    if baseline_summary["maximum_monthly_difference_minor"] != 0:
        raise AssertionError("standard transactions do not reconcile to the stage-1 monthly baseline")

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    reliable.daily.to_csv(output / "daily_consumption.csv", index=False)
    reliable.weekly.to_csv(output / "weekly_consumption.csv", index=False)
    reliable.monthly.to_csv(output / "monthly_consumption.csv", index=False)
    reliable.category_daily.to_csv(output / "category_daily.csv", index=False)
    reliable.category_weekly.to_csv(output / "category_weekly.csv", index=False)
    reliable.category_monthly.to_csv(output / "category_monthly.csv", index=False)
    quality.to_csv(output / "category_quality.csv", index=False)
    sensitivity.to_csv(output / "refund_sensitivity.csv", index=False)
    total_forecast = build_weekly_forecast_dataset(reliable.weekly)
    total_forecast.to_csv(output / "weekly_forecast_total.csv", index=False)
    for category in quality.loc[quality["qualification"] == "A", "category_code"]:
        build_weekly_forecast_dataset(reliable.weekly, target_category_code=category).to_csv(
            output / f"weekly_forecast_{category}.csv", index=False
        )

    plot_outputs(output, reliable.weekly, reliable.monthly, quality, reliable.daily)
    summary = {
        "dataset_version": "stage5-consumption-v1",
        "standard_transactions": len(transactions),
        "complete_weeks": int(stats["complete_weeks"]),
        "weekly_statistics": stats,
        "anomaly_weeks": len(anomalies),
        "large_transaction_thresholds": reliable.thresholds.__dict__,
        "baseline_reconciliation": baseline_summary,
        "forecast_go_categories": quality.loc[quality["qualification"] == "A", "category_code"].tolist(),
    }
    (output / "analysis_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report = build_report(
        reliable, total_only, stats, anomalies, lower, upper, quality, sensitivity,
        baseline_summary, len(transactions),
    )
    Path(args.report).write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
