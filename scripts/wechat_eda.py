"""Privacy-safe exploratory analysis for a WeChat Pay Excel export.

The script reads a private workbook in place and writes only aggregate Markdown.
It never writes transaction-level rows, counterparties, product descriptions,
payment-account details, transaction IDs, or notes.
"""

from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {
    "交易时间",
    "交易类型",
    "交易对方",
    "商品",
    "收/支",
    "金额(元)",
    "支付方式",
    "当前状态",
    "交易单号",
    "商户单号",
    "备注",
}

CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("餐饮", ("餐", "饭", "美食", "外卖", "咖啡", "奶茶", "茶饮", "烧烤", "火锅", "面包", "蛋糕", "甜品", "酸奶", "食堂")),
    ("交通", ("滴滴", "高德", "顺风", "铁路", "铁旅", "火车", "机场", "航空", "地铁", "公交", "加油", "石油", "停车", "打车", "车票")),
    ("购物", ("淘宝", "京东", "拼多多", "商城", "超市", "便利", "百货", "商店", "购物", "服饰", "鞋", "彩妆", "护肤", "车品")),
    ("居住与缴费", ("物业", "房租", "水费", "电费", "燃气", "话费", "宽带", "酒店", "住宿", "公寓")),
    ("医疗健康", ("医院", "诊所", "药房", "药店", "医疗", "健康", "体检")),
    ("教育", ("学校", "教育", "大学", "学院", "考试", "书店", "课程", "培训")),
    ("数字服务", ("icloud", "apple", "云服务", "软件", "会员", "订阅", "充值中心")),
    ("娱乐", ("电影", "影院", "ktv", "游戏", "景区", "乐园", "演出", "票务")),
)


@dataclass(frozen=True)
class WorkbookStructure:
    sheet_names: list[str]
    header_row_1based: int
    raw_rows_after_header: int
    columns: list[str]


def find_header_row(path: Path, sheet_name: str) -> int:
    preview = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=50)
    for row_index, row in preview.iterrows():
        values = {str(value).strip() for value in row.dropna().tolist()}
        if len(REQUIRED_COLUMNS.intersection(values)) >= 8:
            return int(row_index)
    raise ValueError("未能在前 50 行识别微信账单表头。")


def read_bill(path: Path) -> tuple[pd.DataFrame, WorkbookStructure]:
    book = pd.ExcelFile(path)
    if not book.sheet_names:
        raise ValueError("Excel 文件不包含工作表。")
    sheet_name = book.sheet_names[0]
    header_row = find_header_row(path, sheet_name)
    frame = pd.read_excel(path, sheet_name=sheet_name, header=header_row)
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"缺少必要字段：{sorted(missing)}")
    structure = WorkbookStructure(
        sheet_names=list(book.sheet_names),
        header_row_1based=header_row + 1,
        raw_rows_after_header=len(frame),
        columns=list(frame.columns),
    )
    return frame, structure


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def classify_category(text: str) -> str:
    for category, keywords in CATEGORY_RULES:
        if contains_any(text, keywords):
            return category
    return "其他消费"


