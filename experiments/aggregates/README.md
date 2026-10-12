# 阶段 5 脱敏聚合产物

本目录由 `scripts/run_consumption_analysis.py` 从本地 SQLite 标准 Transaction 生成。这里不包含逐笔交易、交易对象、商户、账号、支付方式、原交易号或备注。

主要文件：

- `daily_consumption.csv`：连续日度总量，零消费日补零；
- `weekly_consumption.csv`：周一至周日总量、完整周标记和类别净额宽表；
- `monthly_consumption.csv`：自然月总量；
- `category_daily/week/monthly.csv`：类别聚合长表；
- `category_quality.csv`：类别覆盖、波动与阶段 6 资格；
- `refund_sensitivity.csv`：退款总量口径与可靠关联口径对照；
- `weekly_forecast_*.csv`：由唯一生成器产生的总量、餐饮和购物预测数据集；
- `analysis_summary.json`：关键机器可读结论；
- `figures/`：论文用静态聚合图表。

金额字段单位均为整数分；图表展示单位为元。预测数据集保留前几周的空 lag/rolling 值，阶段 6 必须在滚动切分后按模型需要处理，不能用未来数据填补。

重新生成示例：

```powershell
.venv\Scripts\python.exe -X utf8 scripts\run_consumption_analysis.py `
  --database data\private\app.sqlite3 `
  --output-dir experiments\aggregates `
  --report docs\consumption-analysis.md
```
