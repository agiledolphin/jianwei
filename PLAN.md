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
| klinecharts | 9.8.12 | K 线图（稳定版，已验证可用） |
| ECharts | 6.1.0 | 净值曲线、回测图表 |
| Rust | 1.96.0（本机已装） | |

应用标识符：`org.azuresky.jianwei`（tauri.conf.json，用于打包/签名与 macOS 应用数据目录命名）。

### Python 量化引擎

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.13（uv 管理） | 见下方说明 ① |
| AkShare | 1.18.64 | A 股辅助数据（股票列表、沪深300成分） |
| Baostock | 0.9.2 | 主力日线数据源，有真实成交额/换手率，无 WAF |
| pandas | 2.2+ | |
| DuckDB | 1.5.3 | 本地行情库 |
| Optuna | 4.9.0 | 策略参数进化 |
| FastAPI | 0.136.3 + uvicorn 0.49.0 | 引擎本地 API |
| pydantic | 2.13.4 | |
| APScheduler | 3.11.2 | 引擎内定时调度（每日同步/信号生成） |
| SQLite | 标准库 | 账户、流水、策略注册表等事务型数据 |

> ① 本机 Python 为 3.14，但 C 扩展轮子覆盖以 3.13 最稳。引擎用 uv 锁定 3.13 独立环境。

依赖管理：前端 npm + 引擎 uv（pyproject.toml + uv.lock），全部锁版本。

## 2. 架构

```
┌──────────────────────────────────────────────┐
│ React 19 + Vite 8（选股 / 回测 / 模拟盘 / 策略管理）│
│            ↕ HTTP (127.0.0.1 随机端口 + token)   │
│ Python 引擎 (FastAPI)：数据 / 因子 / 回测 / 进化 / │
│                        调度 (APScheduler)       │
│            ↕                                  │
│ DuckDB（行情/因子/回测）  SQLite（账户/模拟盘/调度日志）│
└──────────────────────────────────────────────┘
   Rust (Tauri 2.11)：纯壳 —— 窗口 + sidecar 托管
```

职责边界（Rust 压为纯壳，业务集中在 Python）：

- **Python 引擎**：全部量化逻辑 + 定时调度（APScheduler，引擎为常驻进程）。可脱离 UI 独立运行（CLI + pytest 驱动）。
- **Rust 壳**：仅负责窗口和 Python sidecar 生命周期管理（启动 / 健康检查 / 崩溃重启），把引擎端口与 token 注入前端。目标 ≤ 200 行。
- **React 前端**：纯展示交互，拿到端口后直接 HTTP 访问引擎；长任务（回测/训练）轮询进度。

## 3. 目录结构

```
jianwei/
├── PLAN.md
├── apps/desktop/
│   ├── src/                  # React + TS
│   │   ├── pages/            # 选股、回测、模拟盘、策略进化、行情、数据
│   │   ├── components/       # K线（klinecharts）、图表（ECharts）
│   │   └── api.ts            # 引擎 API 封装
│   └── src-tauri/            # Rust：sidecar 托管、随机端口、token 鉴权
├── engine/
│   ├── pyproject.toml        # uv 管理
│   ├── jianwei/
│   │   ├── data/             # 三源切换（bs/tx/em）、DuckDB 存储、增量同步、质量校验
│   │   ├── factors/          # 因子库：动量 / 反转 / 低波动 / 流动性
│   │   ├── strategy/         # 多因子打分策略、SQLite 注册表
│   │   ├── backtest/         # 自研日频调仓模拟器，A 股约束内置
│   │   ├── sim/              # 模拟盘：SimBroker、账户、持仓、流水
│   │   ├── evolve/           # Optuna 参数寻优、champion/challenger
│   │   ├── report/           # 收益指标、基准对比
│   │   ├── scheduler.py      # APScheduler：每日 15:35 同步 + 选股 + 模拟调仓
│   │   ├── scheduler_log.py  # 调度运行日志（SQLite）
│   │   ├── api/              # FastAPI 路由（含 /sync /picks /backtest /sim/* /evolve/*）
│   │   └── cli.py            # 命令行入口（serve / evolve）
│   └── tests/
├── data/                     # market.duckdb + app.sqlite（gitignore）
└── docs/
```

## 4. 核心设计要点

### 4.1 数据源

三源切换，通过 `JIANWEI_DATA_SOURCE` 环境变量控制（默认 `bs`）：

