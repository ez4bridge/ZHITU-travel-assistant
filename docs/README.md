# 小队文档入口

适用于四人、几天内完成的原型开发。先跑通，再按模块分工；默认大家共用 main，无强制 PR。

| 你要做什么 | 读哪份 |
|---|---|
| 新成员第一次接手 | [运行与开发教程](GETTING_STARTED.md) |
| 了解当前功能与技术 | [当前实现介绍](PROJECT_STATUS.md) |
| 让新的 Codex 接手 | 根目录 [AGENTS.md](../AGENTS.md)，再读本页 |
| 四人分工、同步代码、处理冲突 | [轻量协作约定](COLLABORATION.md) |
| 改 API、字段、SSE 或仓颉协议 | [接口与数据约定](API_CONTRACT.md) |
| 记录工作与交接 | [工作记录模板](TASKS.md) |
| 理解 Agent 和数据经过哪些模块 | [架构与技术流程](ARCHITECTURE.md) |
| 填写密钥、换供应商、查地图问题 | [配置说明](CONFIGURATION.md) |
| 看此前真实测过什么 | [2026-10-14 验收记录](ACCEPTANCE.md) |
| 看代码/素材来源与许可证 | [来源说明](ATTRIBUTION.md) |
| 安装、配置与单独测试铁路查询 | [12306 MCP](RAIL_MCP.md) |
| 编译可选的仓颉校验器 | [仓颉目录说明](../cangjie/README.md) |

当前实现说明的核对日期为 **2026-10-20**。验收记录保留实际测试日期；它不是对后续所有改动的测试保证。完整数据结构以 `backend/models.py` 为准。

给新 Codex 的第一条消息可以直接写：

> 请先读根目录 AGENTS.md、docs/PROJECT_STATUS.md 和 docs/COLLABORATION.md，检查当前 Git 状态。我的任务是【具体功能】，主要修改【目录/文件】。先核对现有实现，再完成改动与相关验证；如需改变公共接口，同步类型和文档，不覆盖别人的工作。
