# 仓颉行程校验与调整模块

本目录提供两个本地进程，均通过标准输入、输出交换 UTF-8 JSON，不调用模型、地图或其他在线服务。金额使用整数分，时间使用整数分钟。源代码适用根目录 Mulan PSL v2 许可，运行库许可保留在 `bin` 中。

- `bin/validator.exe`：校验预算、时间、地点、路线与跨城衔接。Python 入口为 `backend/validation.py`；默认先与固定样例核对，原生程序不可用时回退 Python 校验。
- `bin/journey_planner.exe`：处理行程调整预览与有限候选搜索。Python 入口为 `backend/constraint_bridge.py`；通过原生自检和 Python 独立复核后才可应用，程序不可用时明确提示，不使用其他搜索冒充原生结果。

运行时请保留两个程序、本地 DLL、自检样例、`journey_planner/src/main.cj` 与 `bin/journey_planner.build.json`。源码与程序哈希不一致时，调整引擎会拒绝调用。正常使用无需安装 SDK 或重新编译。

调整引擎支持同城连续1—3天、最多24项活动及每天最多8项活动，使用已提供的路线、费用和开放信息。缺失资料保留待确认，硬冲突拒绝应用；已完成活动、预订事实及未选日期由上层业务保护。

需要修改仓颉源码时，使用自备 SDK 执行 `build.ps1` 或 `build_journey_planner.ps1 -SdkRoot <SDK目录>`，再运行 `tools/check_cangjie.py` 或 `tools/check_constraint_planner.py`。普通安装与启动步骤见[安装与启动](../docs/GETTING_STARTED.md)。