| 源 | 标识 | 说明 |
|---|---|---|
| Baostock | `bs`（默认） | 真实成交额/换手率，专为量化设计，无 WAF，免费注册 |
| 腾讯财经 | `tx` | 无需登录，成交额为近似值（收盘价×成交量） |
| 东方财富 | `em` | 字段最全，部分网络被 WAF 拦截，探测失败自动降级 tx |

股票列表与沪深300成分通过 AkShare 获取（中证指数官网接口，300只当前成分）。

### 4.2 A 股交易约束（回测与模拟盘共用一套规则）

- **T+1**：当日买入次日才能卖出
- **涨跌停**：触及涨停不可买入、跌停不可卖出（主板 10% / ST 5% / 创业板科创板 20% / 北交所 30%）
- **费用**：佣金（万 2.5、最低 5 元）+ 卖出印花税 0.05% + 滑点
- **停牌**：停牌日跳过，复牌后按规则处理

### 4.3 选股策略：多因子打分

多因子截面 z-score 加权打分模型，取 Top N：

| 因子 | 权重 | 说明 |
|---|---|---|
| momentum_20 | 30% | 20 日动量 |
| momentum_60 | 30% | 60 日动量 |
| reversal_5 | 10% | 5 日反转（超跌反弹） |
| low_volatility_60 | 20% | 60 日低波动 |
| liquidity_20 | 10% | 20 日平均成交额对数 |

流动性硬过滤：20 日均成交额 ≥ 3000 万元。

因子权重与 Top N 全部可被 Optuna 优化，champion/challenger 机制控制策略晋升。

### 4.4 模拟盘

- APScheduler 每日 15:35 触发：同步行情 → 打分选股 → SimBroker 调仓
- 账户净值、持仓、流水落 SQLite；UI 实时查询
- 兼作策略晋升前的灰度验证环境

### 4.5 收益评估

- 指标：年化收益、最大回撤、夏普 / 卡玛、胜率、换手率、总手续费
- 基准对比：沪深 300 超额收益

### 4.6 存储分工

- **DuckDB**：行情日线、指数日线、同步状态——列存，批量分析
- **SQLite**：模拟盘账户/持仓/流水、策略注册表、调度日志——频繁小事务

数据目录（`JIANWEI_DATA_DIR` 环境变量覆盖）：
- 开发期缺省：`<项目根>/data/`
- 分发期 macOS 缺省：`~/.jianwei/data/`

## 5. 实施阶段

### ✅ 阶段一 · 引擎骨架（已完成）
- Monorepo 脚手架：engine（uv + pyproject）+ apps/desktop（Tauri 2.11 + Vite 8 + React 19）
- 数据层：AkShare 同步 A 股日线入 DuckDB，增量更新，质量校验
- 规则型多因子选股器（动量/反转/低波动/流动性）
- 自研约束回测内核 + 指标报告

### ✅ 阶段二 · 桌面应用（已完成）
- Rust 托管 Python sidecar（uv run，随机端口 + token 鉴权，父进程监听防孤儿）
- 6 页面：选股、K 线、回测、模拟盘、策略进化、数据同步
- DuckDB 读写锁分离（读路由 read_only=True，写路由独占）

### ✅ 阶段三 · 模拟盘与进化（已完成）
- SimBroker：等权调仓，复用回测约束（T+1/涨跌停/手续费）
- APScheduler：每日 15:35 自动同步 + 选股 + 模拟调仓，调度日志落库
- Optuna 走前进化：训练期优化 Sharpe，验证期与 champion 对比决定晋升
- 数据源重构：Baostock 为默认主源，三源统一出口，成交额/换手率字段真实可靠

### 🔲 阶段四 · 提升与分发
1. **降低回撤**：市场趋势过滤（沪深300 60日均线以下空仓），把最大回撤从 -52% 压缩到合理范围
2. **UI 图表**：回测 NAV 曲线（ECharts），K 线页均线/成交量图形渲染
3. **打包分发**：PyInstaller 打包引擎为单文件二进制，Tauri externalBin；macOS 签名、公证、DMG

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| AkShare 接口偶发失效 | 数据源三层兜底（bs → tx → em），重试 + 缺口检测 |
| Baostock 服务中断 | JIANWEI_DATA_SOURCE=tx 一键切换腾讯财经 |
| Python sidecar 打包（C 扩展坑多） | 推迟到阶段四独立处理；开发期 uv run 即可 |
| klinecharts 10 beta 不稳 | 已退回 9.x 稳定版（9.8.12） |
| 回撤过大影响实用性 | 阶段四加市场趋势过滤 |

## 7. 合规说明

自用不涉及牌照问题。若未来对外发布并提供荐股功能，国内需证券投资咨询资质，分发前需评估。
