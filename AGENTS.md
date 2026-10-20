# 给参与本项目的 Codex

本文件适用于整个仓库。我们是四人小队，开发周期只有几天，采用共用 `main` 的轻量协作。把一个改动做完整、让队友接得上即可，不需要自行引入审批流程、强制 PR 或大型工程框架。用户在当前会话中的明确要求优先。

## 开始工作

1. 阅读本文件、`README.md`、`docs/PROJECT_STATUS.md`；涉及接口再读 `docs/API_CONTRACT.md`。
2. 查看 `git status --short` 和当前分支，理解已有改动。未提交的内容可能属于用户或其他 Codex，不要覆盖、清理或擅自提交。
3. 在自己的机器、自己的 clone 中工作。共用 main 不代表四个进程共用同一个工作目录。
4. 工作区干净时先同步远端；有本地修改时先保存自己的工作，不直接用 reset/clean 强行恢复。Git 提交和推送遵循当前用户授权，不因本文件存在就自动发布。
5. 找到相关实现后再改，不能只凭本说明猜代码。`backend/models.py` 是完整后端数据结构的依据；前端 `types.ts` 是对应消费类型，两者目前手工维护。

## 当前实现

- Windows 浏览器应用，Python + FastAPI + LangGraph，Vue 3 + TypeScript + Vite。
- 已实现：全国 1–5 个目的地按序规划、逐城游玩天数与自动汇总、可选出发／返程交通、真实 POI/步行或驾车路线、时间轴地图联动、预算与时间校验、每次点击一轮修改、SQLite 历史版本。
- 可选 12306 MCP 的固定源码、数据与 MIT 许可证随仓库放在 `third_party/mcp-server-12306/`，setup 默认安装独立依赖环境，查询直达列车时刻、席位快照和公布参考价；不提供订票，失败保留估算。新建市内方式默认 auto，按高德接口耗时选择最快可用方式，保留逐段手动切换。
- 仓颉只在 `cangjie/`，负责可选的确定性校验。Python 始终保留等价实现与故障回退，不为仓颉阻塞主体功能。
- 保持独立项目，不改旁边的原 `NVIDIA-TRAVEL` 或其他项目，不动系统 Python/Node/仓颉环境。

## 代码与接口风格

- Python：4 空格、函数/变量 `snake_case`、类 `PascalCase`；沿用周围代码的单引号与导入方式。新增边界函数写清输入输出类型，复杂逻辑拆成职责明确的小函数。
- Vue：Composition API、`<script setup lang="ts">`、2 空格、变量/函数 `camelCase`、组件 `PascalCase.vue`。复用 `api.ts`/`types.ts`，SDK 的局部 opaque handle 除外，不随手扩散 `any`。
- 注释解释原因与约束，不复述代码。用户文案使用中文，技术标识保持英文。只格式化本次涉及部分，不整仓改格式、不顺手重命名公共字段。
- HTTP 路由集中于 `backend/main.py`，模型在 `models.py`，工具在 `amap.py`，规则在 `validation.py`，存储在 `storage.py`。新业务优先放独立模块，避免继续堆进 `App.vue`/`main.py`。
- JSON 使用现有 `snake_case`；默认新增可选、有默认值的字段，兼顾旧 SQLite 记录。更改字段含义、枚举、单位或流事件时，同步 Python 模型、TS 类型、调用处、文档与必要测试。
- 普通接口直接返回数据，不自行增加 `{code,data,msg}` 外壳。生成/修改继续使用 POST + fetch SSE，沿用 `status/tool/result/error` 事件。
- 费用明细和请求预算单位是人民币元；`*_cents` 与 `expense_totals` 是整数分。金额计算由 Python Decimal 完成，前端只展示。全员预算不能悄悄改为人均。
- 日期 `YYYY-MM-DD`，活动时间 `HH:MM`，路线米/秒；`day_index` 从 0 开始；坐标 `[经度, 纬度]`，GCJ-02。

## 必须保留的行为

- 不相信模型坐标或“已验证”声明；地点从本次目的地城市查询结果核实。缺少真实道路形状时标记不可用，不用直线冒充导航。
- 费用仍标估算，营业时间/预约未核实时仍为未知。`passed_with_unknowns` 不能显示为所有事实已核实。
- 活动修改生成新 id，记录 parent_id、revision，保留旧方案；仅切换交通或活动结构不变的路线刷新更新当前记录并递增 routing_revision。重新校验不调用模型、不刷新路线、不覆盖历史。
- 每次生成状态、工具缓存、队列独立；保留取消和超时。不要加无限重试、无限自动修正或跨请求全局聊天记忆。
- 保留根级 `/_AMapService`、代理路径限制、服务端密钥注入、错误脱敏与关闭外部追踪。
- 仓颉 stdout 只输出协议 JSON，诊断走 stderr。新增规则同时改 Python/仓颉/fixtures，运行一致性检查；技术失败回退，普通业务问题不回退。

## 配置与文件

- 实际密钥只放本机被忽略的 `.env.local`，示例用空值。不要读取并展示真实配置，不复制进源码、文档、测试、日志、截图或前端 `VITE_*` 变量。
- 配置统一从 `backend/config.py` 获取；新增配置同步 `.env.example` 和 `docs/CONFIGURATION.md`。Web JS Key 是公开客户端标识，其余私密值不下发前端。
- 不提交 `.venv`、`.tools`、`.runtime`、node_modules、dist、SDK、exe/DLL、数据库。保留依赖锁文件与源码。不要批量升级依赖；必要变更同步对应锁文件并验证。
- 文档用相对仓库路径和可替换的 clone 路径，不依赖创建者机器的绝对目录。

## 四个 Codex 一起改

- 具体内容与分工以小队实际讨论为准，不自行预设路线图或给其他成员分派功能；协作方式见 `docs/COLLABORATION.md`。
- 改 `models.py`、`types.ts`、`main.py`、`App.vue`、锁文件、仓颉协议前，让当前用户在小队里说明涉及文件与接口；已获授权且约定清楚就继续，不新增确认门槛。
- 没有显式授权时，不替用户给其他聊天、GitHub 或群聊发消息。交接内容可写在最终回复或任务记录中。
- 禁止用 force push、reset --hard、清空数据库解决同步问题。推送被拒绝先拉取合并；冲突按双方意图处理，不能无脑全选 ours/theirs。
- `git add` 优先列出本次文件；已有其他改动时不使用 `git add .`。提交前看暂存差异，防止带入队友工作与密钥。

## 收尾验证

- 纯文档：检查链接、命令、状态陈述；不为文字改动调用付费接口。
- 后端行为：运行相关 pytest；涉及核心规则/流式/存储时运行 `python -m pytest -q`。使用项目 `.venv\Scripts\python.exe`。
- 前端：用项目 Node 执行 `npm run build`（包含类型检查）；界面改动还要看实际页面，至少检查桌面和窄屏受影响区域。
- 仓颉：重新编译，执行 `tools/check_cangjie.py`；记录实际后端，不能只跑到 Python 回退就声称仓颉成功。
- `tools/live_check.py` 会调用真实模型和高德，只有任务需要且有授权才运行，不作为每次改动的默认检查。
- 最终说明：改了什么、涉及哪些接口/配置、实际做过哪些验证、还剩什么。未执行的测试直说。新增功能或改变边界同步 `docs/PROJECT_STATUS.md`；历史验收记录不要直接改日期冒充重验。
