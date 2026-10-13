# 面向储蓄目标的个性化消费预测与动态预算优化系统

本科毕业设计项目。系统计划从微信、支付宝和银行卡账单中构建统一交易数据，以周为主要分析与执行周期，进行个体化消费预测、储蓄目标可行性判断、分类预算优化和滚动再规划。

## 当前状态

阶段 0—9 已形成完整本地 Web 闭环：账单导入、消费分析、预测、目标可行性、预算优化、事件反馈、滚动再规划和历史版本均可通过 FastAPI + Vue 使用。真实账单只在本地私有目录处理，没有复制到公开数据或 Git。

## 设计原则

- 通用算法框架与个体化参数估计分离。
- 数据结论和实验数字只来自可复现的真实运行结果。
- 优先使用可解释、可复现且适合约 52 周样本量的方法。
- 已发生交易只读；滚动再规划只改变未来预算。
- 真实账单、隐私字段、私有数据库和敏感日志不得进入 Git。
- 测试仅使用脱敏数据或模拟数据。

## 目录

```text
backend/             FastAPI 模块边界与框架无关领域契约
frontend/            Vue 3 + Element Plus + ECharts 前端
data/sample/         可提交的脱敏或模拟样本
data/private/        本地真实数据（Git 忽略）
docs/                项目、需求与设计文档
experiments/         可复现实验代码与非敏感结果
scripts/             数据检查与开发脚本
tests/               自动化测试
uploads/private/     本地私有上传区（Git 忽略）
```

## 隐私提示

请将真实账单放入 `data/private/`。不要把真实姓名、账号、交易对象或完整备注复制到文档、测试、日志或提交信息中。

## 技术栈

V1 采用 Vue 3、Element Plus、ECharts、FastAPI、SQLAlchemy、Pandas、NumPy 和 SQLite。预测实验使用 statsmodels；预算优化采用 SciPy SLSQP，实现带用户约束的凸二次目标，不引入 CVXPY 或商业求解器。

## 本地运行

要求：Python 3.11+、Node.js 20+、pnpm 9+。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
alembic upgrade head
python -m uvicorn backend.app.main:app --reload
```

后端默认位于 `http://127.0.0.1:8000`，Swagger 为 `http://127.0.0.1:8000/docs`。首次运行前请把 `.env` 中的 `SECRET_KEY` 换成长随机值；`.env` 和 SQLite 文件不会进入 Git。

新开终端启动前端：

```powershell
Set-Location frontend
pnpm install
pnpm run dev
```

浏览器访问 `http://localhost:5173`。前端开发服务器会把 `/api` 转发到 FastAPI。

## 测试与构建

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -X utf8 -m compileall -q backend tests scripts
Set-Location frontend
pnpm run test
pnpm run build
```

## 完全模拟的 Demo 流程

1. 在页面注册任意本地演示账号，密码至少 8 位；不要把密码提交到仓库。
2. 在“账单”页导入 `data/sample/wechat_bill_demo.xlsx`。该文件完全模拟，覆盖约一年。
3. 查看消费分析；创建储蓄目标并确认分类最低预算、固定支出和可调整程度。
4. 在预算页生成候选方案，查看可行性、周度分类预算和自然月汇总后手动激活。
5. 在动态调整页记录模拟计划外支出，先预览旧/新方案差异，再确认 Plan v2。
6. 在历史方案页查看只读的 Plan v1 → Plan v2 版本链。

如需重新生成模拟账单：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\build_demo_bill.py
```

## 当前数据结论

真实微信账单包含 2,445 条有效记录和 51 个完整自然周。周度消费连续但波动较高，适合以简单基线为核心进行短期滚动预测，不适合复杂深度学习或直接用月度样本训练复杂模型。详见 `docs/data-eda-report.md` 和 `docs/requirements-v1.md`。