def prepare_transactions(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    data = raw.copy()
    data["交易时间"] = pd.to_datetime(data["交易时间"], errors="coerce")
    data["金额(元)"] = pd.to_numeric(data["金额(元)"], errors="coerce")
    for column in ("交易类型", "交易对方", "商品", "收/支", "当前状态", "备注"):
        data[column] = data[column].fillna("").astype(str).str.strip()

    invalid_time = data["交易时间"].isna()
    invalid_amount = data["金额(元)"].isna() | (data["金额(元)"] < 0)
    empty_direction = ~data["收/支"].isin(["收入", "支出", "/"])
    valid = ~(invalid_time | invalid_amount | empty_direction)
    cleaned = data.loc[valid].copy()

    tx_type = cleaned["交易类型"]
    direction = cleaned["收/支"]
    status = cleaned["当前状态"]
    is_internal = tx_type.isin(["零钱充值", "零钱提现"])
    is_transfer = tx_type.eq("转账") | tx_type.str.startswith("转账-退款")
    is_red_packet = tx_type.str.startswith("微信红包")
    is_purchase_refund = (
        tx_type.str.endswith("-退款")
        & ~tx_type.str.startswith("转账-退款")
        & ~tx_type.str.startswith("微信红包-退款")
    ) | (direction.eq("收入") & status.str.contains("退款", regex=False) & ~is_transfer & ~is_red_packet)
    is_core_spend = direction.eq("支出") & ~is_internal & ~is_transfer & ~is_red_packet
    is_income_candidate = direction.eq("收入") & ~is_internal & ~is_transfer & ~is_red_packet & ~is_purchase_refund

    conditions = [
        is_internal,
        is_transfer & tx_type.str.contains("退款", regex=False),
        is_transfer,
        is_red_packet & tx_type.str.contains("退款", regex=False),
        is_red_packet,
        is_purchase_refund,
        is_core_spend,
        is_income_candidate,
    ]
    labels = [
        "零钱充值/提现",
        "转账退款",
        "转账",
        "红包退款",
        "红包",
        "消费退款",
        "核心消费支出",
        "外部收入候选",
    ]
    cleaned["资金流分类"] = np.select(conditions, labels, default="其他特殊交易")

    private_text = (
        cleaned["交易类型"] + " " + cleaned["交易对方"] + " " + cleaned["商品"]
    )
    cleaned["消费类别"] = private_text.map(classify_category)
    cleaned["消费毛额"] = np.where(cleaned["资金流分类"].eq("核心消费支出"), cleaned["金额(元)"], 0.0)
    cleaned["退款冲减"] = np.where(cleaned["资金流分类"].eq("消费退款"), cleaned["金额(元)"], 0.0)
    cleaned["消费净额"] = cleaned["消费毛额"] - cleaned["退款冲减"]
    cleaned["日期"] = cleaned["交易时间"].dt.normalize()
    cleaned["周起始日"] = cleaned["日期"] - pd.to_timedelta(cleaned["日期"].dt.weekday, unit="D")
    cleaned["月份"] = cleaned["日期"].dt.to_period("M").astype(str)

    diagnostics = {
        "invalid_time_rows": int(invalid_time.sum()),
        "invalid_amount_rows": int(invalid_amount.sum()),
        "invalid_direction_rows": int(empty_direction.sum()),
        "invalid_rows": int((~valid).sum()),
        "duplicate_transaction_ids": int(
            cleaned["交易单号"].astype(str).duplicated(keep=False).sum()
        ),
    }
    return cleaned, diagnostics


def complete_week_index(min_date: pd.Timestamp, max_date: pd.Timestamp) -> pd.DatetimeIndex:
    first_monday = min_date + pd.Timedelta(days=(7 - min_date.weekday()) % 7)
    last_sunday = max_date - pd.Timedelta(days=(max_date.weekday() + 1) % 7)
    if first_monday + pd.Timedelta(days=6) > last_sunday:
        return pd.DatetimeIndex([])
    return pd.date_range(first_monday, last_sunday - pd.Timedelta(days=6), freq="W-MON")


def safe_cv(mean: float, std: float) -> float:
    return float(std / abs(mean)) if not math.isclose(mean, 0.0, abs_tol=1e-12) else math.nan


def money(value: float) -> str:
    return f"¥{value:,.2f}"


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def number(value: float) -> str:
    return "不适用" if pd.isna(value) or math.isinf(value) else f"{value:.2f}"


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def build_report(raw: pd.DataFrame, data: pd.DataFrame, structure: WorkbookStructure, diagnostics: dict[str, int]) -> str:
    min_time = data["交易时间"].min()
    max_time = data["交易时间"].max()
    min_date = min_time.normalize()
    max_date = max_time.normalize()
    coverage_days = int((max_date - min_date).days + 1)
    active_days = int(data["日期"].nunique())
    month_count = int(data["交易时间"].dt.to_period("M").nunique())
    full_months = pd.period_range(min_date.to_period("M") + 1, max_date.to_period("M") - 1, freq="M")

    all_days = pd.date_range(min_date, max_date, freq="D")
    daily = data.groupby("日期")[["消费毛额", "退款冲减", "消费净额"]].sum().reindex(all_days, fill_value=0.0)
    daily.index.name = "日期"
    daily["是否周末"] = daily.index.weekday >= 5
    day_type = daily.groupby("是否周末")["消费净额"].agg(["mean", "median", "sum"])
    weekday_mean = float(day_type.loc[False, "mean"])
    weekend_mean = float(day_type.loc[True, "mean"])
    weekend_ratio = weekend_mean / weekday_mean if weekday_mean else math.nan

    week_index = complete_week_index(min_date, max_date)
    weekly_all = data.groupby("周起始日")[["消费毛额", "退款冲减", "消费净额"]].sum()
    weekly = weekly_all.reindex(week_index, fill_value=0.0)
    weekly.index.name = "周起始日"
    weekly_stats = weekly["消费净额"].describe(percentiles=[0.25, 0.5, 0.75])
    weekly_mean = float(weekly_stats["mean"])
    weekly_std = float(weekly_stats["std"])
    weekly_cv = safe_cv(weekly_mean, weekly_std)
    zero_weeks = int((weekly["消费毛额"] == 0).sum())
    negative_net_weeks = int((weekly["消费净额"] < 0).sum())
    lag1 = float(weekly["消费净额"].autocorr(lag=1)) if len(weekly) > 2 else math.nan

    q1 = float(weekly_stats["25%"])
    q3 = float(weekly_stats["75%"])
    weekly_iqr = q3 - q1
    high_week_threshold = q3 + 1.5 * weekly_iqr
    high_weeks = weekly.loc[weekly["消费净额"] > high_week_threshold, "消费净额"].sort_values(ascending=False)
    high_week_share = float(high_weeks.sum() / weekly["消费净额"].sum()) if weekly["消费净额"].sum() else math.nan

    if len(weekly):
        x = np.arange(len(weekly), dtype=float)
        trend_slope = float(np.polyfit(x, weekly["消费净额"].to_numpy(), 1)[0])
        trend_pct = trend_slope / weekly_mean if weekly_mean else math.nan
    else:
        trend_slope = math.nan
        trend_pct = math.nan

    core = data.loc[data["资金流分类"].eq("核心消费支出")].copy()
    spend_q1 = float(core["金额(元)"].quantile(0.25))
    spend_q3 = float(core["金额(元)"].quantile(0.75))
    large_threshold = spend_q3 + 1.5 * (spend_q3 - spend_q1)
    large = core.loc[core["金额(元)"] > large_threshold]
    large_share = float(large["金额(元)"].sum() / core["金额(元)"].sum()) if core["金额(元)"].sum() else math.nan
    weekly_without_large = (
        core.loc[core["金额(元)"] <= large_threshold]
        .groupby("周起始日")["金额(元)"]
        .sum()
        .reindex(week_index, fill_value=0.0)
    )
    cv_without_large = safe_cv(float(weekly_without_large.mean()), float(weekly_without_large.std()))

    flow = data.groupby("资金流分类")["金额(元)"].agg(["count", "sum"]).sort_values("sum", ascending=False)
    flow_rows = [[index, int(row["count"]), money(float(row["sum"]))] for index, row in flow.iterrows()]

    monthly = data.groupby("月份")[["消费毛额", "退款冲减", "消费净额"]].sum().sort_index()
    monthly_rows = []
    full_month_set = {str(period) for period in full_months}
    for index, row in monthly.iterrows():
        monthly_rows.append([
            index,
            "完整月" if index in full_month_set else "部分月",
            money(float(row["消费毛额"])),
            money(float(row["退款冲减"])),
            money(float(row["消费净额"])),
        ])

    category_tx = data.loc[data["资金流分类"].isin(["核心消费支出", "消费退款"])].copy()
    category_amount = category_tx.groupby("消费类别")[["消费毛额", "退款冲减", "消费净额"]].sum()
    category_count = core.groupby("消费类别").size().rename("frequency")
    category_week = category_tx.pivot_table(
        index="周起始日", columns="消费类别", values="消费净额", aggfunc="sum", fill_value=0.0
    ).reindex(week_index, fill_value=0.0)
    category_gross_week = core.pivot_table(
        index="周起始日", columns="消费类别", values="金额(元)", aggfunc="sum", fill_value=0.0
    ).reindex(week_index, fill_value=0.0)
    total_net = float(category_amount["消费净额"].sum())
    category_rows: list[list[object]] = []
    category_model_candidates: list[str] = []
    sparse_categories: list[str] = []
    for category, row in category_amount.sort_values("消费净额", ascending=False).iterrows():
        series = category_week[category] if category in category_week else pd.Series(0.0, index=week_index)
        gross_series = category_gross_week[category] if category in category_gross_week else pd.Series(0.0, index=week_index)
        mean = float(series.mean())
        std = float(series.std())
        cv = safe_cv(mean, std)
        active_weeks = int((gross_series > 0).sum())
        frequency = int(category_count.get(category, 0))
        share = float(row["消费净额"] / total_net) if total_net else math.nan
        if active_weeks >= 30 and frequency >= 50 and share >= 0.05:
            category_model_candidates.append(category)
        if active_weeks < 20 or frequency < 30:
            sparse_categories.append(category)
        category_rows.append([
            category,
            frequency,
            active_weeks,
            money(float(row["消费净额"])),
            pct(share),
            number(cv),
            "较稳定" if active_weeks >= 30 and cv <= 1.0 else ("较波动" if active_weeks >= 20 else "稀疏"),
        ])

    anomaly_rows = [[index.strftime("%Y-%m-%d"), money(float(value))] for index, value in high_weeks.head(8).items()]
    if not anomaly_rows:
        anomaly_rows = [["无", "无超过 IQR 上界的完整周"]]

    model_candidate_text = "、".join(category_model_candidates) if category_model_candidates else "暂无类别满足单独建模的最低覆盖条件"
    sparse_text = "、".join(sparse_categories) if sparse_categories else "无"
    trend_assessment = (
        "存在需要进一步验证的上升趋势" if trend_pct > 0.01 else
        "存在需要进一步验证的下降趋势" if trend_pct < -0.01 else
        "未见强线性趋势"
    )
    variability = "较高" if weekly_cv > 0.75 else ("中等" if weekly_cv > 0.4 else "较低")

    return f"""# 微信支付账单真实数据 EDA 报告

## 1. 分析口径与隐私说明

- 数据源：本地提供的微信支付账单 Excel；脚本原地只读，不复制原始文件。
- 本报告仅包含字段结构、计数、金额汇总和时间聚合，不输出交易对象、姓名、账号、商品明细、交易单号或备注。
- 周定义：周一至周日。建模可用周仅采用完整覆盖的自然周，首尾不完整周不进入完整周统计。
- 核心消费口径：支出中排除转账、红包、零钱充值和提现；消费退款作为负向金额冲减。转账和红包因无法可靠判断用途，单独标记，不当作日常消费。
- “外部收入候选”仅表示账单结构上不是退款、转账、红包或内部资金流的微信入账，不能直接认定为工资或完整收入。
- 生活消费类别来自通用关键词规则，只用于 EDA。微信导出没有可靠的标准生活类别字段，因此这些类别必须在后续系统中支持用户确认和纠错。

复现命令：

```powershell
python scripts/wechat_eda.py --input <私有账单路径> --report docs/data-eda-report.md
```

## 2. A. 数据结构

- 工作表：{len(structure.sheet_names)} 个（主表名称：`{structure.sheet_names[0]}`）。
- 真实表头：第 {structure.header_row_1based} 行；前 {structure.header_row_1based - 1} 行为导出说明。
- 字段数量：{len(structure.columns)} 个。
- 字段：`{'`、`'.join(structure.columns)}`。
- 表头后原始记录：{structure.raw_rows_after_header:,} 条。
- 有效记录：{len(data):,} 条；无效记录：{diagnostics['invalid_rows']:,} 条。
- 重复交易单号涉及记录：{diagnostics['duplicate_transaction_ids']:,} 条。
- 时间范围：{min_time:%Y-%m-%d %H:%M:%S} 至 {max_time:%Y-%m-%d %H:%M:%S}。
- 覆盖自然日：{coverage_days:,} 天；存在交易的自然日：{active_days:,} 天。
- 完整周：{len(week_index):,} 个；观察到的月份：{month_count:,} 个，其中完整自然月 {len(full_months):,} 个。

字段类型：交易时间解析为日期时间，金额解析为数值，其余字段按文本处理。当前数据的必要字段非空且金额、时间可以解析；状态字段存在带具体退款金额的多种字符串，后续标准化时必须按模式归一化，不能把每个退款金额视为独立状态。

## 3. B. 交易与资金流类型

{markdown_table(['归一化资金流', '记录数', '原始金额合计'], flow_rows)}

处理建议：

- 核心消费支出进入消费分析；消费退款以负向冲减，不作为新收入。
- 外部收入候选进入收入核验区，但需用户确认来源；当前微信账单不能代表完整收入。
- 转账、转账退款、红包及红包退款单独保留，不直接进入日常消费或稳定收入。
- 零钱充值和提现属于账户内部资金流转，排除消费和收入分析。
- 退款不能简单删除原支付记录；净消费采用原支出减退款，并保留毛额和退款额供审计。

## 4. C—D. 日度、周度和月度消费

脚本已按同一核心口径构造零填充的日度序列、周一至周日的周度序列和自然月序列。完整周净消费统计如下：

{markdown_table(
    ['指标', '结果'],
    [
        ['完整周数', len(weekly)],
        ['均值', money(float(weekly_stats['mean']))],
        ['中位数', money(float(weekly_stats['50%']))],
        ['标准差', money(float(weekly_stats['std']))],
        ['变异系数', number(weekly_cv)],
        ['最小值', money(float(weekly_stats['min']))],
        ['25% 分位数', money(float(weekly_stats['25%']))],
        ['75% 分位数', money(float(weekly_stats['75%']))],
        ['最大值', money(float(weekly_stats['max']))],
        ['零毛消费周', zero_weeks],
        ['净额为负的周', negative_net_weeks],
        ['滞后 1 周相关系数', number(lag1)],
    ],
)}

自然月汇总：

{markdown_table(['月份', '覆盖状态', '消费毛额', '退款冲减', '消费净额'], monthly_rows)}

### 异常周与大额交易影响

- 完整周高异常阈值（Q3 + 1.5×IQR）：{money(high_week_threshold)}。
- 高异常周数量：{len(high_weeks)} 个；其净消费占完整周净消费的 {pct(high_week_share)}。

{markdown_table(['异常周起始日', '周净消费'], anomaly_rows)}

- 单笔大额统计阈值（消费交易 Q3 + 1.5×IQR）：{money(large_threshold)}。
- 超过阈值的消费记录：{len(large):,} 条，金额合计 {money(float(large['金额(元)'].sum()))}，占核心消费毛额 {pct(large_share)}。
- 完整周净消费变异系数为 {number(weekly_cv)}；移除上述大额消费后，周毛消费变异系数为 {number(cv_without_large)}。该对比说明大额交易对波动的影响，但不能据此认定交易“计划外”，计划属性必须由用户确认。

## 5. E. 消费分类

{markdown_table(['暂定类别', '消费频次', '活跃周', '净金额', '金额占比', '周度CV', '描述'], category_rows)}

解释：

- 金额占比基于核心消费净额；退款按同一关键词规则尽量回冲类别。
- “较稳定/较波动/稀疏”只描述观测频率和统计波动，不表示该类别是必须支出或可削减支出。
- “其他消费”占比较高时，说明微信字段不足以支持可靠自动分类，V1 必须提供分类确认和修正机制。
- 可能适合未来单独测试预测的类别：{model_candidate_text}。
- 当前样本太少、不值得单独建模的类别：{sparse_text}。

## 6. F. 时间规律

- 工作日零填充日均净消费：{money(weekday_mean)}。
- 周末零填充日均净消费：{money(weekend_mean)}，为工作日的 {number(weekend_ratio)} 倍。
- 完整周线性趋势斜率：每周 {money(trend_slope)}，约为周均值的 {pct(trend_pct)}；结论为“{trend_assessment}”。一年数据不足以确认年度季节性。
- 周度波动程度：{variability}；高异常周对均值的影响见上一节。后续模型评价应同时报告 MAE 和对大额周的误差，避免仅用均值描述典型消费。

月份差异可以从自然月表直接观察，但首尾部分月不可与完整月直接比较；完整月之间的差异可能同时包含季节、假期、大额事件和数据源使用习惯变化，不能只凭月份归因。

## 7. G. 建模可行性判断

1. **周度建模是否合理：**合理，但应限定为短期、可解释的滚动预测。{len(weekly)} 个完整周明显优于直接使用 {len(full_months)} 个完整月建模。
2. **实际可用周数：**{len(weekly)} 个完整自然周；首尾不完整周仅用于描述，不用于标准回测。
3. **是否存在大量 0 消费周：**零毛消费周 {zero_weeks} 个，占 {pct(zero_weeks / len(weekly) if len(weekly) else math.nan)}，{'不存在大量零周' if len(weekly) and zero_weeks / len(weekly) <= 0.1 else '零周比例需要在建模中专门处理'}。
4. **周度消费是否足够稳定：**周度变异系数 {number(weekly_cv)}，波动{variability}。可建立基线并量化不确定性，但不应承诺精确点预测。
5. **总消费是否适合预测：**适合作为首要预测对象，因为覆盖最完整；应同时保留包含/排除统计大额交易的敏感性分析。
6. **适合单独预测的主要类别：**{model_candidate_text}。最终资格仍需滚动回测确认。
7. **样本太少的类别：**{sparse_text}。这些类别宜合并为“其他”或只做规则预算，不单独训练模型。
8. **预测未来 1/2/4 周：**1 周预测最有依据；2 周可以测试但不确定性更高；4 周仅适合提供区间或月度规划参考，不能当作精确承诺。
9. **是否使用滚动预测：**应该。每周加入最新实际值并重新估计，既符合执行周期，也能适应临时支出和行为变化。
10. **月度预测方式：**应由周度预测汇总，但跨月周必须按日期或已验证的日内权重分摊到自然月，不能把整周重复计入或机械归入单一月份。

## 8. H. 模型候选建议

建议进入后续对照实验：

1. 历史完整周均值基线。
2. 最近 4 周移动平均基线。
3. 简单指数平滑，用于允许近期消费获得更高权重。
4. Holt 仅在滚动训练窗内持续检出趋势且优于基线时保留；当前趋势判断为“{trend_assessment}”。
5. 简单 ARIMA 只测试低阶规格。当前滞后 1 周相关系数为 {number(lag1)}，样本量不支持大规模参数搜索。
6. 带少量滞后和日历特征的正则化线性回归可作为可解释候选，但必须限制特征数并使用时序回测。

不建议 LSTM、Transformer 或复杂季节模型：可用完整周只有 {len(weekly)} 个，一年数据也不足以学习可靠年度季节性。模型选择应以滚动回测 MAE、稳定性和解释性为准，而不是以复杂度为准。

## 9. I. 数据缺失与多源偏差

当前结果仅描述微信可观测资金流。若用户同时大量使用支付宝、银行卡或现金，会出现：

- 总消费和真实收入被系统性低估；
- 类别占比偏向微信常用场景；
- 固定支出、房租、工资和银行卡自动扣款可能缺失；
- 在不同支付渠道之间切换时产生伪趋势；
- 同一笔跨平台转账可能被误识别为收入或支出。

未来统一标准至少需要：来源、源记录 ID、交易时间、金额、币种、方向、交易状态、资金流类型、标准类别、对方匿名标识、支付账户匿名标识、退款/原交易关联、导入批次和解析器版本。多源合并后应使用时间、金额、方向和匿名账户线索做去重与内部转账匹配，同时保留人工确认入口。

## 10. 结论与限制

真实微信账单在结构完整性、时间跨度和周度覆盖上可用于本科项目的 EDA、简单短期预测基线与滚动规划案例。核心限制不是记录数量，而是单一支付渠道、生活类别缺失、转账用途不明和大额事件造成的高波动。项目应继续，但 V1 必须把“数据覆盖说明、类别确认、资金流排除规则、基线优先和滚动回测”作为正式要求。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成隐私安全的微信账单聚合 EDA 报告。")
    parser.add_argument("--input", required=True, type=Path, help="本地私有微信账单路径")
    parser.add_argument("--report", required=True, type=Path, help="聚合 Markdown 报告输出路径")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input.is_file():
        raise FileNotFoundError(f"找不到账单文件：{args.input}")
    raw, structure = read_bill(args.input)
    data, diagnostics = prepare_transactions(raw)
    report = build_report(raw, data, structure, diagnostics)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(f"EDA 完成：有效记录 {len(data)} 条；聚合报告已写入 {args.report}")


if __name__ == "__main__":
    main()

