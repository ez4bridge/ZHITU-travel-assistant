# 工作记录模板

用于记录小队实际讨论后确定的工作或交接情况，按需填写。

```text
任务名称：
负责人：
状态：进行中 / 完成 / 待处理
涉及文件与公共字段：
完成条件：
实际验证：
交接说明：
提交号（如已提交）：
```


## 全国规划与铁路接入交接（2026-10-20）

- 任务：全国多城市与逐城游玩天数、可选 12306 查询、表单顺序调整、文档同步。
- 公共变化：TripRequest.destinations、自动 days、出发／返程选项；Stop.city、Itinerary.transfers、RouteLeg.scope；铁路查询状态／参考价元数据；GET /api/cities 与 capabilities.railway。新字段默认值及旧记录兼容见 API_CONTRACT.md。
- 配置：新增可选 RAIL_MCP_ENABLED 与 RAIL_MCP_PYTHON，独立安装见 RAIL_MCP.md，不需要额外铁路 Key。真实密钥及本机历史不交接到仓库。
- 验证：88 项 pytest 通过、1 项原生跳过；前端 build、pip check 通过。桌面／窄屏和真实接口记录见 PROJECT_STATUS.md，未重新调用付费模型。
- 限制：交通未预订，公布票价不等于成交价；未核实接驳／营业时间；铁路仅直达；本轮使用 Python，仓颉原生编译／一致性需有 SDK 的机器完成。
- 同步：功能提交 ce4ed2a；用户已为服务器添加公钥，SSH 认证通过，远端 main 无新增提交。按用户授权正常推送到 main，不强推；版本以远端提交历史为准。


## 12306 源码随项目提供（2026-10-20）

- 状态：按本次明确授权上传 MCP 源码和安装流程；此前仓颉改动继续保留本地，不纳入本次提交。
- 文件：third_party/mcp-server-12306 原始快照与 MIT LICENSE／来源哈希；tools/setup_rail_mcp.py、依赖哈希锁、stdio 包装器；setup.ps1、config 和 README 等说明。
- 行为：clone 本项目即可取得 MCP 源码，默认安装入口仅需安装独立 Python 依赖，无另行 git clone、无 uv 前置要求；已有密钥配置保留，失败可估算回退。
- 验证：全新独立环境安装、源码导入路径和依赖检查通过；真实车次查询成功；发布快照 94 项 pytest 通过、1 项原生测试跳过。Windows PowerShell 入口未在本 Linux 主机实际执行。
