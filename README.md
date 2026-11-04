# 面向储蓄目标的个性化消费预测与动态预算优化系统

> 不只记录“钱花到哪里”，还会预测“未来可能花多少”，并围绕储蓄目标计算“接下来应该怎么花”。

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue_3-Frontend-42B883?logo=vuedotjs&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?logo=sqlite&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-Automated-success)
![Privacy](https://img.shields.io/badge/Privacy-Local_First-blueviolet)

## 30 秒了解项目

这是一个以**真实个人账单、消费预测和储蓄目标**为核心的智能预算系统。用户导入微信账单后，系统会完成解析、清洗、幂等去重、退款识别和消费分类；随后按自然周分析消费规律，预测未来消费，并结合当前存款、收入、目标金额和截止日期判断储蓄目标是否可行，生成可解释的分类预算。当发生超支、临时收入或计划外支出时，系统能够保留旧方案并滚动生成新版本。

它与普通记账应用的区别在于：**传统应用主要解释已经发生的消费，本项目进一步把历史账单转化为未来消费基线、储蓄可行性判断和可执行预算。**

## 核心亮点

| 能力 | 项目实现 |
| --- | --- |
| 真实账单数据管道 | 支持微信 Excel 账单解析、清洗、退款处理、同源幂等和 SQLite 持久化 |
| 隐私友好的混合分类 | 结合集中规则、公共品牌知识、可选 DeepSeek 候选、豆包公开商户检索与用户确认；AI 不直接篡改真实标签 |
| 周度消费预测 | 使用统一无泄漏数据集和滚动回测比较模型，以真实误差而不是模型复杂度选择生产方案 |
| 储蓄目标可行性 | 根据存款、收入、预测消费和目标期限，返回可行性、资金缺口及调整建议 |
| 个性化预算优化 | 使用 SciPy SLSQP，在固定支出、最低预算和调整意愿等约束下生成周×类别预算 |
| 动态滚动再规划 | 记录超支、临时收入等财务事件，基于旧计划生成新版本，并保留完整版本链 |
| 可解释与可复现 | 金额统一使用整数分；模型、数据集、规则和预算方案均有版本；实验结论来自真实运行结果 |
| 完整 Web 闭环 | Vue 3 前端与 FastAPI 后端覆盖导入、分析、目标、预算、执行反馈和历史方案 |

## 系统工作流

```mermaid
flowchart LR
    A[导入微信账单] --> B[解析·清洗·去重]
    B --> C[规则 + AI候选 + 人工确认]
    C --> D[日/周/月消费分析]
    D --> E[周度消费预测]
    E --> F[储蓄目标可行性判断]
    F --> G[约束预算优化]
    G --> H[实际消费与财务事件]
    H --> I[滚动再规划]
    I --> G
```

## 技术架构

```text
Vue 3 + Element Plus + ECharts
                ↓
          FastAPI REST API
                ↓
业务服务 ── 消费分析 ── 预测 ── 优化 ── 滚动再规划
                ↓
       SQLAlchemy 2.x + SQLite
```

## 当前状态

阶段 0—10 已形成完整本地 Web 闭环并完成核心对照实验；阶段 9.5 又统一了首次使用流程、页面引导与动态下一步。账单导入、消费分析、目标可行性、预算优化、事件反馈、滚动再规划和历史版本均可通过 FastAPI + Vue 使用。真实账单只在本地私有目录处理，没有复制到公开数据或 Git。

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
3. 查看消费分析；创建储蓄目标并确认每周至少保留的预算、固定支出和可接受调整程度。
4. 在预算页生成候选方案，先查看可行性与分类变化，再手动确认启用。
5. 在“本周执行”页记录模拟计划外支出，先预览旧/新方案差异，再确认新版本。
6. 在“方案记录”页查看只读的版本链及每次变化原因。

如需重新生成模拟账单：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\build_demo_bill.py
```

## 当前数据结论

真实微信账单包含 2,445 条有效记录和 51 个完整自然周。周度消费连续但波动较高，适合以简单基线为核心进行短期滚动预测，不适合复杂深度学习或直接用月度样本训练复杂模型。详见 `docs/data-eda-report.md` 和 `docs/requirements-v1.md`。
