# BizAgent — 商务智能助手

基于 LLM（DeepSeek）的对话式商务智能 Agent，用自然语言查询和分析数据，自动生成报告与可视化图表。

## 产品定位

BizAgent 将自然语言交互引入数据分析场景，让非技术用户也能像和数据专家对话一样，直接提问获取洞察。

**目标用户**：业务负责人、运营分析、销售管理、财务人员 — 任何需要快速从数据中获得答案但不想写 SQL 或拖拽报表的人。

**解决的问题**：传统 BI 工具有使用门槛（需拖拽维度指标、配置仪表盘），而 BizAgent 只需打字提问。

## 核心能力

- **快速问答** — 查数据、比指标，一问一答，秒级响应
- **深度分析** — 自动完成意图识别→数据查询→统计分析→图表生成→结论输出全流程
- **数据源** — 内置 Kaggle Superstore Sales 数据集（美国零售超市 2014-2017 年，9994 条交易记录）
- **分析维度** — 产品品类（Furniture / Office Supplies / Technology）、区域（East / West / Central / South）、客户群（Consumer / Corporate / Home Office）、时间趋势、地理分布等
- **权限控制** — 管理端（可读写）/ 员工端（仅查询）
- **文件解析** — 上传 PDF / Word / Excel / CSV 等格式，自动提取内容进行分析

## 典型使用场景

| 场景 | 示例提问 | 推荐模式 |
|------|---------|---------|
| 快速查数 | "Technology 品类卖了多少" | 快速对话 |
| 对比分析 | "各区域的销售额和利润对比" | 深度分析 |
| 趋势洞察 | "月度销售趋势如何" | 快速对话 |
| 定位问题 | "哪些产品亏损最严重？根因是什么" | 深度分析 |
| 客户洞察 | "不同客户群的消费行为差异" | 深度分析 |
| 综合报告 | "全维度经营状况分析" | 深度分析 |

## 工作流程

```
用户提问 → 意图匹配 / 关键词识别 → 调用 Superstore 数据 API
  → 统计分析与指标计算 → 图表生成（折线图/柱状图/饼图）
  → LLM 组织自然语言报告 → 输出结果
```

两种模式的区别：
- **快速对话**：识别关键词后调用对应 API 获取数据，直接格式化返回，轻量快速
- **深度分析**：多维度调取数据 + 统计分析算子 + 图表可视化 + LLM 深度报告

## 效果示例

**问**："Technology 品类销售情况"

**答**：
> Technology 品类总销售额约 83.6 万美元，利润约 14.5 万美元，平均利润率 15.61%，在三大品类中销售额最高、利润也最高。
>
> 各子类表现如下：
> - Phones（手机）：销售额最高，约 33 万美元
> - Accessories（配件）：销售额 16.7 万，利润率 21.82%，盈利能力较强
> - Copiers（复印机）：利润率 31.72%，利润率最高
> - Machines（机器）：利润率为 -7.2%，处于亏损状态

## 快速开始

### 环境要求

- Python >= 3.12
- uv（推荐）或 pip

### 安装

```bash
git clone https://github.com/awww313/BizAgent.git
cd BizAgent
uv sync
```

### 配置

在项目根目录创建 `.env` 文件：

```bash
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

### 启动

```bash
python api_server.py
```

访问 http://localhost:5001 即可使用 Web 界面。

## 架构

```
api_server.py          FastAPI 服务（对话/会话/任务/文件端点）
  └── BizAgent         核心 Agent
        ├── chat_quick()              快速问答（函数调用+工具执行）
        ├── chat_with_analysis()      深度分析（意图→API→分析→图表→报告）
        ├── superstore_api.py         11 个数据查询函数与工具定义
        ├── superstore_analysis.py    统计分析算子
        ├── visualizer.py             图表生成（折线图/柱状图/饼图）
        ├── session_store.py          SQLite 会话持久化
        └── task_tracker.py           任务执行追踪
static/index.html      Web 前端界面
```

### 对话模式

| 模式 | 端到端延迟 | 适用场景 |
|------|-----------|---------|
| 快速对话 | ~3-6s | 日常数据查询、指标确认 |
| 深度分析 | ~5-12s | 复杂分析、报告生成、图表可视化 |

### 数据层

- **Superstore Sales**（Kaggle）：9,994 条交易记录，21 个字段
- **数据表**：`superstore_orders`（SQLite，自动从 Kaggle 下载并导入）
- 首次查询时自动完成数据加载，无需手动操作

## API 使用

### 统一对话接口

```bash
curl -X POST http://localhost:5001/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "各区域的销售额和利润对比",
    "mode": "analysis"
  }'
```

### 模式参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `mode` | `"quick"` | 快速问答 |
| `mode` | `"analysis"` | 深度分析+图表 |
| `role` | `"admin"` / `"employee"` | 管理端/员工端权限 |
| `generate_chart` | `true` / `false` | 是否生成图表（仅 analysis 模式） |
| `file_content` | base64 | 上传文件内容 |
| `file_name` | string | 上传文件名 |

### 文件上传

支持 `.txt .csv .json .md .pdf .docx .xlsx .png .jpg .jpeg`

### 会话管理

```bash
GET  /api/sessions                       # 活跃会话列表
GET  /api/sessions/{session_id}/messages # 会话消息历史
DELETE /api/sessions/{session_id}        # 删除会话
```

## 项目结构

```
BizAgent/
├── api_server.py                  FastAPI 服务（端点和路由）
├── src/minimal_agent/
│   ├── biz_agent.py               核心 Agent（对话编排）
│   ├── superstore_api.py          Superstore 数据 API（11 个查询函数）
│   ├── superstore_analysis.py     分析算子
│   ├── superstore_loader.py       数据集自动下载与导入
│   ├── enterprise_db.py           SQLite 数据库层
│   ├── visualizer.py              图表生成
│   ├── intent_engine.py           意图识别引擎
│   ├── analysis_ops.py            分析算子（旧版）
│   ├── prompts.py                 Prompt 模板
│   ├── context_manager.py         上下文管理
│   ├── session_store.py           SQLite 会话持久化
│   ├── task_tracker.py            任务执行追踪
│   ├── mock_enterprise_api.py     企业 API 函数定义
│   ├── response_builder.py        响应格式化
│   └── exceptions.py              异常定义
├── static/index.html              Web 前端
├── charts/                        自动生成的图表
└── data/                          持久化数据
```

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| LLM | DeepSeek Chat (deepseek-chat) |
| 数据库 | SQLite |
| 可视化 | matplotlib |
| 文件解析 | PyMuPDF (PDF), python-docx (Word), openpyxl (Excel) |
| 数据源 | Kaggle Superstore Sales Dataset |
| 运行环境 | Python 3.12+, uv 包管理 |

## License

MIT
