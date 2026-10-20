# 新成员运行与开发教程（Windows）

以下命令默认在**本项目根目录**的 PowerShell 中执行；不要在旁边的原 NVIDIA-TRAVEL 目录执行。每人 clone 一份代码，用各自的 `.env.local` 和本地数据库。

## 1. 获取项目、准备环境

安装 Git 和 **Python 3.11 x64**。本项目锁文件以 Windows / Python 3.11 为基准；不要默认所有其他 Python 版本都已验证。Node 和仓颉可以使用项目局部环境，不要求修改系统版本。

```powershell
git clone <队长提供的仓库地址>
cd <克隆得到的项目目录>
python --version
.\setup.bat
```

把尖括号内容换成真实值，不要原样执行。`setup.bat` 调用根目录 `setup.ps1`，创建 `.venv`，安装 Python 锁定依赖，下载并校验项目专用 Node 22.23.3，执行前端 `npm ci` 与构建，最后在配置不存在时创建空白 `.env.local`。首次安装需要联网，不会带来队长机器上的密钥、数据库或历史。

若 `python` 指向别的版本，可明确指定已安装的 Python 3.11：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Python "C:\你的Python311目录\python.exe" -Dev
```

此参数只对本次脚本生效。路径换成自己的解释器路径；安装脚本不会替你安装 Python。`-Dev` 明确补装测试依赖；当前完整锁文件本身也包含已验证的测试依赖。

## 2. 填写本地配置

用编辑器打开 `.env.local`。如果文件不存在，执行下面命令创建；**已有配置不要覆盖**：

```powershell
if (-not (Test-Path -LiteralPath .env.local)) { Copy-Item .env.example .env.local }
```

| 设置 | 怎么填 | 需要体验什么 |
|---|---|---|
| `LLM_API_KEY` | 自己的模型 API Key，或队内获准使用的 Key | 生成/修改 |
| `LLM_BASE_URL` | 当前示例为 `https://api.deepseek.com` | 模型服务地址 |
| `LLM_MODEL` | 当前已验证 `deepseek-flash`；以所用账号实际可用模型为准 | 模型名，不是 Key ID |
| `AMAP_API_KEY` | 高德“Web 服务”平台 Key | 地点/路线，也用于完整规划 |
| `AMAP_SIGN_SECRET` | 上述 Web 服务 Key 对应的签名私钥；启用签名时填写 | Web 服务鉴权 |
| `AMAP_WEB_KEY` | 高德“Web 端（JS API）”平台 Key | 浏览器底图 |
| `AMAP_JS_SEC` | 上述 Web 端 Key 对应的安全密钥 | 浏览器地图代理鉴权 |
| `VALIDATOR_BACKEND` | 首次可填 `python`；启用可选仓颉后用 `auto` | 规则检查，不需要模型 Key |
| `PORT` | 默认 `7862` | 后端与生产页面端口 |
| `RAIL_MCP_ENABLED` / `RAIL_MCP_PYTHON` | 新模板开启，现有配置保留；解释器路径可留空 | 可选铁路查询，见 [RAIL_MCP.md](RAIL_MCP.md) |

两个高德 Key 的平台和密钥类型不要混用。高德控制台若启用了额外限制，须与本机使用方式匹配。完整配置解释见 [CONFIGURATION.md](CONFIGURATION.md)。不要把真实值贴进共享文档、Git 或截图。

没有 Key 也可以打开已构建首页、运行不依赖外部 API 的测试和开发界面；不能完成真实规划/底图。换模型供应商要支持工具调用和 JSON 输出，其他供应商尚未验收。环境变量优先于 dotenv 中同名设置，修改 `.env.local` 后若仍不生效，检查当前终端是否设置了同名变量；不要打印密钥排查。

## 3. 正常启动、关闭与首次检查

```powershell
.\start.bat
```

