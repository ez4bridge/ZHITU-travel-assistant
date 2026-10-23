# 安装与启动

选择以下一种方式。两种方式均需要网络连接和有效的 API 配置。

## 一、便携快速启动版（推荐）

适用于 Windows 10／11 x64，无需另外安装 Python 或 Node。

1. **下载与解压。** 到[最新发布页](https://github.com/ez4bridge/ZHITU-travel-assistant/releases/latest)下载 **travel-assistant-windows-x64.zip**，也可[直接下载便携版](https://github.com/ez4bridge/ZHITU-travel-assistant/releases/latest/download/travel-assistant-windows-x64.zip)。完整解压后，进入能看到“启动漫游.exe”的程序目录，保留所有文件和子目录。
2. **填写配置。** 双击“配置API.bat”，填写根目录的 `.env.local` 并保存。公开下载包的密钥为空；已预填有效配置时可跳过。也可以把已有配置文件拖到“启动漫游.exe”上导入。
3. **启动使用。** 双击“启动漫游.exe”，等待本地服务启动并自动打开浏览器。请使用启动窗口显示的访问地址。

结束使用时双击“停止运行.bat”；修改配置后双击“重启运行.bat”。便携版包含研学资料、检索资源，无需另外恢复。

## 二、源码版（安装后启动）

GitHub 仓库的 **Code → Download ZIP** 和发布页中的 **Source code** 下载的是源码版，也可[直接下载当前源码](https://github.com/ez4bridge/ZHITU-travel-assistant/archive/refs/heads/main.zip)。源码版通过下方的安装脚本准备环境，再使用 start.bat 启动。

需要 Windows x64、Python 3.11 和网络连接。请预先安装 Python 3.11 x64，并在安装时勾选“Add python.exe to PATH”。安装脚本会准备 Node 和应用依赖，以下操作均在解压后的源码根目录执行。

1. **安装。** 双击 `setup.bat`，等待依赖与页面准备完成。行程校验由 Python 执行，无需编译原生程序。
2. **配置。** 打开根目录的 `.env.local`，填写下方 API 信息并保存。随包密钥均为空，安装程序不会覆盖已有配置。
3. **恢复研学资源。** 如需使用历史研学，先[下载研学资源包](https://github.com/ez4bridge/ZHITU-travel-assistant/releases/download/v1.0.1/travel-assistant-resources.zip)。解压后，将直接包含 MANIFEST.json、study 和 rag 的资源目录命名为 travel-assistant-resources，与源码文件夹并列放置。停止应用后在源码根目录打开 PowerShell，执行：

   ```powershell
   .\.venv\Scripts\python.exe tools\restore_resources.py ..\travel-assistant-resources
   .\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt
   ```

   资源恢复前会校验文件。已有资源默认不覆盖，需要替换时在第一条命令末尾加 `--replace`。替换仅清理对应资料包和旧索引，保留个人行程与会话。未安装本地模型依赖时，可使用关键词检索。
4. **启动。** 双击 `start.bat`，打开 [http://127.0.0.1:7862/](http://127.0.0.1:7862/)。使用期间保留启动窗口。配置其他 `PORT` 时，请访问相应地址。

结束使用时运行 `stop.bat`；修改配置后运行 `restart.bat`。

## 填写 API 配置

在根目录 `.env.local` 中填写，每行一项，格式为 `字段名=值`，使用 UTF-8 编码。更换服务商时，Key、接口地址与模型名称需相互匹配，修改后重启生效。

| 配置项 | 用途 |
| --- | --- |
| `LLM_API_KEY` | 模型服务 Key |
| `LLM_BASE_URL`、`LLM_MODEL` | OpenAI 兼容接口地址与模型名称 |
| `AMAP_API_KEY` | 高德 Web 服务 Key，查询地点、路线和天气 |
| `AMAP_SIGN_SECRET` | 高德 Web 服务签名私钥，按账号要求填写 |
| `AMAP_WEB_KEY`、`AMAP_JS_SEC` | 高德 Web 端 JS API Key 及对应安全密钥，显示地图 |
| `QWEATHER_API_KEY`、`QWEATHER_API_HOST` | 可选的和风天气配置；Host 不含协议或路径 |
| `GUIDE_SEARCH_API_KEY` | DeepSeek 搜索／网页读取 Key；使用官方 DeepSeek 模型接口时可留空并复用模型 Key |

普通旅行首先需要模型与高德配置。铁路查询及本地研学检索无需额外 Key。照片讲解与联网出行参考需要当前接入服务所支持的图片输入、搜索或网页读取能力，仅配置其他文本模型不保证这些功能可用。

和风天气的两项同时填写时优先使用和风，否则使用高德天气；显示日期以服务实际返回的预报范围为准。

## 使用与故障提示

- **页面无法打开：** 确认服务仍在运行，核对启动窗口中的地址与端口。
- **提示缺少配置：** 检查 `.env.local` 对应字段，保存并重启。
- **地点、天气或模型请求失败：** 检查服务额度、权限与网络。页面打开不代表外部服务已连通。
- **铁路环境未安装：** 源码版可执行 `.\.venv\Scripts\python.exe tools\setup_rail_mcp.py` 重试；无法查询时保留估算与待确认信息。
- **调整行程不可用：** 请检查模型与地图配置，重新启动服务后刷新页面，再进入 AI 调整。
- **研学资源未就绪：** 源码版按上述步骤恢复资源；便携版确认所有文件均已解压。

新建行程的日期和首日开始时间须晚于当前北京时间。车次、票价、开放时间和天气均可能变化，请在出发前核实；估算费用不代表订单报价，系统不代订酒店或车票。

## 本机数据与备份

配置保存在根目录 `.env.local`；使用记录及研学资源位于源码版的 `.runtime` 或便携版的 `app/.runtime`。备份或更换程序前请停止服务，保留配置及数据目录。分享程序时请使用密钥留空的配置，不要附带个人使用记录。

[返回功能介绍](../README.md)
