# 见微 Jianwei

> 见微知著 —— 从细微的市场信号中发现选股机会。

聚焦中国 A 股的自动选股软件：内置可持续进化的多因子选股算法，提供严谨的收益评估与模拟演练环境。跨平台桌面应用，优先 macOS。

## 功能特性

- **多因子选股**：动量 / 反转 / 低波动 / 流动性等价量因子，截面 z-score 加权打分，取 Top N；因子权重与参数通过 Optuna 持续寻优进化（champion/challenger 晋升机制）
- **A 股约束回测**：自研日频调仓模拟器，T+1、涨跌停（主板 10% / 创业板科创板 20% / 北交所 30% / ST 5%）、佣金（最低 5 元）、卖出印花税、滑点、停牌、整手交易全部内置——回测数字不骗人
- **收益评估**：年化收益、最大回撤、夏普 / 卡玛比率、胜率、换手率，对比沪深 300 基准
- **模拟盘**：SimBroker 每日自动调仓，账户净值 / 持仓 / 流水实时可查；APScheduler 每日 15:35 自动触发同步 + 选股 + 调仓
- **Baostock 数据源**：真实成交额与换手率，专为 A 股量化设计，无 WAF；腾讯财经兜底，东方财富可选
- **本地数据**：行情增量同步入 DuckDB，数据无需每次重下，离线可用
- **策略版本化**：策略参数与每次回测结果落 SQLite 注册表，可追溯

## 架构

```
React 19 + Vite 8（选股 / K线 / 回测 / 模拟盘 / 策略进化 / 数据）
        ↕ HTTP (127.0.0.1 随机端口 + token)
Python 引擎 (FastAPI)：数据 / 因子 / 回测 / 模拟盘 / 进化 / 调度
        ↕
DuckDB（行情日线）  SQLite（模拟盘账户 / 策略注册表 / 调度日志）

Tauri 2 (Rust)：纯壳 —— 窗口 + Python sidecar 托管
```

全部量化逻辑集中在 Python 引擎，可脱离 UI 独立运行；Rust 仅做窗口与 sidecar 生命周期管理。详细设计与实施路线见 [PLAN.md](PLAN.md)。

## 快速开始

### 环境要求

- Python 3.13（由 [uv](https://docs.astral.sh/uv/) 自动管理）
- Node.js 20+ 与 Rust 工具链（仅桌面端开发需要）

### 引擎（命令行）

```bash
cd engine
uv sync                        # 安装依赖

uv run jianwei sync            # 同步沪深 300 行情（首次约 5 分钟）
uv run jianwei select --top 10 # 按最新数据选股
uv run jianwei backtest --start 2021-01-01 --top 10 --rebalance W
uv run jianwei quality         # 数据质量报告
```

数据目录默认 `<项目根>/data/`，可用 `JIANWEI_DATA_DIR` 重定向。  
数据源默认 Baostock，可用 `JIANWEI_DATA_SOURCE=tx` 切换腾讯财经。

### 测试

```bash
cd engine
uv run pytest          # 20 项单测，覆盖全部 A 股交易约束
uv run ruff check jianwei tests
```

### 桌面端

```bash
cd apps/desktop
npm install
npm run tauri dev    # 自动拉起 Python 引擎 sidecar（随机端口 + token）
```

6 个页面：**选股**（Top N 因子打分）、**行情**（K 线 + MA + 成交量）、**回测**（净值指标卡片）、**模拟盘**（账户净值 / 持仓 / 流水）、**策略进化**（Optuna 参数寻优）、**数据**（同步触发与质量报告）。

## 回测样本（v0.2.0，Baostock 数据）

> 沪深 300 全成分，2019-01-02 ~ 2026-06-18，周频调仓，Top 10，初始资金 100 万

| 指标 | 数值 |
|---|---|
| 累计收益 | **785%** |
| 年化收益 | **35.5%** |
| 沪深300基准年化 | 7.4% |
| 超额年化收益 | **28.2%** |
| 最大回撤 | -52.4% |
| Sharpe | 0.98 |
| Calmar | 0.68 |
| 胜率 | 44.4% |
| 总手续费 | 33 万元（已计入） |

> ⚠️ 回测仅供参考，不含幸存者偏差修正，实盘有冲击成本。

## 开发状态

- [x] 阶段一 · 引擎骨架：数据同步、多因子选股、约束回测、CLI
- [x] 阶段二 · 桌面应用：sidecar 托管、HTTP API、6 页面
- [x] 阶段三 · 模拟盘与进化：SimBroker、APScheduler 每日调度、Optuna 参数进化、Baostock 主数据源
- [ ] 阶段四 · 提升与分发：市场趋势过滤、UI 图表、macOS 打包签名

## 免责声明

本软件仅供个人学习与研究使用，所有选股结果与回测数据不构成任何投资建议。股市有风险，入市需谨慎。若对外发布并提供荐股功能，依据中国法规需取得证券投资咨询业务资质。