保持启动窗口运行，访问 [http://127.0.0.1:7862/](http://127.0.0.1:7862/)。修改 PORT 后按新端口访问。关闭可在启动窗口 Ctrl+C，或者另开终端执行 `.\stop.bat`。重复启动会识别本项目，不必手工杀其他 Python 进程。

在另一终端检查：

```powershell
Invoke-RestMethod http://127.0.0.1:7862/api/health
```

`missing` 只表示必要配置是否为空，`capabilities` 只表示配置/校验器就绪情况；它们**没有探测 API 余额、Key 是否有效或配额**。确认全部填好后，手动生成一次单日方案，检查地点、道路路线、预算和未知提示。生成/修改会调用真实模型与高德，可能消耗额度。

每个人的历史在自己的 `.runtime/plans.sqlite3`。空白历史是正常的，不必向队长索取数据库。

## 4. 前后端开发模式

终端 A：运行后端（代码修改后 Ctrl+C，再启动；默认无自动重载）。

```powershell
.\.venv\Scripts\python.exe -m backend.main
```

终端 B：从根目录执行，临时把项目 Node 放在 PATH 首位：

```powershell
$env:PATH = (Join-Path (Get-Location) '.tools\node') + ';' + $env:PATH
$env:npm_config_cache = Join-Path (Get-Location) '.tools\npm-cache'
Set-Location frontend
npm.cmd run dev
```

打开 Vite 输出的地址（通常 `http://127.0.0.1:5173`），不要继续看 7862 的旧构建页面。修改 Vue/CSS 会热更新。`frontend/vite.config.ts` 代理 `/api` 和 `/_AMapService` 到 7862；如改后端端口，开发代理也要一致。

给其他人演示前，在终端 B 的 `frontend` 目录执行：

```powershell
npm.cmd run build
```

再用 `start.bat` 启动/重启生产页面，刷新 7862。只改源码却不构建时，7862 不会自动展示新界面。更新仓库后，Python 依赖变化需要重新安装，前端锁文件变化需要 `npm ci`，前端源码变化需要重新 build；最省事可在根目录重跑 `setup.bat`，不会覆盖已存在的 `.env.local`。

## 5. 测试命令

根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

测试使用模拟模型/地图响应，不会调用付费模型。部分真实仓颉测试在可执行文件缺失时跳过，留意输出，不要将跳过当作仓颉验证通过。前端构建命令见上节。

真实接口脚本仅在需要时运行（默认地址固定为 7862；改变端口需同步脚本）：

```powershell
.\.venv\Scripts\python.exe tools\live_check.py --mode walking --days 1
```

加 `--revise` 会再做一次模型修改，额外消耗请求；无需每次提交都跑。输出保存在忽略的 `.runtime` 下。

## 6. 可选仓颉

先完成 Python 版启动，再按需运行：

```powershell
.\.venv\Scripts\python.exe tools\download_cangjie.py
.\cangjie\build.ps1
.\.venv\Scripts\python.exe tools\check_cangjie.py
```

下载器获取并校验官方 Windows x64 SDK（约 284 MB），放在忽略的 `cangjie/.sdk`；编译产物在 `cangjie/bin`。PowerShell 如拦截脚本，可用 `powershell -NoProfile -ExecutionPolicy Bypass -File .\cangjie\build.ps1`，不必修改全局策略。

将 `VALIDATOR_BACKEND=auto` 后重启，查看健康接口的 `capabilities.validator` 与 `validator_diagnostic`。一致性通过应显示 `cangjie`，否则自动用 Python。下载的是编译 SDK，**不是 AI 模型权重**。

## 可选铁路查询

12306 MCP 的固定版本源码、车站数据和许可证已经在 third_party，拉取本仓库即可取得。setup.bat 默认调用 tools/setup_rail_mcp.py 在独立虚拟环境安装哈希锁定依赖，无需另行克隆或安装 uv。新模板开启 RAIL_MCP_ENABLED，旧配置不覆盖；无需新的铁路 Key 或账号。依赖缺失时保留交通估算，主应用依赖不变。详见 [RAIL_MCP.md](RAIL_MCP.md)。

表单先填写出发城市，再添加目的地。每城默认 1 天、可按半天调整，合计向上取整且最多 10 天，无需另填总天数；可勾选返程。市内方式控件暂时隐藏，新建默认步行。

## 7. 常见问题

| 现象 | 先查什么 |
|---|---|
| 首页 503 / 提示先构建 | `frontend/dist/index.html` 是否存在；重新运行 setup/build |
| 前端改了没生效 | 是否打开旧的 7862 页面；开发用 Vite 地址，演示先 build |
| 规划报错/超时 | `.env.local`、模型名/兼容能力、余额/配额、网络；不要无限重试 |
| 地图空白但有行程 | Web Key/JS 安全密钥的对应关系、网络、根级代理；文字路线仍可看 |
| health 显示 python | 可选仓颉缺失、设置为 python 或一致性失败；按诊断排查 |
| npm 仍是 Node 16 | 在当前终端把项目 `.tools/node` 加入 PATH；无需卸载系统 Node |
| 7862 被占用 | 用本项目 stop.bat；确认占用者后处理，或换 PORT 与开发代理 |
| 拉取后旧历史加载失败 | 保留数据库，检查是否有人破坏了模型兼容性；不要直接删除历史 |

提交前确认 `git status --short` 没有本地配置、数据库、SDK 或依赖目录。协作方式见 [COLLABORATION.md](COLLABORATION.md)。
