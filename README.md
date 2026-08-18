# PaperMind · CV 异常检测论文 RAG 问答系统

[![CI](https://github.com/zanezhao0708/PaperMindRAG/actions/workflows/ci.yml/badge.svg)](https://github.com/zanezhao0708/PaperMindRAG/actions/workflows/ci.yml)
[![Daily Digest](https://github.com/zanezhao0708/PaperMindRAG/actions/workflows/daily.yml/badge.svg)](https://github.com/zanezhao0708/PaperMindRAG/actions/workflows/daily.yml)
[![Latest](https://img.shields.io/badge/日报-每日自动更新-blue)](./digest/README.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

> 📚 **[论文浏览页（在线）](https://zanezhao0708.github.io/PaperMindRAG/)** 主题筛选 · 搜索 · 趋势　|　📡 **[RSS 订阅](https://raw.githubusercontent.com/zanezhao0708/PaperMindRAG/main/digest/feed.xml)** 复制到任意阅读器　|　📖 [每日日报](./digest/README.md)　|　📈 [每周周报](./digest/weekly/)

## 🔔 每日论文日报（自动更新，每天可用）

**每天北京时间 09:00**，GitHub Actions 自动完成：抓取 arXiv 最新 CV 异常检测论文 → DeepSeek 中文解读（标题翻译/一句话总结/方法亮点/★推荐评级/主题标签/今日概览）→ 自动生成日报提交到仓库。无需任何人工操作，Star 后每天来仓库看 [digest/](./digest/README.md) 就能跟踪领域最新进展。

**这一步同时也是 RAG 知识库的自动供给**——新论文持续入库，问答系统随之"越用越懂"。

### 三种订阅方式

| 方式 | 入口 | 说明 |
|---|---|---|
| 在线浏览页 | [zanezhao0708.github.io/PaperMindRAG](https://zanezhao0708.github.io/PaperMindRAG/) | 主题标签筛选 / 评分过滤 / 关键词搜索 / 近 4 周趋势卡片，GitHub Pages 自动部署 |
| RSS 订阅 | [digest/feed.xml](https://raw.githubusercontent.com/zanezhao0708/PaperMindRAG/main/digest/feed.xml) | 任意 RSS 阅读器添加 raw 链接；浏览页已埋自动发现标签 |
| 邮件推送 | 配置下方 SMTP Secrets | 日报生成后自动发送到指定邮箱，未配置则自动跳过 |

**每周一 09:30** 自动生成周报（近 7 天聚合 + 与上期环比趋势对比 + 本周必读），归档在 [digest/weekly/](./digest/weekly/)，见 [weekly.yml](.github/workflows/weekly.yml)。

RSS raw 链接：`https://raw.githubusercontent.com/zanezhao0708/PaperMindRAG/main/digest/feed.xml`

---

面向**计算机视觉异常检测（Industrial Anomaly Detection）**论文库的检索增强生成（RAG）问答系统：上传论文 PDF，用中文提问，系统检索最相关的文献片段并让 LLM 生成**带 [1][2] 引用标注**的回答。

## 架构

```
                     ┌──────────── 摄入链路 ────────────┐
 PDF/TXT/MD ──> Loader ──> Chunker ──> Embedder ──> VectorStore(持久化)
              (pypdf抽取)  (递归分割    (三级降级)     (NumPy余弦)
                           800/120)

                     ┌──────────── 问答链路 ────────────┐
 中文问题 ──> Embedder ──> Retriever ──> Generator ──> 带引用的回答
              (查询嵌入)   (top-k+阈值过滤)  (DeepSeek LLM)
```

### 嵌入三级降级（系统在无网/无 Key 环境也能跑）
1. **API 嵌入**：OpenAI 兼容 `/embeddings`（需配置 `PM_EMBED_API_KEY`）
2. **本地语义嵌入**：fastembed + ONNX Runtime，默认多语言模型
   `paraphrase-multilingual-MiniLM-L12-v2`，支持**中文问句 ↔ 英文论文**跨语言检索
3. **哈希兜底**：词/字符 n-gram 哈希投影，纯离线（仅词面匹配，无语义）

## 快速开始

```bash
pip install -r requirements.txt

# 1) 下载 CV 异常检测经典论文（PatchCore/PaDiM/SPADE/CutPaste/DRAEM 等 8 篇）
python scripts/download_papers.py

# 2) 启动（自动加载 data/docs 并摄入）
python app.py            # 打开 http://127.0.0.1:5000
```

启动后会进入图形化论文工作台，无需额外安装前端工具链。界面支持：

- PDF / TXT / MD 多文件选择、拖拽导入与导入队列
- 知识库文档/文本块统计，以及检索、嵌入、生成模型运行状态
- 快捷问题、问答历史、耗时信息和可定位的原文引用
- 亮/暗主题切换、API 配置弹窗、桌面三栏布局与移动端自适应布局

CLI（`pip install -e .` 后可用）：

```bash
papermind ingest data/docs/      # 摄入文档（增量：同名替换旧块）
papermind query "PatchCore 的核心思想"   # 命令行问答
papermind serve --port 5000      # 启动 Web 服务
```

测试与检查：

```bash
make dev        # 安装开发依赖
make test       # 42 个用例，全离线 2s 跑完（不下载模型/不联网）
make lint       # ruff 代码检查
```

Docker 部署：

```bash
make docker-build && make docker-run   # gunicorn 生产级 WSGI
```

## 工程化实践

- **测试**：`tests/` 54 个用例覆盖分块/向量库/嵌入降级/BM25/混合检索/端到端/HTTP 契约，**全离线可跑**（stub 掉外部依赖，本机与 CI 行为一致），CI 4 分钟内完成
- **CI**：[ci.yml](.github/workflows/ci.yml) push/PR 自动 lint + pytest（Python 3.10-3.12 矩阵），[daily.yml](.github/workflows/daily.yml) 每日日报
- **增量摄入**：按 source 替换旧块（`VectorStore.remove_source`），重复上传不膨胀
- **应用工厂**：`create_app()` + 懒加载 Pipeline + `/api/health` 存活探针 + 统一 JSON 错误处理（400/404/413/500）
- **配置 fail-fast**：`Config.__post_init__` 启动即校验参数合法性
- **日志**：库内 `logging.getLogger`，入口统一 `setup_logging()`，级别可用 `PM_LOG_LEVEL` 调
- **打包**：pyproject.toml + console_scripts 入口，可 `pip install` 分发
- **依赖分层**：核心（flask/numpy/pypdf）→ embed（fastembed/onnxruntime，可选）→ dev（pytest/ruff）

## 配置（.env，已 gitignore）

启动 Web 界面后可点击右上角 **⚙** 配置生成 API 与嵌入 API。设置会写入
本机写入项目根目录的 `.env`，Render 部署写入持久磁盘中的 `.env`，并立即生效。
密钥不会由读取接口回传；公网部署仅 `PM_ADMIN_EMAIL` 指定的账户可修改配置。
也可以继续手动编辑：

```ini
PM_API_KEY=sk-xxx                # DeepSeek（生成模型）
PM_BASE_URL=https://api.deepseek.com/v1
PM_CHAT_MODEL=deepseek-chat
PM_LOCAL_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
HF_ENDPOINT=https://hf-mirror.com   # 模型权重国内镜像
HF_HUB_DISABLE_XET=1               # 镜像站不支持 xet 协议时禁用
PM_LOG_LEVEL=INFO                  # 日志级别

# Web 登录（默认开启；邮箱账户保存在 data/users.db）
PM_AUTH_REQUIRED=1
PM_SECRET_KEY=请替换为至少32字节的随机值
PM_COOKIE_SECURE=0                 # HTTPS 部署时改为 1
PM_ADMIN_EMAIL=owner@example.com  # 公网部署的 API 配置管理员

# GitHub OAuth
PM_GITHUB_CLIENT_ID=
PM_GITHUB_CLIENT_SECRET=

# Google OAuth
PM_GOOGLE_CLIENT_ID=
PM_GOOGLE_CLIENT_SECRET=

# Microsoft OAuth（common 同时支持个人与组织账户）
PM_MICROSOFT_CLIENT_ID=
PM_MICROSOFT_CLIENT_SECRET=
PM_MICROSOFT_TENANT=common

# 邮件推送（可选；配到 GitHub Secrets 后每日日报自动发邮箱）
PM_SMTP_HOST=smtp.qq.com           # SMTP 服务器
PM_SMTP_PORT=465                   # SSL 端口，默认 465
PM_SMTP_USER=you@qq.com            # 发件账号
PM_SMTP_PASS=授权码                 # QQ 邮箱用授权码，非登录密码
PM_MAIL_TO=you@qq.com,peer@x.com   # 收件人，逗号分隔
```

OAuth 应用需要登记与当前访问域名完全一致的回调地址。本机默认端口示例：

```text
http://localhost:5000/auth/oauth/github/callback
http://localhost:5000/auth/oauth/google/callback
http://localhost:5000/auth/oauth/microsoft/callback
```

未填写某个平台的 Client ID/Secret 时，对应快捷登录按钮仍会展示，但保持禁用。
生产环境应使用 HTTPS、设置 `PM_COOKIE_SECURE=1`，并显式配置稳定的
`PM_SECRET_KEY`。未配置时，PaperMind 会在已忽略版本控制的 `data/.session_secret`
中生成本机开发密钥。

## Windows 桌面版

桌面入口使用系统 WebView2（Chromium）显示现有 Flask 页面。双击程序会在本机
`127.0.0.1:5000` 启动服务，并通过 `localhost:5000` 加载界面；关闭窗口时服务同步退出。第三方登录会
在系统默认浏览器中完成，再自动回到桌面窗口，避免 OAuth 平台拦截内嵌浏览器。登录账户、论文、索引和网页内保存的 API
配置位于 `%LOCALAPPDATA%\PaperMind`，升级 EXE 不会覆盖这些数据。

如端口 5000 已被占用，可在启动前通过 `PM_DESKTOP_PORT` 改为其他固定端口，并同步
修改 OAuth 平台登记的回调地址。

桌面版右上角的账户菜单可以切换或添加本机用户。切换账户会回到登录页并要求重新
验证密码；每个用户的 API Key、模型配置和亮暗主题偏好分别保存在本机
`data\users.db` 中，密钥不会通过接口回传。旧版 `settings.env` 中已有的 API 配置会
在升级后首次使用时迁移给第一个本机用户，后续用户不会继承其密钥。论文知识库仍是
这台设备上的共享资源。

```powershell
python -m pip install -r requirements-desktop.txt
powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1
```

构建结果为 `dist\PaperMind.exe`。目标电脑需要 Windows 10/11 和 Microsoft Edge
WebView2 Runtime（Windows 11 通常已预装）。

## Render 部署

仓库根目录的 `render.yaml` 会创建新加坡区域的 Docker Web Service，并把
`/app/data` 挂载为 1 GB 持久磁盘。邮箱用户、上传文档、向量索引、模型缓存和网页内
保存的 API 配置都保存在该目录。服务使用单进程 Gunicorn，避免 SQLite 和本地索引
被多个进程并发写入。

Render 创建 Blueprint 时需要填写 `PM_ADMIN_EMAIL`。OAuth 的 Client ID/Secret
应在对应平台创建应用后填写，回调地址格式如下（按最终服务域名替换）：

```text
https://papermindrag-lauk1z.onrender.com/auth/oauth/github/callback
https://papermindrag-lauk1z.onrender.com/auth/oauth/google/callback
https://papermindrag-lauk1z.onrender.com/auth/oauth/microsoft/callback
```

持久磁盘只能附加到 Render 付费 Web Service；免费实例的 SQLite、上传文件和索引会
在休眠、重启或重新部署时丢失，因此本 Blueprint 明确使用 `starter` 套餐。

## 目录结构

```
papermind/
├── papermind/          # 核心包（可 pip install）
│   ├── config.py       # 集中配置 + .env 加载 + 参数校验
│   ├── auth.py         # 邮箱账户、会话保护与 OAuth 登录
│   ├── loader.py       # PDF/TXT/MD 加载
│   ├── chunker.py      # 递归字符分块（可重叠）
│   ├── embeddings.py   # 三级降级嵌入
│   ├── vectorstore.py  # NumPy 向量库（余弦检索+持久化+按源删除）
│   ├── retriever.py    # top-k 召回 + 阈值过滤
│   ├── generator.py    # LLM 生成（引用约束/抽取式兜底）
│   ├── pipeline.py     # RAG 编排（增量摄入）
│   ├── server.py       # Flask 应用工厂
│   └── cli.py          # papermind 命令行
├── tests/              # pytest 测试套件（离线）
├── scripts/            # 论文下载 / 每日日报 / 周报 / 站点 / 邮件
├── templates/          # 工作台与登录页面
├── app.py              # 兼容旧启动方式
├── Dockerfile / render.yaml / Makefile / pyproject.toml
└── .github/workflows/  # CI（lint+test）+ 每日日报 + 每周周报
```

## 混合检索（BM25 + 稠密向量 RRF 融合）

稠密向量擅长语义泛化（中文问句→英文论文），但专业术语词面匹配是弱项；BM25 相反。两路排名用 RRF 融合（`rrf(d)=Σ 1/(k+rank_i(d))`，按排名融合规避量纲不可比）。中文分词用 CJK 字符 bigram，零分词库依赖。

真实论文库实测（8 篇经典论文 / 631 块 / 16 个中文问题）：

| 模式 | R@1 | R@3 | R@5 | MRR |
|---|---|---|---|---|
| dense | 0.750 | 0.938 | 1.000 | 0.846 |
| bm25 | **0.875** | 0.875 | 0.875 | 0.875 |
| **hybrid** | 0.812 | **1.000** | **1.000** | **0.906** |

复现：`python scripts/eval_retrieval.py`（自动跳过未下载的论文）。

## 设计取舍

| 决策 | 理由 |
|---|---|
| NumPy 暴力余弦而非 FAISS | 论文库量级小（千级块），暴力扫描 <10ms；全流程透明可解释 |
| BM25+稠密 RRF 混合检索 | 语义泛化与术语精确匹配互补，实测 MRR +0.06、R@3 100% |
| 检索阈值过滤（0.30，dense路） | 宁缺毋滥，低相关片段会诱导 LLM 幻觉 |
| 分块 800 字符 + 120 重叠 | 论文段落级语义完整；重叠避免关键句被截断 |
| 提示词强制引用编号 | 回答可溯源到具体 chunk，可核验、可评估 |
| 多语言 MiniLM 本地嵌入 | 中文提问英文论文；ONNX 推理免 torch，部署轻 |
| 测试全离线 stub 外部依赖 | CI 不下模型不联网，2s 跑完，降级路径本身也被测试覆盖 |

## 局限与改进方向

- 图表内容无法解析（PDF 只抽文本），可引入多模态文档解析
- 嵌入无缓存，可按 chunk 哈希做增量嵌入复用
- 评测集可扩展为人工标注的多文档混合问题

## License

[MIT](./LICENSE) — 欢迎 Fork、二次开发与 PR。如果项目对你有帮助，欢迎 Star 支持。
