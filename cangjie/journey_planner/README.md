# Engine 2.2 约束与有限修复核心

来源是本项目早期开发副本的 `journey_planner` 2.1 源码和固定合成样例，沿用根目录 Mulan PSL v2。JSON 编解码又复用了该副本的 planner 2.0。当前仅扩展独立 2.2 协议对五种真实市内交通方式的接纳，不是原创规划算法，也不保证全局最优。

这套进程独立于既有可回退 Python 的 `validator`。约束修复必须有通过正反自检的 `journey_planner.exe`；缺失或技术失败时不调用 Python 搜索。旧旅行生成和既有规则校验继续使用原接口。

上界为 3 天、授权目录 24 项、每天 8 项、200 个展开状态和 2000 毫秒原生搜索。Python 进程边界额外限总执行时间 5 秒、输入与 stdout 各 1 MB、stderr 64 KB。交通模式允许 `walking/taxi/driving/transit/bicycling`，每一条边使用适配器提供的真实模式和冻结时长、费用；内核不联网、不发明新路线、不比较尚未提供的交通方式。缺失时长、费用、营业时间和边界地点会产生 unknown，不能当零成本或已验证。

独立构建（SDK 路径由本机指定）：

```powershell
.\cangjie\build_journey_planner.ps1 -SdkRoot '<existing-sdk-folder>'
.\.venv\Scripts\python.exe tools\check_constraint_planner.py
```

脚本只写本仓库被忽略的 `cangjie/bin/`，包括执行需要的运行 DLL、SDK 许可及二进制/源码哈希清单。不下载安装 SDK，不改其他项目或全局环境。清单不匹配当前源码或二进制时，桥接拒绝可用状态，需重新构建。

协议输入以固定样例 `fixtures/cases.json` 为可运行说明，所有金额是整数分，日索引映射到本次 0—2 号窗口。`repair` 输入 candidate=null；`evaluate` 为已给候选独立检查。源目录声明原始日时、停留时长、必去/想去/可放弃、是否漏游、保护、允许日期和开放证据。输出给出候选、差异、未安置项、费用、检查、八维词典序目标和有限搜索终止原因。没有候选不构成已证明的全局无解。

`backend/constraint_reference.py` 只重算身份、时序、日界、开放区间、保护与费用，不搜索。适配器还必须复核整趟多城费用、跨城交通、酒店与未选日期，不能把内部剩余额度检查当整趟旅行可执行证明。
