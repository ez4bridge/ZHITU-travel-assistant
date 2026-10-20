# 来源、许可证与新增工作

## 参考项目

旅行功能与交互需求参考 [blackwisten/NVIDIA-TRAVEL](https://github.com/blackwisten/NVIDIA-TRAVEL)，原项目使用木兰宽松许可证第 2 版。本目录保留其许可证副本 `LICENSE`。

高德签名的“按参数名排序原始值、拼接签名私钥、对 UTF-8 计算 MD5、之后 URL 编码”方式参考本机已修复的 `NVIDIA-TRAVEL/amap_service.py`，新实现文件内注明了来源；签名方式同时符合高德账号鉴权需要。原项目此前的环境与故障修复工作应与本次新项目开发区分记录。

本目录采用独立的 Python 与 Vue 实现。

## 本次新实现

- 独立的 Python／Node 环境、依赖锁定、Windows 安装和启动／停止流程。
- Vue 3 旅行界面、自定义布局、原生城市 SVG 插画、每日时间轴、真实地图联动。
- FastAPI + SSE 服务、每个请求独立的 LangGraph 工具循环、JSON 结构校验和取消机制。
- 城市内 POI 匹配、模型坐标丢弃、真实道路形状与通行时长查询。
- 按人数和计费范围的预算汇总、明确展示未知数据、用户一次修改后的重新核验。
- SQLite 版本记录和旧版本保留。
- 实际编译运行的仓颉规则校验器、固定／随机一致性测试以及 Python 故障回退。

这些内容是在 Codex 协助下编写和测试的。参赛或对外提交时应如实说明 AI 辅助使用情况，由团队理解并解释 Agent 流程、工具接口、费用计算、仓颉模块与回退逻辑；本文件不是参赛资格或得分保证。

## 主要依赖

Python、FastAPI、Uvicorn、HTTPX、Pydantic、LangGraph、OpenAI 兼容客户端、Vue、Vite、TypeScript、Lucide 及仓颉 SDK 分别遵循各自许可证。完整版本以 Python／npm 锁文件为准。

地图与地点由高德在线服务提供，模型由配置的 DeepSeek 接口提供；使用时遵循各自账号服务条件。本项目的视觉参考仅用于设计方向，没有复制先前展示的旅行网站页面或商业图片。


## 可选铁路服务

[drfccv/mcp-server-12306](https://github.com/drfccv/mcp-server-12306)，MIT，Copyright (c) 2026 drfccv。安装固定提交 f6865c077cfd51eb90021cc75aff33fad75ac3d5（0.5.0.post20260930），保留上游 LICENSE。固定快照源码、车站数据、上游依赖锁文件及完整 MIT LICENSE 收录于 third_party/mcp-server-12306；UPSTREAM.json 记录版本及逐文件哈希。仅排除上游 Git 忽略配置，运行源码保持原样。该目录按上游 MIT 条款分发，其余项目许可证不改变。项目通过独立 stdio 适配和 TLS／车站城市元数据启动包装器调用；运行依赖和虚拟环境在本机安装，不随源码提交。该项目为社区实现，不是官方发布的 MCP。铁路公布票价仅作参考，不提供预订。
