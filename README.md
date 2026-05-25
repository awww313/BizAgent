# BizAgent — 商务智能助手

基于 LLM（DeepSeek）的商务智能 Agent 服务，支持自然语言查询企业数据、自动分析生成报告与可视化图表。

## 功能特性

- **多模式对话** — `quick` 快速问答 / `analysis` 深度分析+图表，按需切换
- **意图驱动引擎** — 自动识别用户意图（财务/销售/库存/员工/客户），提取参数、绑定 API、模板化输出
- **10+ 分析算子** — 增长率、占比、财务环比、销售对比、月度趋势、库存状态、员工统计、客户分层等
- **自动可视化** — 根据数据特征自动选择折线图、柱状图、饼图，一键生成 PNG
- **Reflection 质量评估** — 每次回答自动做置信度打分和幻觉检测，不确定时主动提示用户确认
- **角色权限控制** — `admin`（管理端，增删改查）/ `employee`（员工端，仅查询）
- **文件解析** — 支持 PDF、Word、Excel、CSV、JSON、Markdown 等格式上传解析
- **任务追踪与会话持久化** — SQLite 存储历史会话和消息，支持回溯
- **完整 Web 界面** — 单页应用，可拖拽侧边栏、图表面板、模式切换
- **Mock 企业数据** — 内置模拟库存/财务/销售/员工/客户数据，开箱即用

## 架构概览

BizAgent 遵循 **三层 Prompt + Function Calling + 意图引擎** 的设计：

1. 用户输入 → **意图引擎** 分类（财务/销售/库存等）并提取参数
2. 意图匹配 → 调用 **Mock 企业 API** 获取真实业务数据
3. 数据 → **分析算子** 自动计算统计指标
4. 结果 → **可视化器** 生成图表 + **Reflection** 管线做质量评估
5. 最终输出结构化响应（文字报告 + 图表）

## 快速开始

### 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/getting-started/installation/)（推荐）或 pip

### 安装

```bash
git clone https://github.com/<your-org>/BizAgent.git
cd BizAgent
uv sync
```

### 配置

在项目根目录创建 `.env` 文件：

```bash
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

如果需要联网搜索能力，可选配置：

```bash
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 启动服务

```bash
python api_server.py
```

服务启动后：
- Web 界面：http://localhost:5001
- API 文档（Swagger UI）：http://localhost:5001/docs
- 健康检查：http://localhost:5001/api/health

## API 使用

### 统一对话接口

```bash
curl -X POST http://localhost:5001/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "上个月各产品线利润情况如何？",
    "mode": "analysis",
    "role": "admin",
    "generate_chart": true
  }'
```

### 模式说明

| 模式 | 端点参数 | 说明 |
|------|---------|------|
| 快速问答 | `mode: "quick"` | 三层 Prompt 直接回答，不调用外部工具 |
| 智能对话 | `mode: "smart"` | 同 quick，兼容旧版 |
| 深度分析 | `mode: "analysis"` | 意图识别 → API 调用 → 统计分析 → 图表生成 |

### 文件上传

```bash
curl -X POST http://localhost:5001/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "分析这份销售数据",
    "mode": "analysis",
    "file_content": "<base64编码的文件内容>",
    "file_name": "sales_2025.xlsx"
  }'
```

支持的文件类型：`.txt .csv .json .md .pdf .docx .xlsx .png .jpg .jpeg`

### 会话管理

```bash
# 查看活跃会话
GET /api/sessions

# 获取会话消息历史
GET /api/sessions/{session_id}/messages

# 删除会话
DELETE /api/sessions/{session_id}
```

## 项目结构

```
BizAgent/
├── api_server.py                  # FastAPI 服务入口
├── src/minimal_agent/
│   ├── biz_agent.py               # 核心 Agent（三层 Prompt + Function Calling）
│   ├── intent_engine.py           # 意图识别引擎
│   ├── analysis_ops.py            # 分析算子（增长率/占比/趋势等）
│   ├── visualizer.py              # 图表生成（matplotlib）
│   ├── reflection.py              # Reflection 质量评估管线
│   ├── prompts.py                 # Prompt 模板
│   ├── context_manager.py         # 上下文管理/裁剪
│   ├── session_store.py           # SQLite 会话持久化
│   ├── task_tracker.py            # 任务执行追踪
│   ├── enterprise_db.py           # Mock 企业数据库
│   ├── mock_enterprise_api.py     # Mock 业务 API
│   ├── response_builder.py        # 响应格式化
│   └── exceptions.py              # 分级异常定义
├── static/index.html              # 前端 Web 界面
├── charts/                        # 自动生成的图表（gitignore）
├── data/                          # 持久化数据（gitignore）
├── scripts/                       # 演进教程脚本
└── benchmark/                     # 性能基准测试
```

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| LLM | DeepSeek Chat（通过 LiteLLM 统一接口） |
| 文件解析 | PyMuPDF（PDF）、python-docx（Word）、openpyxl（Excel） |
| 可视化 | matplotlib |
| Agent 引擎 | smolagents（Hugging Face） |
| 搜索 | DuckDuckGo / Tavily（可选） |
| 运行环境 | Python 3.12+，uv 包管理 |

## 配置参考

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | 必填 |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com/v1` |
| `BIZAGENT_PORT` | 服务端口 | `5001` |
| `TAVILY_API_KEY` | Tavily 搜索 API 密钥（可选） | - |

## License

MIT
