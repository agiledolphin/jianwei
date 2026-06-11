# 见微 Jianwei — 开发计划

> 自动选股软件，取「见微知著」之意：从细微的市场信号中发现选股机会。
> 聚焦中国 A 股 · 可进化选股算法 · 收益评估 · 模拟演练 · 跨平台桌面应用（优先 macOS）。

## 1. 技术栈与版本

版本核对日期：2026-06-11（均为当日各仓库最新发布版）。

### 桌面端

| 依赖 | 版本 | 说明 |
|---|---|---|
| Tauri | 2.11.2（crate / CLI） | 桌面壳，@tauri-apps/api 2.11.0 |
| Vite | 8.0.16 | 前端构建 |
| React | 19.2.7 | UI 框架 |
| TypeScript | 6.0.3 | |
| klinecharts | 10.0.0-beta3 | K 线图。10 为官方 latest 标签；若遇阻退回 9.x 稳定版 |
| ECharts | 6.1.0 | 净值曲线、回测图表 |
| Rust | 1.96.0（本机已装） | |

应用标识符：`org.azuresky.jianwei`（tauri.conf.json，用于打包/签名与 macOS 应用数据目录命名）。

### Python 量化引擎

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.13（uv 管理） | 见下方说明 ① |
| AkShare | 1.18.64 | A 股数据源（免费） |
| pandas | 3.0.3 | |
| DuckDB | 1.5.3 | 本地行情库（配合 Parquet） |
| vectorbt | 1.0.0 | 可选：向量化批量计算加速（回测内核自研，见 4.1） |
| Optuna | 4.9.0 | 策略参数进化 |
| FastAPI | 0.136.3 + uvicorn 0.49.0 | 引擎本地 API |
| pydantic | 2.13.4 | |
| APScheduler | 3.11.2 | 引擎内定时调度（每日同步/信号生成） |
| SQLite | 标准库 | 账户、流水、策略注册表等事务型数据 |

> ① 本机 Python 为 3.14，但 vectorbt 依赖 numba，numba 对最新 CPython 的支持历来滞后，C 扩展轮子覆盖也以 3.13 最稳。引擎用 uv 锁定 3.13 独立环境，不影响系统 Python。这是「尽量最新」原则下唯一一处保守选择。

依赖管理：前端 npm + 引擎 uv（pyproject.toml + uv.lock），全部锁版本。

## 2. 架构

```
┌──────────────────────────────────────────────┐
│ React 19 + Vite 8（选股 / 回测 / 模拟盘 / 策略管理）│
│            ↕ HTTP (127.0.0.1 随机端口 + token)   │
│ Python 引擎 (FastAPI)：数据 / 因子 / 回测 / 进化 / │
│                        调度 (APScheduler)       │
│            ↕                                  │
│ DuckDB + Parquet（行情/因子/回测） SQLite（账户/状态）│
└──────────────────────────────────────────────┘
   Rust (Tauri 2.11)：纯壳 —— 窗口 + sidecar 托管
```

职责边界（Rust 压为纯壳，业务集中在一种语言里）：

- **Python 引擎**：全部量化逻辑 + 定时调度（APScheduler，引擎为常驻进程）。可脱离 UI 独立运行（CLI + pytest 驱动），先于 UI 开发验证。
- **Rust 壳**：仅负责窗口和 Python sidecar 生命周期管理（启动 / 健康检查 / 崩溃重启），把引擎端口与 token 注入前端。不写业务逻辑，目标 ≤ 200 行。
- **React 前端**：纯展示交互，拿到端口后直接 HTTP 访问引擎；长任务（回测/训练）通过 task_id 轮询或 SSE 接收进度。

## 3. 目录结构

```
jianwei/
├── PLAN.md
├── apps/desktop/
│   ├── src/                  # React + TS
│   │   ├── pages/            # 选股、回测、模拟盘、策略管理
│   │   ├── components/       # K线（klinecharts）、图表（ECharts）
│   │   └── api/              # 引擎 API 封装
│   └── src-tauri/            # Rust：sidecar 托管、调度、命令
├── engine/
│   ├── pyproject.toml        # uv 管理
│   ├── jianwei/
│   │   ├── data/             # AkShare 同步、交易日历、前复权、增量更新、质量校验
│   │   ├── factors/          # 因子库：估值 / 质量 / 动量 / 资金流
│   │   ├── strategy/         # 策略注册表（规则打分型起步），版本化
│   │   ├── backtest/         # 自研日频调仓模拟器，A 股约束内置
│   │   ├── sim/              # 模拟盘：撮合、账户、持仓、流水
│   │   ├── evolve/           # Optuna 参数寻优、champion/challenger
│   │   ├── report/           # 收益指标、基准对比、归因
│   │   ├── api/              # FastAPI 路由、异步任务队列、APScheduler 调度
│   │   └── cli.py            # 命令行入口（sync / select / backtest / report）
│   └── tests/
├── data/                     # market.duckdb + app.sqlite + Parquet（gitignore）
└── docs/
```

