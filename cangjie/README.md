# 可选仓颉行程校验模块

本目录集中保存仓颉代码、编译脚本和跨语言固定样例。主体应用是 Python + Vue，仓颉程序不会调用大模型或高德，也不承担 PDF 解析。

职责：预算、活动时间重叠、相邻活动通行时间、重复景点、缺失地点与路线的确定性校验。

接口：标准输入接收一个 UTF-8 JSON 对象；标准输出返回一个 UTF-8 JSON 校验结果；诊断放到标准错误。输入金额为整数分、时间为整数分钟。Python 负责把完整旅行方案规范化，仓颉负责校验规则。

Python 入口：`backend/validation.py`。默认 `VALIDATOR_BACKEND=auto`：只有找到 `bin/validator.exe`，且运行固定样例与 Python 结果一致后才启用。程序缺失、崩溃、超时、JSON 格式错误会回退 Python；普通的行程问题不会触发回退。

开发环境、可执行文件与构建产物保存在本目录的忽略目录，不修改全局仓颉设置。实际编译与测试情况以 `docs/ACCEPTANCE.md` 为准。

全国多城市扩展使用可选 journey 对象检查城市链、跨城覆盖、跨午夜绝对分钟、交通与活动冲突；intercity 边界不要求市内导航。对应 Python 在 backend/journey.py 和 validation.py，协议见 docs/API_CONTRACT.md。旧协议不含 journey 时行为不变；新增源码需重新编译并跑一致性检查，本地未编译时仍用 Python。

journey.stays 可选字段用于期望时长匹配：expected_halves／actual_halves 均为整数半天数，差异产生 stay_preference 提醒。Python 在规范化阶段按访问次序计算活动半天数，原生与 Python 引擎输出相同文案；新增固定样例与随机检查已覆盖。

## 独立 Engine 2.2：约束与途中调整

本轮另加入 `journey_planner/src/main.cj`，复用早期开发副本的 Engine 2.1，沿用根目录 Mulan PSL v2；当前协议升级为 2.2，明确接纳步行、打车、自驾、公交地铁与骑行五种真实方式。它是有限候选搜索和确定性检查的工程复用，不宣称原创算法或全局最优。

两套原生程序职责和故障行为不同：

| 程序 | Python 入口 | 职责及故障行为 |
|---|---|---|
| `bin/validator.exe` | `backend/validation.py` | 原有整趟规则校验；技术失败仍按既有设置回退 Python |
| `bin/journey_planner.exe` | `backend/constraint_bridge.py` | 新增可选工作区的约束检查、差异和有限修复；必须通过独立原生自检，不能回退 Python 搜索 |

新核心只调整用户选择的同城连续 1—3 天，授权活动目录最多 24 项，每天最多 8 项，最多展开 200 个状态、搜索 2000 毫秒。每条道路使用提供的真实交通模式及冻结资料，不自行换名或制造道路。已完成内容、未授权修改的跨城交通和确认酒店由适配器保护；Python 独立协议复核后还运行当前多城市完整校验。缺少资料保留待核实，硬冲突拒绝应用，预览不会提交事实，用户确认后才保存子版本。详见[实施契约](../docs/CONSTRAINT_REPLAN.md)及[核心协议说明](journey_planner/README.md)。

使用本机已有的仓颉 1.0.5 SDK 构建，不需要重新下载；把示例路径替换为实际 SDK 根目录：

```powershell
.\cangjie\build_journey_planner.ps1 -SdkRoot '<existing-sdk-folder>'
.\.venv\Scripts\python.exe tools\check_constraint_planner.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_constraint_native.py
```

构建脚本只读取指定 SDK，输出新程序、运行 DLL、SDK 许可及源码／二进制哈希清单到本仓库忽略目录 `cangjie/bin/`。本轮使用另一个本机开发副本已有的 SDK，只读借用，未修改该副本或全局环境。桥接检查清单与当前源码、二进制一致，并执行真实独立进程正反自检；源码变化后须重新构建。

2026-10-20 本轮新核心实际验收：**27 个固定合成案例、19 个坏协议拒绝案例、80 个固定种子案例通过；38 项原生边界 pytest 通过**。固定案例包含五种方式、预算、硬锁、漏游和未知资料，使用真实 Engine 2.2 进程及 Python 独立复核。证据为本机 `.runtime/constraint-native-check/summary.json` 与 `fixed-results.json`；这些是离线合成验收，不代表铁路库存、真实地图、第二台电脑或比赛材料已验收。

上述新核心通过情况不证明旧 `validator.exe` 当前可用；旧校验器仍须单独构建、自检，并以运行时实际后端及诊断为准。
