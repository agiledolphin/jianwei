# 见微 Jianwei

> 见微知著 —— 从细微的市场信号中发现选股机会。

聚焦中国 A 股的自动选股软件：内置可持续进化的多因子选股算法，提供严谨的收益评估与模拟演练环境。跨平台桌面应用，优先 macOS。

## 功能特性

- **多因子选股**：动量 / 反转 / 低波动 / 流动性等价量因子，截面 z-score 加权打分，参数可通过 Optuna 持续寻优进化（champion/challenger 晋升机制，规划中）
- **A 股约束回测**：自研日频调仓模拟器，T+1、涨跌停（主板 10% / 创业板与科创板 20% / 北交所 30% / 主板 ST 5%）、佣金（最低 5 元）、卖出印花税、滑点、停牌、整手交易全部内置——回测数字不骗人
- **收益评估**：年化收益、最大回撤、夏普 / 卡玛比率、胜率、换手率，对比沪深300 基准
- **本地数据**：行情增量同步入 DuckDB，双数据源自动切换（东方财富 / 腾讯财经），离线可用
- **策略版本化**：策略参数与每次回测结果落 SQLite 注册表，可追溯

## 架构

```
React 19 + Vite 8（选股 / 回测 / 模拟盘 / 策略管理）
        ↕ HTTP (127.0.0.1)
Python 引擎 (FastAPI)：数据 / 因子 / 回测 / 进化 / 调度
        ↕
DuckDB + Parquet（行情/因子/回测）  SQLite（账户/状态）

Tauri 2 (Rust)：纯壳 —— 窗口 + Python sidecar 托管
```

全部量化逻辑集中在 Python 引擎，可脱离 UI 独立运行；Rust 仅做窗口与 sidecar 生命周期管理。详细设计与实施路线见 [PLAN.md](PLAN.md)。

## 目录结构

```
jianwei/
├── engine/              # Python 量化引擎（uv 管理）
│   ├── jianwei/
│   │   ├── data/        # 数据源（东财/腾讯双源）、DuckDB 存储、增量同步
│   │   ├── factors/     # 价量因子库
│   │   ├── strategy/    # 多因子打分策略、SQLite 注册表
│   │   ├── backtest/    # A 股约束回测引擎
│   │   ├── report/      # 收益指标与报告
│   │   ├── api/         # FastAPI 本地服务（阶段二接入桌面端）
│   │   └── cli.py       # 命令行入口
│   └── tests/
├── apps/desktop/        # Tauri 2 + React 19 桌面应用
├── data/                # 本地数据（gitignore）：market.duckdb / app.sqlite
└── docs/
```

## 快速开始

### 环境要求

- Python 3.13（由 [uv](https://docs.astral.sh/uv/) 自动管理）
- Node.js 20+ 与 Rust 工具链（仅桌面端开发需要）

### 引擎（命令行）

```bash
cd engine
uv sync                                   # 安装依赖

uv run jianwei sync                       # 同步沪深300 行情（首次约 5-10 分钟）
uv run jianwei sync --universe all        # 或全市场（耗时较长）
uv run jianwei select --top 10            # 按最新数据选股
uv run jianwei backtest --start 2021-01-01 --top 10 --rebalance W
uv run jianwei quality                    # 数据质量报告
```

数据目录默认为 `<项目根>/data/`，可用环境变量 `JIANWEI_DATA_DIR` 重定向。

### 测试

```bash
cd engine
uv run pytest          # 16 项单测，覆盖全部 A 股交易约束
uv run ruff check jianwei tests
```

### 桌面端

```bash
cd apps/desktop
npm install
npm run tauri dev    # 自动拉起 Python 引擎 sidecar（随机端口 + token）
```

应用内含四个页面：**选股**（Top N 因子打分，可跳转 K 线）、**行情**（K 线 + MA + 成交量）、**回测**（净值曲线对比沪深300 + 指标卡片）、**数据**（同步触发与质量报告）。

## 开发状态

- [x] 阶段一 · 引擎骨架：数据同步、多因子选股、约束回测、CLI（已完成）
- [x] 阶段二 · 桌面应用：sidecar 托管、HTTP API、选股 / 行情 / 回测 / 数据页面（已完成）
- [ ] 阶段三 · 模拟盘与进化：模拟撮合、每日调度、Optuna 寻优
- [ ] 阶段四 · ML 与分发：Qlib 滚动训练、打包签名

## 免责声明

本软件仅供个人学习与研究使用，所有选股结果与回测数据不构成任何投资建议。股市有风险，入市需谨慎。若对外发布并提供荐股功能，依据中国法规需取得证券投资咨询业务资质。