## 4. 核心设计要点

### 4.1 A 股交易约束（回测与模拟盘共用一套规则）

T+1、涨跌停封板属于路径依赖约束，vectorbt 的纯向量化模型无法直接表达，因此回测内核采用**自研日频调仓模拟器**（约束作为一等公民，逐日撮合、可单测），vectorbt 留作批量指标计算的可选加速。以下约束为阶段一验收标准：

- **T+1**：当日买入次日才能卖出
- **涨跌停**：触及涨停不可买入、跌停不可卖出（按 10% / ST 5% / 创业板与科创板 20% 区分）
- **费用**：佣金（万 2.5、最低 5 元）+ 卖出印花税 0.05% + 滑点
- **停牌**：停牌日跳过，复牌后按规则处理

### 4.2 选股策略：规则打分型起步

多因子打分模型：每只股票按估值（PE/PB 分位）、质量（ROE、毛利率）、动量（区间收益、均线形态）、资金流等因子打分，加权合成总分，取 Top N。

- 因子权重与筛选阈值 = 策略参数，全部可被 Optuna 优化 → 构成「可进化」闭环
- 策略实例 = 代码版本 + 参数 + 数据区间，落库版本化
- **champion/challenger 晋升机制**：Optuna 寻优产生 challenger → 模拟盘试运行 → 样本外指标优于现役 champion 才晋升上线
- ML 路线（Microsoft Qlib 滚动训练）作为阶段四增强，不在前期引入

### 4.2.1 存储分工与位置

- **DuckDB + Parquet**：行情、因子、回测结果——「一次写入、反复分析」的列存场景
- **SQLite**：模拟盘账户、持仓、交易流水、策略注册表、任务状态——「频繁小事务、可靠写入」场景（DuckDB 单写者模型不适合）

数据目录（引擎统一经 `JIANWEI_DATA_DIR` 环境变量解析，未设置时取缺省值）：

- 开发期缺省：`<项目根>/data/`（gitignore）
- 分发期 macOS 缺省：`~/.jianwei/data/`

### 4.3 收益评估

- 指标：年化收益、最大回撤、夏普 / 卡玛、胜率、盈亏比、换手率
- 基准对比：沪深 300 / 中证 500 超额收益曲线
- 分年度、分行业归因

### 4.4 模拟盘

- 每日收盘后生成信号 → 次日开盘价撮合（遵守 4.1 全部约束）
- 账户净值、持仓、流水落库；UI 展示净值 vs 基准
- 兼作策略晋升前的灰度验证环境

## 5. 实施阶段

### 阶段一 · 引擎骨架（先行，CLI 可跑）
1. Monorepo 脚手架：engine（uv + pyproject）+ apps/desktop（Tauri 2.11 + Vite 8 + React 19）
2. 数据层：AkShare 同步 A 股日线 / 基本面入 DuckDB，增量更新，质量校验
3. 规则型多因子选股器（首个内置策略）
4. 自研约束回测内核 + 指标报告
- **验收**：`jianwei sync && jianwei backtest` 命令行跑通，约束项有测试覆盖

### 阶段二 · 桌面应用
1. Rust 托管 Python sidecar（开发期 uv run，健康检查、崩溃重启）
2. 页面：选股结果（含 K 线）、回测报告（净值曲线、指标卡）、数据同步状态
3. 长任务异步：task_id + 进度事件
- **验收**：macOS 上 `tauri dev` 完整走通「同步 → 选股 → 回测 → 看报告」

### 阶段三 · 模拟盘与进化
1. 模拟撮合、账户系统、每日调度（引擎内 APScheduler 触发）
2. Optuna 参数寻优 + champion/challenger 晋升流程
3. 模拟盘页面：净值曲线、持仓、交易流水、策略对比
- **验收**：模拟盘可连续多日自动运行；challenger 晋升有完整记录

### 阶段四 · ML 与分发
1. Qlib 滚动训练接入（ML 选股策略）
2. PyInstaller 打包引擎为单文件二进制，注册 Tauri externalBin
3. macOS 签名、公证、DMG 分发；其后 Windows / Linux
- **验收**：无开发环境的 Mac 可直接安装运行

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| AkShare 接口偶发失效（爬虫聚合） | 数据层抽象 DataSource 接口，BaoStock 兜底日线；重试 + 缺口检测 |
| Python sidecar 打包（C 扩展坑多） | 推迟到阶段四独立处理；开发期 uv run 即可 |
| klinecharts 10 仍为 beta | 封装独立组件，API 不稳则退 9.x，影响面限制在单组件 |
| pandas 3.0 较新，AkShare 兼容性待验证 | 阶段一首日实测；不兼容则 pandas 降 2.x（锁定记录在案） |
| numba / vectorbt 对新 CPython 滞后 | 引擎锁 Python 3.13（已纳入选型） |

## 7. 合规说明

自用不涉及牌照问题。若未来对外发布并提供荐股功能，国内需证券投资咨询资质，分发前需评估。
