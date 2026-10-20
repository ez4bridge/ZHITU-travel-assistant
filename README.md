# 漫游 · Travel Assistant

面向中国城市旅行的浏览器规划原型，支持按顺序规划 **1–5 个目的地**。主体采用 Python + FastAPI + LangGraph，前端采用 Vue 3 + TypeScript + Vite，使用 DeepSeek 与高德查询地点和市内道路，可选接入社区 12306 MCP 查询直达列车。

## 当前功能

- 先填出发城市，再逐行添加目的地；支持城市搜索建议、删除和调整顺序。
- 每城默认游玩 1 天，可按半天调整；行程总天数自动合计并向上取整，当前支持 1–10 天、1–50 人。
- 可选安排出发地到首城的交通，以及最后返回出发地；支持铁路、飞机、长途汽车和自驾偏好。
- 每日活动、真实地点、时间轴与地图联动；跨城交通单独展示，不用直线冒充导航。
- 全员全程预算包含所选跨城交通，使用 Decimal 计算；检查时间冲突、通行时间、城市顺序、重复地点与预算。
- 每次修改保存新版本，旧记录保留在本机 SQLite；支持取消生成和超时退出。

铁路查询成功时分别展示“时刻已查询”和“参考票价”，并显示实际车站、车次、席别、余票快照和查询时间。未开售、无适配直达车或查询失败时保留估算及原因。公布参考价不等于购票成交价，余票不代表已占座；本项目不提供预订、锁价、机票查询或铁路换乘查询。

市内出行方式控件暂时隐藏，新建行程默认步行；后台仍支持步行／驾车，旧历史保留原方式。营业时间、预约要求和站外接驳尚未全部核实，“已知检查通过”不代表全部旅行事实已确认。

## 首次安装与启动（Windows）

准备 Git 和 Python 3.11 x64，在自己的 clone 中执行：

```powershell
git clone https://github.com/ez4bridge/travel-assistant.git
cd travel-assistant
.\setup.bat
```

私有仓库需要先接受队友邀请，并使用有权限的 GitHub 账号认证。安装脚本创建项目 `.venv`，安装锁定依赖，下载并校验项目独立 Node 22，构建前端，并为仓库自带的铁路 MCP 安装独立依赖；不改系统 Node 或全局仓颉环境。

在 `.env.local` 中填写模型与高德设置。安装脚本不会覆盖已有配置；手动创建时也先检查文件是否存在：

```powershell
if (-not (Test-Path -LiteralPath .env.local)) { Copy-Item .env.example .env.local }
.\start.bat
```

