# 配置与额外依赖

## 已配置的内容

最初开发电脑已从原项目的本地配置迁移 DeepSeek 模型设置和高德四项设置。新成员 clone 后不含这些真实值，需自行填写 `.env.local`，详见 [首次运行教程](GETTING_STARTED.md)。这里只记录设置名称和用途，不记录真实值。旧项目的配置没有被修改。

| 功能 | 必要设置 | 用途 |
|---|---|---|
| AI 生成和修改行程 | `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` | 当前接入 DeepSeek，使用原先确认的 `deepseek-flash`，关闭思考模式 |
| 城市 POI、步行和驾车路线 | `AMAP_API_KEY` | 高德 Web 服务 Key，仅由 Python 使用 |
| Web 服务签名 | `AMAP_SIGN_SECRET` | 启用签名的高德账号需要，排序原始参数后计算 MD5，不发送给模型 |
| 浏览器底图和路线展示 | `AMAP_WEB_KEY` | 高德 Web 端 JS API Key，作为公开客户端标识使用 |
| JS API 鉴权 | `AMAP_JS_SEC` | Web 端安全密钥，仅在服务器代理中注入 `jscode` |
| 校验模块选择 | `VALIDATOR_BACKEND=auto` 或 `python` | 自动启用经一致性测试的仓颉模块，或只使用 Python |
| 可选铁路查询 | `RAIL_MCP_ENABLED=true`、`RAIL_MCP_PYTHON` | 新模板默认开启，现有配置保留；setup 自动安装自带源码的独立依赖，见 [铁路接入](RAIL_MCP.md)；不需要新 API Key |
| 本地端口 | `PORT=7862` | 默认为本机 7862，修改后按新地址访问；前端开发代理也需要相应调整 |

模型供应商采用 OpenAI 兼容的 Chat Completions 接口。换供应商时应验证其支持工具调用、JSON 输出和当前模型名；DeepSeek 的额外参数只对 DeepSeek 主机附加。首版已经验证的供应商是 DeepSeek，不把其他接口兼容性当作已测试。

## 启动和使用时的联网需求

打开首页不调用大模型。点击“生成”“修改”才产生模型请求，可能消耗供应商的付费额度。高德请求按你账户的许可与配额计算；本项目不自动申请新服务或购买额度。

地图需要在线加载高德 JS SDK 和地图瓦片。页面自身的城市插画用 SVG 绘制，字体优先使用 Windows 本地中文字体；没有图片、字体 API Key 的额外依赖。

Agent 直接调用注册在 Python 进程中的本地高德工具。

## 地图代理

前端只读取公开 Web Key 与 `/_AMapService` 代理地址。高德要求该前缀位于一级路由，不能使用嵌套的 `/api/.../_AMapService`。Python 转发时附加安全密钥，模型 Key、高德 Web 服务 Key 和签名私钥不会返回前端。

同源生产服务和 Vite 开发代理均支持 `/_AMapService`。模型与工具诊断统一脱敏；LangSmith／LangChain 外部追踪在本项目进程内关闭。

参考：[高德安全密钥代理文档](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)、[DeepSeek 工具调用](https://api-docs.deepseek.com/guides/tool_calls/)、[DeepSeek JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)。

## 常见问题

- **无法规划：**查看页面列出的缺失设置，在 `.env.local` 填写后重新启动。
- **地图空白或超时：**检查网络、高德 Web Key 平台类型及对应安全密钥；地点与路线文本仍然可以查看。
- **路线暂不可用：**可能是 POI 没有匹配、路线距离过大、服务鉴权／配额问题。不会用直线冒充导航路线。
- **校验仍有问题：**本版按用户点击进行一次修改，剩余问题继续展示；不会无限调用模型。
- **仓颉不可用：**直接使用 Python，不影响开始规划。技术原因可从健康接口与校验结果诊断字段查看。
- **端口占用：**先使用本项目 `stop.bat`，或者配置另一端口；停止脚本核验进程及其父进程，只停止本项目记录的服务。
