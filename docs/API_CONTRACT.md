# 接口与数据约定

核对当前源码：2026-10-20。此文约定小队如何对接；完整字段、长度、范围与默认值以 `backend/models.py` 为准，前端消费类型在 `frontend/src/types.ts`，目前没有自动代码生成。后端启动后可访问 `/docs` 查看请求模型；SSE 和部分直接返回 dict 的响应仍以本说明及实现为准，OpenAPI 不会自动表达全部流协议。

## 统一规则

- 同源相对路径，无账号认证；本地原型不具备用户数据隔离。
- 请求/普通响应：JSON，字段 `snake_case`，直接返回对象/数组，无统一业务外壳。
- Pydantic 请求与业务模型拒绝额外字段。未知字段不是“会自动忽略”。
- `budget`、`unit_cost` 是人民币**元**，Decimal 在 JSON 响应中通常为字符串，前端容许 number/string；`*_cents`、`expense_totals` 值为整数**分**。
- 单位价格先 ROUND_HALF_UP 到分，再乘 quantity，`basis=person` 再乘人数；`group` 不乘人数。总计以服务端为准。
- 日期 `YYYY-MM-DD`；活动 `HH:MM` 为旅行当地钟表时间；版本/路线查询时间为带时区 ISO 时间。跨城交通用独立的出发／到达日期与时间支持跨午夜；游玩活动仍不跨午夜，均使用中国当地时间。
- `day_index` 从 0 起；地点 id 为本版本内标识，常见 `d1s1`，不能假设不同版本同 id 必为同地点。
- 坐标 `[longitude, latitude]`，GCJ-02；距离米、时长秒。校验转移时长向上取整为分钟。
- 错误优先用 HTTP 状态；普通错误 `{"detail":"中文说明"}`，422 的 detail 是验证错误数组。流开始后错误用 SSE error 事件，不能只看 HTTP 200 判定成功。

## 端点列表

| 方法/路径 | 输入 | 成功响应/行为 |
|---|---|---|
| `GET /api/health` | 无 | app_id、status、model、capabilities、missing、validator_diagnostic |
| `GET /api/map-config` | 无 | `{key, security_proxy}`；仅公开 JS Key 与代理地址 |
| `GET /api/cities?query=…` | query 最长 40 字 | 最多 8 条 `{name, province, adcode}` 城市建议；失败 503，允许用户继续手输 |
| `POST /api/plans/generate` | TripRequest | POST SSE，最终 result 为 PlanRecord |
| `GET /api/plans` | 无 | 最近 50 条摘要数组，created_at 倒序；目前无分页 |
| `GET /api/plans/{id}` | 版本 id | 完整 PlanRecord；不存在 404 |
| `POST /api/plans/{id}/validate` | 无请求体 | ValidationReport，使用存量路线、不调用模型、不写回历史 |
| `POST /api/plans/{id}/revise` | `{"instruction":"…"}` | POST SSE，新版本；不存在 404，空白要求 422 |
| `GET /_AMapService/{path}` | SDK 查询参数 | 受限高德代理；前缀只允许 v3/ 或 v4/map/，额外拦截危险路径 |

`/api/health` 的 `status=ok` 表示本服务响应，不表示远程 Key/余额有效。`missing` 当前检查模型 Key、Web 服务 Key、JS Key、安全密钥是否为空；按账号要求选填的签名私钥不在该列表。`planning` 取决于模型 Key + Web 服务 Key，`map` 取决于 JS Key + 安全密钥，`routing` 取决于 Web 服务 Key。校验器可为 `python`/`cangjie`。

地图代理必须保留为一级 `/_AMapService`，不能嵌到 `/api` 下。客户端 key/jscode 会被服务端值替换；禁止扩展成任意 URL 转发服务。

## 生成请求

下面是协议示例，未执行真实请求，日期按需调整：

