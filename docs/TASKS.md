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