打开 [http://127.0.0.1:7862/](http://127.0.0.1:7862/)，保持启动窗口运行。修改配置后重新启动；关闭时 Ctrl+C 或执行 `stop.bat`。完整步骤见 [运行与开发教程](docs/GETTING_STARTED.md)。

新 clone 包含 third_party 中的铁路 MCP 源码、车站数据和许可证；不包含密钥、已安装的依赖环境或本机历史数据库。模型和高德配置见 [CONFIGURATION.md](docs/CONFIGURATION.md)；实际密钥仅放被 Git 忽略的 `.env.local`。

## 使用流程

1. 填写可选出发城市，按顺序添加目的地并选择各城天数。默认每城 1 天，例如 1.5 天 + 1 天会生成 3 个日历日；交通占用部分行程时间，未满足游玩时长时会提示。
2. 按需勾选出发交通和返程，选择日期、首日开始时间、同行人数、全员总预算与跨城交通偏好。
3. 点击生成。Agent 查询地点、路线和可用铁路选项，页面持续显示进度，再生成每日活动并校验时间与费用。
4. 查看地图、预算、提醒和未知项。每次点击修改执行一轮并保存新版本，可在“我的行程”查看历史。

表单不再单独填写总天数。旧单城市／显式总天数请求继续兼容；旧六城记录可查看和重新校验，重新生成前需调整为最多五城。

## 可选 12306 铁路查询

使用 [drfccv/mcp-server-12306](https://github.com/drfccv/mcp-server-12306) 社区实现，固定版本源码、车站数据、MIT 许可证及上游锁文件已收录在 `third_party/mcp-server-12306/`。拉取本项目即可取得源码，无需另行克隆上游仓库，也不需要 uv。

默认 `setup.bat` 会调用 `tools/setup_rail_mcp.py`，将哈希锁定的运行依赖安装至 `.tools/rail-mcp/.venv`；主应用依赖保持隔离。需要联网下载的是 Python 依赖包，源码已随仓库提供。当前安装器支持 Python 3.11–3.13；可用 `setup.ps1 -SkipRailMcp` 跳过可选依赖。

新配置模板默认设置 `RAIL_MCP_ENABLED=true`；现有 `.env.local` 不覆盖，原来关闭的用户可自行改为 true 并重启。`RAIL_MCP_PYTHON` 留空时使用项目约定的独立解释器。无需额外铁路 API Key 或 12306 账号。可设置 false 关闭，依赖未安装或查询失败时仍可生成估算交通。

仅重装铁路依赖可运行 `python tools/setup_rail_mcp.py`。安装、启停及单独测试命令见 [铁路接入说明](docs/RAIL_MCP.md)。查询限当前预售期，数据取决于上游可用性。一次完整规划还包含多轮模型和高德调用，铁路单次测速不能代表整份行程生成耗时。

## 仓颉校验与回退

源码在 `cangjie/src/validator.cj`，通过 JSON stdin/stdout 执行确定性时间、跨城顺序、游玩时长偏好和预算检查。`VALIDATOR_BACKEND=auto` 只有在原生程序与固定样例一致时才启用仓颉；缺失或技术故障时回退 Python。可设置 `VALIDATOR_BACKEND=python` 始终使用 Python。

当前 Linux 验证环境没有原生仓颉编译产物，实际使用 Python；2026-10-14 的 Windows 原生验收属于历史记录，不代表本轮规则已完成原生验证。Windows 可选编译步骤：

```powershell
.\.venv\Scripts\python.exe tools\download_cangjie.py
.\cangjie\build.ps1
.\.venv\Scripts\python.exe tools\check_cangjie.py
```

SDK 和编译产物不提交。详情见 [仓颉模块说明](cangjie/README.md)。

## 开发与验证

使用项目独立环境执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
$env:PATH = (Join-Path (Get-Location) '.tools\node') + ';' + $env:PATH
npm.cmd --prefix frontend run build
```

生产页面使用构建产物，更新 Vue 后需要重新 build。开发热更新、依赖安装和端口说明见 [开发教程](docs/GETTING_STARTED.md)。Linux 本地验证使用 `.venv/bin/python` 和项目独立 Node，不使用 Windows 脚本。

最近验证（2026-10-20）：94 项 pytest 通过、1 项原生仓颉测试跳过（含 MCP 源码与安装保护）；前端类型检查与生产构建通过。1440px／390px 页面检查通过表单顺序、返程、各城天数提交和历史重新规划，无脚本错误及横向溢出；该页面检查使用模拟生成并屏蔽底图请求。真实铁路查询及此前完整生成的条件和限制见 [当前状态](docs/PROJECT_STATUS.md)。

日常测试不调用付费模型。`tools/live_check.py` 会调用真实模型和高德，仅在需要且获授权时运行；重新校验已有行程不会刷新路线或铁路快照。

## 目录与协作

```text
backend/       API、Agent、城市建议、地图、铁路适配、存储与校验
frontend/      Vue 界面、目的地编辑器、交通卡片、地图与流式客户端
cangjie/       可选仓颉源码、构建脚本与协议样例
tests/         自动化功能、兼容性与故障测试
tools/         配置、停止服务、跨语言校验和铁路依赖安装／接口检查
third_party/   固定版本的第三方铁路源码、数据、上游锁文件与许可证
docs/          架构、接口、配置、验收与来源说明
.runtime/      本机历史、进程记录、诊断与验收产物（忽略）
.tools/        项目 Node 和可选铁路独立环境（忽略）
.venv/         主应用 Python 环境（忽略）
```

四人共用 main、各自 clone、小步同步。给新 Codex 的入口是 [AGENTS.md](AGENTS.md)，具体协作见 [COLLABORATION.md](docs/COLLABORATION.md)。不提交密钥、数据库、SDK、依赖或构建产物。

- [全部文档入口](docs/README.md)
- [当前功能与验证记录](docs/PROJECT_STATUS.md)
- [接口与金额单位](docs/API_CONTRACT.md)
- [架构与模块职责](docs/ARCHITECTURE.md)
- [配置说明](docs/CONFIGURATION.md)
- [历史验收记录](docs/ACCEPTANCE.md)
- [来源与许可证](docs/ATTRIBUTION.md)

项目保留独立实现与来源说明；原 `NVIDIA-TRAVEL` 不在本次修改范围内。