```json
{
  "origin": "南京",
  "destination": "上海",
  "start_date": "2026-10-21",
  "start_time": "09:00",
  "days": 1,
  "style": "relaxed",
  "budget": "2000.00",
  "people": 2,
  "mode": "walking",
  "preferences": "以外滩、豫园为主，留足休息时间"
}
```

必填 start_date；destinations 或 destination 至少填写一种。推荐新表单 destinations=[{"city":"成都","preferred_days":2},{"city":"重庆","preferred_days":1}]，不发送 days，由服务端自动合计。兼容旧 API 的 null 表示按总天数自动分配；destinations 最多五项。city 去空白后是单个城市名（1–40 字）；preferred_days 默认 null（自动分配），可为 0.5–10、步长 0.5 的数值或十进制字符串。提供结构化列表时服务端生成 destination；同时传入的文本若顺序不一致则拒绝。兼容的 destination 为按顺序填写的 1–5 城文本，支持 →、->、逗号、顿号、分号或换行分隔，最多 240 字，每城最多 40 字；相邻城市不得相同。days 1–10，people 1–50，budget 大于 0 且不超过 10000000；origin 可为空。style 为 relaxed/balanced/compact，mode 为 walking/driving，preferences 最长 2000 字符。新增 intercity_mode=train/flight/bus/driving（默认 train）、include_origin_transport=false、return_to_origin=false。include_origin_transport 为真需要 origin；return_to_origin 为真需要 origin，并自动包含出发交通。旧 JSON 缺少这些字段时继续不计出发交通。历史 JSON 没有 destinations 时默认 []；旧六城记录仅在存储读取上下文允许查看，新生成／修改仍限制五城。默认值见模型，前端应明确发送用户选择值。

修改请求 instruction 去首尾空白后不能为空、最长 2000 字符；本接口使用原版本 request。如果要支持修改预算/日期等结构化需求，应设计接口变更并同步调用处，不能只改提示词就声称原 request 已改变。

## SSE 流

通过 `fetch` POST JSON 读取响应流，不能用原生 EventSource 替代 POST。当前流为 `text/event-stream`，事件块之间空行：

```text
event: status
data: {"message":"正在查询和整理城市资料"}

event: tool
data: {"name":"search_places","arguments":{"keywords":"外滩"},"summary":"找到若干地点"}

```

| 事件 | data | 如何处理 |
|---|---|---|
| `status` | `{message: string}` | 更新进度，不当作最终内容 |
| `tool` | `{name, arguments, summary}` | 展示工具概况，不是模型隐藏思考；工具错误也可能在 summary 中出现 |
| `result` | 完整 PlanRecord | 表示已经完成校验和保存；替换当前显示版本 |
| `error` | `{message: string}` | 显示失败，保留已显示的旧版本 |
| `: keep-alive` | SSE 注释 | 忽略 |

内部 `done` 不向浏览器发送；当前客户端通过 result 判断成功。正常关闭连接但从未收到 result 也视为失败。使用 TextDecoder 和缓冲区拼接，不能假设一次网络 chunk 就是一个完整事件；复用 `frontend/src/api.ts`。AbortController 用于取消；停止等待与后台任务取消都需保留。

## PlanRecord 与关键子对象

| 字段 | 说明 |
|---|---|
| id / parent_id / revision | 每次保存一个新版本；首次 parent_id=null、revision=1；修改指向所选父版本并 +1 |
| created_at | UTC 时间；不是日程活动时间 |
| request | 生成该版本所用原始需求 |
| itinerary | title、summary、days、tips、transfers |
| routes | 同一天相邻活动的 RouteLeg 数组 |
| validation | 当前记录保存时的校验报告；前端打开历史时另行重验 |
| revision_instruction | 本次修改要求；首次为空 |
| expense_totals | 按 category 汇总的整数分对象，服务端重新计算，不能由模型决定 |

版本号属于**父子链**，不是全库递增序号；同一旧版本可以产生多个 revision 相同的分支版本。id 才是唯一键。

