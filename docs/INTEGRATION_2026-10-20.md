# 2026-10-20 主分支整合与本地优化交接

## 本次整合范围

基于 main 698e860，先备份本地全部工作，再快进至远端 b64940d，恢复本地优化。仅 PROJECT_STATUS.md 出现文本冲突，保留双方记录。未覆盖协同开发的目的地校验和全国多城／铁路能力。用户已明确授权本次提交并推送 main；本机密钥、历史、依赖与构建产物不发布。

远端新增 b64940d（协同开发贡献）：固定 12306 MCP 源码、车站数据、MIT 许可证、逐文件来源哈希与锁文件收录于 third_party/mcp-server-12306；setup 默认安装 .tools/rail-mcp/.venv，可 SkipRailMcp。无需额外克隆或 uv；新模板启用铁路，现有 .env.local 不覆盖。支持直达时刻、席位快照、公布参考价；未安装、未启用或查询失败仍有明确估算回退。不提供购票或铁路换乘查询。

Windows 合并验证发现 core.autocrlf 转换第三方文件后破坏来源哈希。新增 .gitattributes，限定第三方目录 -text；按 HEAD 原始 blob 和 UPSTREAM.json 双重校验恢复 LF 原始字节，不改上游代码或哈希。合并后的铁路日期已采用 UTC+8 固定时区，不再需要此前临时增加的 tzdata 主应用依赖。

## 本地累计优化（本次一起发布）

| 内容 | 最终行为 | 主要入口 |
|---|---|---|
| 地图失败排查及限流 | 区分鉴权、日额度、短时频率、IP 与网络；按接口共享限速、有限重试；自驾和打车复用道路；备选失败不撤销可用主路线 | amap.py、map_rate_limit.py、config.py |
| 运行版本识别 | health 返回 build_id、失败阶段和任务统计；start 识别旧进程，restart 安全重启并保留数据库 | build_info.py、main.py、start.ps1、restart.ps1 |
| 多方式与最快交通 | 五种真实交通候选，按接口完整可用方案耗时自动选最快；用户仍可逐段手选；打车不是网约车实时报价 | amap.py、RouteChoices.vue |
| 行程版本 | 单段交通切换保存同 id；活动结构不变的刷新更新原记录；活动变化保存新版本；routing_revision 和事务防并发覆盖 | main.py、storage.py |
| 顺路餐饮与住宿 | 根据前后景点真实道路距离挑附近真实餐馆，保护明确指定餐馆；未确认酒店只推荐片区，不固定为路线端点 | locality.py |
| 费用参考 | 自驾、打车、骑行给出地区公开费率或明确预算假设、区间和出处；按人／车辆计费，Decimal 汇总 | transport_cost.py、planning.py |
| 可执行性 | 未定位或无真实道路阻断；支持明确原地休息，拒绝模糊餐馆；检查日期、时间、缺费、步行量和节奏，有界修复排程 | models.py、validation.py、cangjie/ |
| 区域选点 | 从地图返回的真实子地点随机选择，过滤停车场、售票处等设施，或使用明确导航入口；刷新保留已核实点，无虚构坐标 | place_info.py |
| 每地点信息 | Expense.stop_id 关联唯一账本；地图商业信息和营业参考带来源、查询时间及区域／地点范围；平均消费不冒充门票 | place_info.py、itinerary_evidence.py、StopFacts.vue |
| 攻略参考 | 正文导入，默认优先地点和合理顺序；连续原文摘录、活动关联、调整与未采纳原因；程序复核摘录和地点关系 | agent.py、itinerary_evidence.py、GuideEvidence.vue |
| 用餐安排 | 优先地方特色、攻略菜品；真实通勤空档允许时安排常见饭点；无法安排提醒沿途就餐／自带干粮，不虚构餐厅 | meals.py、locality.py |
| 表单与样式 | 删除默认市内方式、攻略链接／日期／标题输入；攻略自动编号，相关选项按需显示；沿用现有字号和布局 | App.vue、GuideInput.vue、style.css |
| 其他操作 | 支持只改当天、同步结构化需求、删除单版本；保留取消和超时，本机 Host/Origin 保护及任务容量限制 | main.py、api.ts、App.vue |

## 公共接口与配置

- models.py / types.ts 同步：mode=auto 默认，实际路线仍为五种具体方式；guides、guide_mode、guide_decisions；区域定位、营业参考、菜品和餐饮提醒；费用 stop_id；routing_revision、schedule_baseline、user_selected。
- 新增 POST /api/plans/{id}/refresh、POST /api/plans/{id}/route、DELETE /api/plans/{id}；revise 增加 trip、day_index。SSE 事件和金额单位不变；validate 仍离线且不写历史。
- AMAP_QPS 默认 2，控制本进程同接口频率，不能协调其他机器共用账号的请求。无新的必需 Key。
- 远端铁路配置 RAIL_MCP_ENABLED、RAIL_MCP_PYTHON 保留；安装／启停见 RAIL_MCP.md。拉取源码不会自动修改现有配置或安装环境；不要将“已加入仓库”当作“本机已运行”。
- 旧数据库通过默认字段兼容，不清库、不自动改写旧行程；历史文档中的旧默认值与旧测试数量仅对应当时快照，以当前 README 和本记录为准。

## 本轮验证（2026-10-20，Windows）

- 完整 pytest：166 passed、1 skipped；覆盖新增铁路打包／哈希和所有本地优化。原生仓颉不可用而跳过一项，两条上游弃用提醒不影响通过。
- 前端 TypeScript 和 Vite 生产构建通过；主应用 pip check 通过。
- 仓颉重新编译成功；tools/check_cangjie.py 仍报原生执行超过 3 秒，不宣称一致性通过，实际使用 Python 回退。
- 此前本机独立测试库已完成真实区域、餐馆、道路、营业参考和两次完整模型生成；详见 PLACE_GUIDE_2026-10-20.md。桌面1440px／窄屏390px已检查攻略、费用、菜品、无餐提醒、交通切换和表单，无横向溢出。浏览器底图屏蔽与真实 API 验证分别记录，不混为一谈。
- 合并后再次运行实际浏览器验证：1440px／390px地点费用、攻略关联、特色菜、无餐提醒正常，无脚本异常和横向溢出；手动切换前后同 id、测试库历史数量均为5。
- Windows 启动脚本的两个测试在非 Windows 环境明确跳过，不要求 Linux 安装 PowerShell。
- 本次整合不重复调用付费模型，不自动启用本机铁路配置，也不将远端在另一台机器的真实铁路验收当成本机测试。

## 使用与限制

队友拉取后按 README 运行 setup.bat（含铁路独立依赖）、构建前端并 restart.bat。已有配置若关闭铁路，按 RAIL_MCP.md 启用后重启；已有行程不会自动重写，可主动刷新或重新规划。

路线依赖地图实际返回，不保证全局最优或无任何绕路；餐饮优化只做局部真实距离筛选。区域锚点不代表整个游览区域的步行量。营业参考不保证旅行日开放或预约名额，攻略未自动搜索抓取。费用均有参考性质，未知不当作免费。原生仓颉故障、铁路仅直达、本机单用户范围等限制均保留。