- **DayPlan**：date、title、stops（0–12 个，纯移动日允许为空）、expenses（最多 20 项）、accommodation。
- **Stop**：city 默认空值；新多城市行程必须明确填写，旧单城市可回填目的地。字段 id/name/city/kind/start/end/description/poi_id/address/location/matched_name/location_status/opening_hours_status/location_note。kind 是 sight/meal/hotel/break；location 可 null，location_status 是 verified/unknown，opening_hours_status 当前只能 unknown。`types.ts` 只声明前端消费字段，不能误把未消费字段当作后端不存在。
- **CityTransfer**（itinerary.transfers，默认 []，最多 14 段）：from_city/to_city、departure_date/departure_time、arrival_date/arrival_time、mode=train/flight/bus/driving、unit_cost（元）、basis=person/group、estimated 固定 true、note。unit_cost 在服务器用 Decimal ROUND_HALF_UP 规范到两位小数，展示与计费一致。未查询时为模型估算；启用铁路服务且核验成功后可附实际时刻和席位快照，公布参考价仍不代表成交价，字段见下方铁路说明。按出发顺序排列；跨城费用由服务器统一计入 transport 分类，不应再重复放入日 expenses。
- **Expense**：label/category/unit_cost/quantity/basis/estimated/note；category 为 ticket/food/hotel/transport/other，quantity 1–1000；estimated 固定 true。
- **RouteLeg**：day_index/from_id/to_id/mode/available/distance_m/duration_s/polyline/queried_at/note。新增 scope=local/intercity，默认 local；跨城边界 scope=intercity、available=false、polyline=[]，前端查看独立跨城安排。available=false 时数值可 null、polyline 可空；禁止用缺失值 0 秒冒充已验证。
- **ValidationReport**：status/errors/warnings/unknowns/total_cents/budget_cents/backend/diagnostic。
- **Issue**：code/message/day_index/stop_id；全局问题后两项可 null。不要靠中文 message 匹配业务规则，使用稳定 code。

status 优先级：有 errors **或 warnings** 即 `issues`；否则有 unknowns 是 `passed_with_unknowns`；都没有才 `passed`。后端引擎名称与“方案有没有问题”是两个维度。

当前 issue codes：

| 分类 | code |
|---|---|
| 错误 | day_count、date_mismatch、before_start、over_budget、invalid_duration、time_overlap、transfer_time |
| 提醒 | missing_cost、duplicate_stop、stay_preference |
| 未知 | estimated_prices、opening_hours、missing_location、missing_route、intercity_estimates |

历史摘要字段：id/title/destination/start_date/revision/created_at/status。摘要 status 为保存时的值；打开历史后新校验结果可能不同，不代表重新查过路线或营业时间。

## Python ↔ 仓颉内部协议

这不是外部 HTTP 接口。Python 先 `normalized_input` 转成整数数据，再把一个 UTF-8 JSON 对象送入可执行程序 stdin，程序 stdout 返回一个 JSON 对象；日志走 stderr，不加 Markdown、BOM 或额外进度文字。

输入顶层：budget_cents、total_cents、pre_errors、pre_warnings、unknowns、days、legs。days[].stops[] 包含 id/name/identity/start_min/end_min/poi_ok/needs_poi/check_duplicate；legs[] 包含 day_index/from_id/to_id/available/duration_min。完整可执行输入见 [fixtures.json](../cangjie/fixtures.json)。

输出：status/errors/warnings/unknowns/total_cents/budget_cents；Python 包装时补 backend/diagnostic。仓颉不重新计算费用明细，只检查输入总额是否超预算。新增可选 journey 对象：cities（完整交通顺序）、destinations（目的地顺序）、start_min/end_min（旅行范围）、stops（含 city、绝对 start_min/end_min、day_index、id）、transfers（from_city/to_city、绝对 start_min/end_min、day_index）。绝对分钟从首日午夜起算；legs[].intercity=true 时跳过市内路线检查，由 journey 规则处理。journey.stays 可选，项为 city（展示名）、expected_halves、actual_halves（整数半天数）；偏好不一致时两套引擎返回 stay_preference 提醒。半天按同次城市访问的景点或自由活动覆盖上午／下午去重，排除餐饮、住宿和跨城；同城非相邻重访分别计数。该指标是偏好匹配估算，不代表精确驻留小时或车票时刻。journey 缺失时保持旧协议行为。

新增错误码：intercity_route、city_sequence、intercity_duration、intercity_dates、intercity_overlap、intercity_activity_overlap、city_arrival；未知码 intercity_estimates。

输入有 1 MB/深度 32 限制；适配器设 3 秒超时并拒绝异常退出、超大输出、非法格式和不一致金额/状态。

修改协议/规则时一并维护 `normalized_input`、`validate_core`、`validator.cj`、fixtures 与测试。默认 auto 只有原生程序和固定样例一致才启用；一致性比对包含数组顺序和文案，不只是 status。固定样例之外还要运行随机一致性检查。不要只改仓颉让 Python 静默回退，从而误以为新规则已经启用。

## 变更时的最小同步范围

新增字段：Python 默认值/兼容读取 → TS 类型 → 使用处 → 文档/有必要的测试。删除/改名/单位变化：先在队内约定，考虑本地已有 JSON 历史，不要默认可以删库。新增校验：Python + 仓颉 + fixtures。新增流事件：服务端 + api.ts/App.vue + 本说明。不要为当前小队对接随意引入第二套 response 外壳、状态码系统或相同语义的新枚举。


## 铁路查询字段（可选接入）

不增加公开查询端点。`capabilities.railway` 仅表示开关开启且独立 Python 存在，不代表上游实时可用。模型工具 `query_train_tickets(from_city,to_city,travel_date,earliest_time?)` 只允许本次完整城市链中相邻的有向城市段、行程日期和当前预售期，最多返回八个候选。

CityTransfer 新字段均有默认值，旧 SQLite 记录继续可读：

| 字段 | 含义 |
|---|---|
| rail_option_id | 当前请求服务端选项凭据；历史值不能作为新请求的证据 |
| schedule_status | estimated（默认）或 queried；仅服务端本次查询可写 queried |
| price_status | estimated（默认）或 reference（公布参考价） |
| train_code / departure_station / arrival_station | 已查询的实际车次和乘车站；默认空字符串 |
| seat_type / seat_availability | 席别、查询时席位快照；默认空字符串，非占座承诺 |
| queried_at / source | UTC 查询时间（默认 null）、来源（默认空字符串） |
| query_note | 查询未成功或没有适配时段等原因，默认空字符串 |

铁路时间使用乘车当地日期及 HH:MM，跨午夜按历时推导到达日期。unit_cost 继续以元表示；得到关联的席别公布价时设 basis=person，price_status=reference。estimated=true 继续保留，公布价不等于实际成交价。未得到价格时保留模型估算，并显示 price_status=estimated。重新校验不刷新查询快照；修改请求重新查询，不复用历史选项凭据。


## 表单简化与天数自动汇总

新表单不再填写旅行总天数；每个目的地 preferred_days 默认 1，可按半天选择，提交时不发送 days。若请求未显式提供 days 且 destinations 的时长均非 null，服务端按 Decimal 求和后向上取整得到 days，仍限制 1–10 天（如 1.5+1=3，0.5+0.5=1）。响应和存储继续包含 days，供日期、预算和校验使用。显式 days 的旧 API／历史记录保持原语义；旧自动分配项在表单重新规划时默认显示 1 天，不回写原记录。返程勾选及对应 return_to_origin 协议保留。市内出行控件仅从界面隐藏，mode 协议继续支持 walking/driving；新建默认 walking，历史记录仍保留原 mode。
