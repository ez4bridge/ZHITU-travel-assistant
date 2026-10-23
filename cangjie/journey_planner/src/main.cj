// Travel Assistant independent journey planner 2.2. Mulan PSL v2.
// JSON codec adapted from the project planner 2.0; old source and protocol remain unchanged.
// A bounded JSON codec for the internal integer/UTF-8 contract; no stdx dependency.
import std.collection.*
import std.env.*
import std.time.*

class Json {
    public var kind: Int64
    public var number: Int64 = 0
    public var text: String = ""
    public var flag: Bool = false
    public let items = ArrayList<Json>()
    public let keys = ArrayList<String>()

    public init(kind: Int64) { this.kind = kind }

    public func get(key: String): Json {
        for (i in 0..keys.size) {
            if (keys[i] == key) { return items[i] }
        }
        throw Exception("missing JSON field")
    }

    public func put(key: String, value: Json): Unit {
        if (has(key)) { throw Exception("duplicate JSON key") }
        keys.add(key)
        items.add(value)
    }

    public func has(key: String): Bool {
        for (item in keys) { if (item == key) { return true } }
        return false
    }

    public func set(key: String, value: Json): Unit {
        for (i in 0..keys.size) { if (keys[i] == key) { items[i] = value; return } }
        put(key, value)
    }

    public func copy(): Json {
        let result = Json(kind)
        result.number = number; result.text = text; result.flag = flag
        for (item in keys) { result.keys.add(item) }
        for (item in items) { result.items.add(item.copy()) }
        return result
    }

    public func render(): String {
        if (kind == 0) { return "null" }
        if (kind == 1) { return jsonQuote(text) }
        if (kind == 2) { return if (flag) { "true" } else { "false" } }
        if (kind == 3) { return number.toString() }
        var result = if (kind == 4) { "[" } else { "{" }
        for (i in 0..items.size) {
            if (i > 0) { result += "," }
            if (kind == 5) { result += jsonQuote(keys[i]) + ":" }
            result += items[i].render()
        }
        return result + if (kind == 4) { "]" } else { "}" }
    }
}

func jsonQuote(text: String): String {
    let output = ArrayList<UInt8>()
    output.add(34)
    for (byte in text.toArray()) {
        if (byte == 34 || byte == 92) {
            output.add(92); output.add(byte)
        } else if (byte == 10) {
            output.add(92); output.add(110)
        } else if (byte == 13) {
            output.add(92); output.add(114)
        } else if (byte == 9) {
            output.add(92); output.add(116)
        } else if (byte < 32) {
            let hex = "0123456789abcdef".toArray()
            for (part in "\\u00".toArray()) { output.add(part) }
            output.add(hex[Int64(byte) / 16])
            output.add(hex[Int64(byte) % 16])
        } else { output.add(byte) }
    }
    output.add(34)
    return String.fromUtf8(output.toArray())
}

class Parser {
    let bytes: Array<UInt8>
    var position: Int64 = 0
    public init(input: String) {
        if (input.size > 1000000) { throw Exception("input too large") }
        bytes = input.toArray()
    }
    func peek(): UInt8 {
        if (position >= bytes.size) { throw Exception("unexpected end") }
        return bytes[position]
    }
    func space(): Unit {
        while (position < bytes.size && (bytes[position] == 32 || bytes[position] == 9 || bytes[position] == 10 || bytes[position] == 13)) {
            position++
        }
    }
    func expect(byte: UInt8): Unit {
        space()
        if (peek() != byte) { throw Exception("invalid JSON") }
        position++
    }
    func word(text: String): Unit {
        for (byte in text.toArray()) {
            if (peek() != byte) { throw Exception("invalid literal") }
            position++
        }
    }
    func hex4(): Int64 {
        var result: Int64 = 0
        for (_ in 0..4) {
            let byte = peek(); position++
            let digit = if (byte >= 48 && byte <= 57) { Int64(byte) - 48 }
                else if (byte >= 65 && byte <= 70) { Int64(byte) - 55 }
                else if (byte >= 97 && byte <= 102) { Int64(byte) - 87 }
                else { throw Exception("invalid unicode escape") }
            result = result * 16 + digit
        }
        return result
    }
    func appendScalar(output: ArrayList<UInt8>, scalar: Int64): Unit {
        if (scalar < 128) { output.add(UInt8(scalar)) }
        else if (scalar < 2048) {
            output.add(UInt8(192 + scalar / 64)); output.add(UInt8(128 + scalar % 64))
        } else if (scalar < 65536) {
            output.add(UInt8(224 + scalar / 4096)); output.add(UInt8(128 + scalar / 64 % 64))
            output.add(UInt8(128 + scalar % 64))
        } else {
            output.add(UInt8(240 + scalar / 262144)); output.add(UInt8(128 + scalar / 4096 % 64))
            output.add(UInt8(128 + scalar / 64 % 64)); output.add(UInt8(128 + scalar % 64))
        }
    }
    func stringValue(): String {
        expect(34)
        let output = ArrayList<UInt8>()
        while (peek() != 34) {
            var byte = peek(); position++
            if (byte == 92) {
                byte = peek(); position++
                if (byte == 34 || byte == 92 || byte == 47) { output.add(byte) }
                else if (byte == 110) { output.add(10) }
                else if (byte == 114) { output.add(13) }
                else if (byte == 116) { output.add(9) }
                else if (byte == 98) { output.add(8) }
                else if (byte == 102) { output.add(12) }
                else if (byte == 117) {
                    var scalar = hex4()
                    if (scalar >= 55296 && scalar <= 56319) {
                        if (peek() != 92) { throw Exception("invalid surrogate") }
                        position++
                        if (peek() != 117) { throw Exception("invalid surrogate") }
                        position++
                        let low = hex4()
                        if (low < 56320 || low > 57343) { throw Exception("invalid surrogate") }
                        scalar = 65536 + (scalar - 55296) * 1024 + low - 56320
                    } else if (scalar >= 56320 && scalar <= 57343) { throw Exception("invalid surrogate") }
                    appendScalar(output, scalar)
                } else { throw Exception("invalid escape") }
            } else {
                if (byte < 32) { throw Exception("control character") }
                output.add(byte)
            }
        }
        position++
        return String.fromUtf8(output.toArray())
    }
    func value(depth: Int64): Json {
        if (depth > 32) { throw Exception("JSON too deep") }
        space()
        let first = peek()
        if (first == 34) { let result = Json(1); result.text = stringValue(); return result }
        if (first == 110) { word("null"); return Json(0) }
        if (first == 116 || first == 102) {
            let result = Json(2); result.flag = first == 116
            word(if (result.flag) { "true" } else { "false" }); return result
        }
        if (first == 91 || first == 123) {
            position++
            let result = Json(if (first == 91) { 4 } else { 5 })
            let close: UInt8 = if (first == 91) { 93 } else { 125 }
            space()
            if (peek() == close) { position++; return result }
            while (true) {
                if (first == 123) {
                    let key = stringValue(); expect(58)
                    result.put(key, value(depth + 1))
                } else { result.items.add(value(depth + 1)) }
                space()
                if (peek() == close) { position++; break }
                expect(44)
            }
            return result
        }
        let result = Json(3)
        let negative = first == 45
        if (negative) { position++ }
        var count: Int64 = 0
        let leadingZero = peek() == 48
        while (position < bytes.size && bytes[position] >= 48 && bytes[position] <= 57) {
            result.number = result.number * 10 + Int64(bytes[position]) - 48
            position++; count++
            if (count > 18) { throw Exception("integer too large") }
            if (leadingZero && count > 1) { throw Exception("leading zero") }
        }
        if (count == 0) { throw Exception("expected integer") }
        if (negative) { result.number = -result.number }
        return result
    }
    public func parse(): Json {
        let result = value(0); space()
        if (position != bytes.size) { throw Exception("trailing JSON input") }
        return result
    }
}

func stringNode(text: String): Json { let result = Json(1); result.text = text; return result }
func numberNode(number: Int64): Json { let result = Json(3); result.number = number; return result }

func require(value: Bool): Unit { if (!value) { throw Exception("invalid journey protocol") } }
func shape(node: Json, fields: Array<String>): Unit {
    require(node.kind == 5 && node.keys.size == fields.size)
    for (field in fields) { require(node.has(field)) }
}
func text(node: Json): String { require(node.kind == 1 && node.text.size <= 2048); return node.text }
func ident(node: Json): String {
    let value = text(node); require(value.size > 0 && value.size <= 640)
    var scalars: Int64 = 0
    for (byte in value.toArray()) { require(byte >= 32 && byte != 127); if (byte < 128 || byte >= 192) { scalars++ } }
    require(scalars <= 160); return value
}
func wideIdent(node: Json): String {
    let value = text(node); require(value.size > 0)
    for (byte in value.toArray()) { require(byte >= 32 && byte != 127) }; return value
}
func integer(node: Json, low: Int64, high: Int64): Int64 { require(node.kind == 3 && node.number >= low && node.number <= high); return node.number }
func boolean(node: Json): Bool { require(node.kind == 2); return node.flag }
func array(node: Json, maximum: Int64): Unit { require(node.kind == 4 && node.items.size <= maximum) }
func member(value: String, values: Array<String>): Bool { for (item in values) { if (value == item) { return true } }; return false }
func enumeration(node: Json, values: Array<String>): String { let value = text(node); require(member(value, values)); return value }
func includes(ids: Json, id: String): Bool { for (item in ids.items) { if (item.text == id) { return true } }; return false }
func pushId(ids: Json, id: String): Unit { if (!includes(ids, id)) { ids.items.add(stringNode(id)) } }
func idsValid(ids: Json, maximum: Int64): Unit {
    array(ids, maximum); let seen = HashSet<String>()
    for (item in ids.items) { let id = ident(item); require(!seen.contains(id)); seen.add(id) }
}
func findTask(input: Json, id: String): Json { for (task in input.get("source_catalog").items) { if (task.get("task_id").text == id) { return task } }; return Json(0) }
func findDay(input: Json, index: Int64): Json { for (day in input.get("days").items) { if (day.get("day_index").number == index) { return day } }; return Json(0) }
func findAssignment(assignments: Json, id: String): Json { for (item in assignments.items) { if (item.get("task_id").text == id) { return item } }; return Json(0) }
func active(input: Json, id: String): Bool { return !includes(input.get("completed_ids"), id) && !includes(input.get("cancelled_ids"), id) }
func allowed(task: Json, index: Int64): Bool { for (day in task.get("allowed_day_indexes").items) { if (day.number == index) { return true } }; return false }
func assignment(id: String, day: Int64, start: Int64, end: Int64): Json {
    let result = Json(5); result.put("task_id", stringNode(id)); result.put("day_index", numberNode(day)); result.put("start_min", numberNode(start)); result.put("end_min", numberNode(end)); return result
}
func assignmentValid(node: Json): Unit {
    shape(node, ["task_id", "day_index", "start_min", "end_min"]); ident(node.get("task_id"))
    integer(node.get("day_index"), 0, 2); integer(node.get("start_min"), 0, 1439); integer(node.get("end_min"), 0, 1439)
}
func dateValid(node: Json): Unit {
    let bytes = text(node).toArray(); require(bytes.size == 10 && bytes[4] == 45 && bytes[7] == 45)
    var year: Int64 = 0; var month: Int64 = 0; var day: Int64 = 0
    for (i in 0..10) {
        if (i == 4 || i == 7) { continue }; require(bytes[i] >= 48 && bytes[i] <= 57)
        let digit = Int64(bytes[i]) - 48
        if (i < 4) { year = year * 10 + digit } else if (i < 7) { month = month * 10 + digit } else { day = day * 10 + digit }
    }
    require(year >= 1 && month >= 1 && month <= 12)
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
    let lengths: Array<Int64> = [31, if (leap) { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    require(day >= 1 && day <= lengths[month - 1])
}
func inputValid(input: Json): Unit {
    shape(input, ["schema_version", "request_id", "op", "days", "source_catalog", "baseline", "candidate", "completed_ids", "cancelled_ids", "budget_cents", "cost_catalog", "routes", "limits"])
    require(text(input.get("schema_version")) == "2.2"); ident(input.get("request_id"))
    let op = enumeration(input.get("op"), ["evaluate", "repair"])
    array(input.get("days"), 3); array(input.get("source_catalog"), 24); array(input.get("baseline"), 24)
    idsValid(input.get("completed_ids"), 24); idsValid(input.get("cancelled_ids"), 24)
    integer(input.get("budget_cents"), 0, 10000000000000000)
    let endpoints = HashSet<String>(); let dayIds = HashSet<Int64>()
    for (day in input.get("days").items) {
        shape(day, ["day_index", "date", "start_min", "end_min", "start_anchor_id", "end_anchor_id"])
        let index = integer(day.get("day_index"), 0, 2); require(!dayIds.contains(index)); dayIds.add(index)
        dateValid(day.get("date"))
        require(integer(day.get("end_min"), 0, 1439) > integer(day.get("start_min"), 0, 1439))
        if (day.get("start_anchor_id").kind != 0) { endpoints.add(ident(day.get("start_anchor_id"))) }
        if (day.get("end_anchor_id").kind != 0) { endpoints.add(ident(day.get("end_anchor_id"))) }
    }
    let taskIds = HashSet<String>()
    for (task in input.get("source_catalog").items) {
        shape(task, ["task_id", "original_day_index", "original_start_min", "duration_min", "priority", "backlog", "protected", "allowed_day_indexes", "opening_state", "windows", "cost_ids"])
        let id = ident(task.get("task_id")); require(!taskIds.contains(id) && !endpoints.contains(id)); taskIds.add(id); endpoints.add(id)
        integer(task.get("original_day_index"), -1, 2); integer(task.get("original_start_min"), 0, 1439); integer(task.get("duration_min"), 1, 1439)
        enumeration(task.get("priority"), ["must", "want", "optional"]); boolean(task.get("backlog")); boolean(task.get("protected"))
        enumeration(task.get("opening_state"), ["verified", "user_confirmed", "unknown"])
        array(task.get("allowed_day_indexes"), 3); let allowedDays = HashSet<Int64>()
        for (day in task.get("allowed_day_indexes").items) { let index = integer(day, 0, 2); require(dayIds.contains(index) && !allowedDays.contains(index)); allowedDays.add(index) }
        array(task.get("windows"), 12)
        for (window in task.get("windows").items) {
            shape(window, ["day_index", "start_min", "end_min", "last_entry_min"])
            require(dayIds.contains(integer(window.get("day_index"), 0, 2)))
            let start = integer(window.get("start_min"), 0, 1439); let end = integer(window.get("end_min"), 0, 1439); require(end > start)
            if (window.get("last_entry_min").kind != 0) { integer(window.get("last_entry_min"), start, end) }
        }
        idsValid(task.get("cost_ids"), 60)
    }
    let baselineIds = HashSet<String>()
    for (item in input.get("baseline").items) {
        assignmentValid(item); let id = item.get("task_id").text
        require(taskIds.contains(id) && !baselineIds.contains(id) && dayIds.contains(item.get("day_index").number)); baselineIds.add(id)
    }
    for (task in input.get("source_catalog").items) { require(!task.get("protected").flag || !active(input, task.get("task_id").text) || baselineIds.contains(task.get("task_id").text)) }
    if (op == "repair") { require(input.get("candidate").kind == 0) } else { array(input.get("candidate"), 24); for (item in input.get("candidate").items) { assignmentValid(item) } }
    array(input.get("cost_catalog"), 100); let costIds = HashSet<String>(); var known: Int64 = 0
    for (cost in input.get("cost_catalog").items) {
        shape(cost, ["cost_id", "task_id", "remaining_cents", "charge_on_omit", "estimated"])
        let id = ident(cost.get("cost_id")); require(!costIds.contains(id)); costIds.add(id)
        if (cost.get("task_id").kind != 0) { require(taskIds.contains(ident(cost.get("task_id")))) }
        if (cost.get("remaining_cents").kind != 0) { known += integer(cost.get("remaining_cents"), 0, 100000000000000); require(known <= 10000000000000000) }
        boolean(cost.get("charge_on_omit")); boolean(cost.get("estimated"))
        if (cost.get("task_id").kind != 0) { require(includes(findTask(input, cost.get("task_id").text).get("cost_ids"), id)) }
    }
    for (task in input.get("source_catalog").items) { for (id in task.get("cost_ids").items) {
        require(costIds.contains(id.text))
        for (cost in input.get("cost_catalog").items) { if (cost.get("cost_id").text == id.text) { require(cost.get("task_id").kind == 1 && cost.get("task_id").text == task.get("task_id").text) } }
    } }
    array(input.get("routes"), 250); let routeIds = HashSet<String>(); let edges = HashSet<String>()
    for (route in input.get("routes").items) {
        shape(route, ["route_id", "day_index", "from_id", "to_id", "mode", "duration_s", "cost_cents", "time_state", "cost_state", "evidence_id"])
        let id = wideIdent(route.get("route_id")); require(!routeIds.contains(id)); routeIds.add(id)
        let index = integer(route.get("day_index"), 0, 2); require(dayIds.contains(index))
        let from = ident(route.get("from_id")); let to = ident(route.get("to_id")); require(endpoints.contains(from) && endpoints.contains(to) && from != to)
        let key = numberNode(index).render() + jsonQuote(from) + jsonQuote(to); require(!edges.contains(key)); edges.add(key)
        enumeration(route.get("mode"), ["walking", "taxi", "driving", "transit", "bicycling"])
        enumeration(route.get("time_state"), ["verified", "estimated", "unknown"]); enumeration(route.get("cost_state"), ["verified", "estimated", "unknown"])
        if (route.get("duration_s").kind != 0) { integer(route.get("duration_s"), 0, 864000) }
        if (route.get("cost_cents").kind != 0) { integer(route.get("cost_cents"), 0, 100000000000000) }
        text(route.get("evidence_id"))
        require(route.get("time_state").text != "verified" || (route.get("duration_s").kind == 3 && route.get("evidence_id").text != ""))
        require(route.get("cost_state").text != "verified" || (route.get("cost_cents").kind == 3 && route.get("evidence_id").text != ""))
    }
    shape(input.get("limits"), ["max_states", "max_search_ms"])
    integer(input.get("limits").get("max_states"), 1, 200); integer(input.get("limits").get("max_search_ms"), 1, 2000)
}

func routeFor(input: Json, index: Int64, from: String, to: String): Json {
    for (route in input.get("routes").items) { if (route.get("day_index").number == index && route.get("from_id").text == from && route.get("to_id").text == to) { return route } }; return Json(0)
}
func routeMinutes(route: Json): Int64 { return if (route.kind != 0 && route.get("duration_s").kind == 3) { (route.get("duration_s").number + 59) / 60 } else { 0 } }
func dayItems(assignments: Json, index: Int64): Json {
    let result = Json(4); for (item in assignments.items) { if (item.get("day_index").number == index) { result.items.add(item) } }; return result
}
func check(id: String, kind: String, state: String, task: String, day: Int64, message: String): Json {
    let result = Json(5); result.put("constraint_id", stringNode(id)); result.put("kind", stringNode(kind)); result.put("state", stringNode(state))
    result.put("task_id", if (task == "") { Json(0) } else { stringNode(task) }); result.put("day_index", if (day < 0) { Json(0) } else { numberNode(day) })
    result.put("message", stringNode(message)); result.put("evidence_ids", Json(4)); return result
}
func state(okay: Bool): String { return if (okay) { "satisfied" } else { "violated" } }
func same(left: Json, right: Json): Bool {
    return left.kind != 0 && right.kind != 0 && left.get("day_index").number == right.get("day_index").number && left.get("start_min").number == right.get("start_min").number && left.get("end_min").number == right.get("end_min").number
}
func windowFits(task: Json, item: Json): Bool {
    for (window in task.get("windows").items) {
        if (window.get("day_index").number == item.get("day_index").number && item.get("start_min").number >= window.get("start_min").number && item.get("end_min").number <= window.get("end_min").number && (window.get("last_entry_min").kind == 0 || item.get("start_min").number <= window.get("last_entry_min").number)) { return true }
    }; return false
}
func evaluate(input: Json, assignments: Json): Json {
    let checks = Json(4); let usedRoutes = Json(4); let charged = Json(4); let unknownCosts = Json(4)
    let seen = HashSet<String>(); var total: Int64 = 0
    for (item in assignments.items) {
        let id = item.get("task_id").text; let dayIndex = item.get("day_index").number; let task = findTask(input, id); let day = findDay(input, dayIndex)
        let unique = !seen.contains(id); seen.add(id)
        checks.items.add(check("identity:" + id, "identity", state(unique && task.kind != 0 && active(input, id)), id, dayIndex, "只允许目录内未完成且未取消的唯一活动"))
        if (task.kind == 0 || day.kind == 0) {
            checks.items.add(check("day:" + id, "allowed_day", "violated", id, dayIndex, "活动或日期不在授权目录")); continue
        }
        let start = item.get("start_min").number; let end = item.get("end_min").number
        let validDay = allowed(task, dayIndex) && (task.get("backlog").flag || task.get("original_day_index").number == dayIndex)
        checks.items.add(check("allowed:" + id, "allowed_day", state(validDay), id, dayIndex, "只有授权漏游可以迁入允许的未来日期"))
        checks.items.add(check("duration:" + id, "duration", state(end - start == task.get("duration_min").number), id, dayIndex, "保持目录中的真实停留时长"))
        checks.items.add(check("bounds:" + id, "day_window", state(start >= day.get("start_min").number && end <= day.get("end_min").number), id, dayIndex, "活动须在用户日界内"))
        if (task.get("opening_state").text == "unknown") { checks.items.add(check("opening:" + id, "opening_hours", "unknown", id, dayIndex, "营业与预约未核实，不能当作全天已开放")) }
        else {
            checks.items.add(check("opening:" + id, "opening_hours", state(windowFits(task, item)), id, dayIndex, "停留与入场时刻须满足已给定的开放区间"))
            if (task.get("opening_state").text == "user_confirmed") { checks.items.add(check("opening_source:" + id, "opening_source", "unknown", id, dayIndex, "按用户给定时窗规划，尚非运营方实时核验")) }
        }
    }
    for (task in input.get("source_catalog").items) {
        let id = task.get("task_id").text; let item = findAssignment(assignments, id)
        if (!active(input, id)) { continue }
        if (task.get("priority").text == "must") { checks.items.add(check("must:" + id, "must_placed", state(item.kind != 0), id, -1, "每个必去活动必须安置，否则不得成功")) }
        if (task.get("protected").flag) { checks.items.add(check("protected:" + id, "booking_protection", state(same(findAssignment(input.get("baseline"), id), item)), id, -1, "未明确修改的已接受预订日期和时间保持")) }
    }
    for (day in input.get("days").items) {
        let index = day.get("day_index").number; let items = dayItems(assignments, index).items
        if (day.get("start_anchor_id").kind == 0 || day.get("end_anchor_id").kind == 0) { checks.items.add(check("boundary:" + index.toString(), "boundary_location", "unknown", "", index, "日首或日末边界地点未确认，不制造酒店或零耗时事实")) }
        checks.items.add(check("capacity:" + index.toString(), "daily_capacity", state(items.size <= 8), "", index, "每天最多八项市内活动，边界锚点不占槽位"))
        for (i in 0..items.size) {
            for (j in (i + 1)..items.size) {
                checks.items.add(check("overlap:" + items[i].get("task_id").text + ":" + items[j].get("task_id").text, "time_overlap", state(items[i].get("start_min").number >= items[j].get("end_min").number || items[j].get("start_min").number >= items[i].get("end_min").number), items[j].get("task_id").text, index, "同日全部活动区间两两检查"))
            }
        }
        var from = day.get("start_anchor_id").text; var end = day.get("start_min").number
        for (i in 0..(items.size + 1)) {
            let finalEdge = i == items.size
            let to = if (finalEdge) { day.get("end_anchor_id").text } else { items[i].get("task_id").text }
            let start = if (finalEdge) { day.get("end_min").number } else { items[i].get("start_min").number }
            checks.items.add(check("order:" + index.toString() + ":" + i.toString(), "time_order", state(start >= end), if (finalEdge) { "" } else { to }, index, "包括日首末边界的时间顺序不得倒置"))
            if (from != to) {
                let route = routeFor(input, index, from, to)
                let known = route.kind != 0 && route.get("duration_s").kind == 3 && route.get("time_state").text != "unknown"
                let item = check("route:" + index.toString() + ":" + from + ":" + to, "transfer_time", if (known) { state(start - end >= routeMinutes(route)) } else { "unknown" }, if (finalEdge) { "" } else { to }, index, "日首末和活动间接驳均使用冻结道路信息；缺项待核实")
                if (route.kind != 0) {
                    pushId(usedRoutes, route.get("route_id").text)
                    if (route.get("evidence_id").text != "") { item.get("evidence_ids").items.add(route.get("evidence_id").copy()) }
                    if (known && route.get("time_state").text == "estimated") { checks.items.add(check("route_estimate:" + route.get("route_id").text, "route_estimate", "unknown", "", index, "交通时长是估算，不能保证未来交通")) }
                    if (route.get("cost_cents").kind == 3 && route.get("cost_state").text != "unknown") { total += route.get("cost_cents").number }
                    else { pushId(unknownCosts, "route:" + route.get("route_id").text); checks.items.add(check("route_cost:" + route.get("route_id").text, "cost_unknown", "unknown", "", index, "当前路段没有可用费用，不按零元通过")) }
                    if (route.get("cost_state").text == "estimated") { checks.items.add(check("route_price_estimate:" + route.get("route_id").text, "estimated_price", "unknown", "", index, "路段费用为估算，不是成交报价")) }
                } else { pushId(unknownCosts, "route:" + index.toString() + ":" + from + ":" + to); checks.items.add(check("missing_route_cost:" + from + ":" + to, "cost_unknown", "unknown", "", index, "缺少路段方式与费用资料，不能视为免费")) }
                checks.items.add(item)
            }
            if (!finalEdge) { end = items[i].get("end_min").number; from = to }
        }
    }
    for (cost in input.get("cost_catalog").items) {
        if (cost.get("task_id").kind == 0 || cost.get("charge_on_omit").flag || findAssignment(assignments, cost.get("task_id").text).kind != 0) {
            let id = cost.get("cost_id").text; pushId(charged, id)
            if (cost.get("remaining_cents").kind == 3) { total += cost.get("remaining_cents").number }
            else { pushId(unknownCosts, id); checks.items.add(check("cost:" + id, "cost_unknown", "unknown", "", -1, "未来应付金额未知，保留未定费用")) }
            if (cost.get("estimated").flag) { checks.items.add(check("estimate:" + id, "estimated_price", "unknown", "", -1, "费用仍为估算，不是实付或实时成交价")) }
        }
    }
    require(total <= 10000000000000000)
    checks.items.add(check("budget", "remaining_budget", state(total <= input.get("budget_cents").number), "", -1, "内核只比较此账目快照的未来应付；外层仍须复核整趟总账"))
    let result = Json(5); result.put("checks", checks); result.put("used_route_ids", usedRoutes); result.put("charged_cost_ids", charged); result.put("unknown_cost_ids", unknownCosts); result.put("total_cents", numberNode(total)); return result
}

func absNumber(value: Int64): Int64 { return if (value < 0) { -value } else { value } }
func unplaced(input: Json, assignments: Json): Json {
    let result = Json(4)
    for (task in input.get("source_catalog").items) {
        let id = task.get("task_id").text
        if (active(input, id) && findAssignment(assignments, id).kind == 0) {
            let entry = Json(5); entry.put("task_id", stringNode(id)); entry.put("priority", task.get("priority").copy())
            let reason = if (task.get("allowed_day_indexes").items.size == 0) { "no_remaining_day" } else if (task.get("opening_state").text != "unknown" && task.get("windows").items.size == 0) { "no_open_window" } else if (task.get("priority").text == "must") { "must_not_placed" } else { "not_selected_in_bounded_search" }
            entry.put("reason_code", stringNode(reason)); entry.put("message", stringNode("未在本次限额与条件中安置；仍保留在任务池，不等于已取消或证明全局无解")); result.items.add(entry)
        }
    }; return result
}
func changes(input: Json, assignments: Json): Json {
    let result = Json(4)
    for (task in input.get("source_catalog").items) {
        let id = task.get("task_id").text; if (!active(input, id)) { continue }
        let before = findAssignment(input.get("baseline"), id); let after = findAssignment(assignments, id)
        let originalDay = if (before.kind == 0) { task.get("original_day_index").number } else { before.get("day_index").number }
        let originalStart = if (before.kind == 0) { task.get("original_start_min").number } else { before.get("start_min").number }
        let fields = Json(4)
        if (after.kind == 0) { fields.items.add(stringNode("unplaced")) }
        else {
            if (after.get("day_index").number != originalDay) { fields.items.add(stringNode("day")) }
            if (after.get("start_min").number != originalStart) { fields.items.add(stringNode("time")) }
            if (before.kind == 0) { fields.items.add(stringNode("placed_from_catalog")) }
        }
        if (fields.items.size > 0) {
            let item = Json(5); item.put("task_id", stringNode(id)); item.put("fields", fields)
            item.put("from_day", numberNode(originalDay)); item.put("to_day", if (after.kind == 0) { Json(0) } else { after.get("day_index").copy() })
            item.put("from_start", numberNode(originalStart)); item.put("to_start", if (after.kind == 0) { Json(0) } else { after.get("start_min").copy() }); result.items.add(item)
        }
    }; return result
}
func score(input: Json, assignments: Json, report: Json): Json {
    var mustMissing: Int64 = 0; var wantMissing: Int64 = 0; var optionalMissing: Int64 = 0
    var disturbed: Int64 = 0; var shift: Int64 = 0; var unknown: Int64 = 0; var seconds: Int64 = 0
    for (task in input.get("source_catalog").items) {
        let id = task.get("task_id").text; if (!active(input, id)) { continue }
        let item = findAssignment(assignments, id)
        if (item.kind == 0) { if (task.get("priority").text == "must") { mustMissing++ } else if (task.get("priority").text == "want") { wantMissing++ } else { optionalMissing++ } }
        let original = findAssignment(input.get("baseline"), id)
        if (original.kind != 0 && !same(original, item)) {
            disturbed++
            if (item.kind != 0) { shift += absNumber(item.get("day_index").number * 1440 + item.get("start_min").number - original.get("day_index").number * 1440 - original.get("start_min").number) }
        }
    }
    for (item in report.get("checks").items) { if (item.get("kind").text == "transfer_time" && item.get("state").text == "unknown") { unknown++ } }
    for (id in report.get("used_route_ids").items) { for (route in input.get("routes").items) { if (route.get("route_id").text == id.text && route.get("duration_s").kind == 3) { seconds += route.get("duration_s").number } } }
    let result = Json(4)
    for (value in [mustMissing, wantMissing, disturbed, optionalMissing, unknown, shift, seconds, report.get("total_cents").number]) { result.items.add(numberNode(value)) }; return result
}

func lessScore(left: Json, right: Json): Bool {
    for (i in 0..left.items.size) { if (left.items[i].number != right.items[i].number) { return left.items[i].number < right.items[i].number } }; return false
}
func hardErrors(report: Json, allowMissing: Bool): Int64 {
    var count: Int64 = 0
    for (item in report.get("checks").items) { if (item.get("state").text == "violated" && (!allowMissing || item.get("kind").text != "must_placed")) { count++ } }; return count
}
func materialize(input: Json, order: Json, preserve: Bool): Json {
    let result = Json(4)
    for (day in input.get("days").items) {
        let index = day.get("day_index").number; let items = dayItems(order, index).items
        if (items.size > 8) { return Json(0) }
        var previous = day.get("start_anchor_id").text; var earliest = day.get("start_min").number
        for (item in items) {
            let id = item.get("task_id").text; let task = findTask(input, id)
            if (task.kind == 0 || !active(input, id) || !allowed(task, index) || (!task.get("backlog").flag && task.get("original_day_index").number != index)) { return Json(0) }
            let route = routeFor(input, index, previous, id)
            var start = earliest + routeMinutes(route)
            let old = findAssignment(input.get("baseline"), id)
            if (preserve && old.kind != 0 && start < old.get("start_min").number) { start = old.get("start_min").number }
            if (task.get("protected").flag) {
                if (old.kind == 0 || old.get("day_index").number != index || old.get("start_min").number < start) { return Json(0) }
                start = old.get("start_min").number
            }
            let duration = task.get("duration_min").number
            if (task.get("opening_state").text != "unknown") {
                var found = false; var bestStart: Int64 = 100000
                for (window in task.get("windows").items) {
                    if (window.get("day_index").number != index) { continue }
                    let choice = if (start > window.get("start_min").number) { start } else { window.get("start_min").number }
                    if (choice + duration <= window.get("end_min").number && (window.get("last_entry_min").kind == 0 || choice <= window.get("last_entry_min").number) && (!task.get("protected").flag || choice == start) && choice < bestStart) { found = true; bestStart = choice }
                }
                if (!found) { return Json(0) }; start = bestStart
            }
            if (start + duration > day.get("end_min").number || start < 0) { return Json(0) }
            result.items.add(assignment(id, index, start, start + duration)); earliest = start + duration; previous = id
        }
        if (earliest + routeMinutes(routeFor(input, index, previous, day.get("end_anchor_id").text)) > day.get("end_min").number) { return Json(0) }
    }; return result
}
func filteredBaseline(input: Json): Json {
    let result = Json(4)
    for (day in input.get("days").items) { for (item in input.get("baseline").items) {
        if (item.get("day_index").number == day.get("day_index").number && active(input, item.get("task_id").text)) { result.items.add(item.copy()) }
    } }; return result
}
func without(assignments: Json, one: String, two: String): Json {
    let result = Json(4)
    for (item in assignments.items) { if (item.get("task_id").text != one && item.get("task_id").text != two) { result.items.add(item.copy()) } }; return result
}
func inserted(input: Json, assignments: Json, id: String, index: Int64, position: Int64): Json {
    let result = Json(4)
    for (day in input.get("days").items) {
        let currentDay = day.get("day_index").number; let items = dayItems(assignments, currentDay).items
        for (i in 0..(items.size + 1)) {
            if (currentDay == index && i == position) { result.items.add(assignment(id, index, day.get("start_min").number, day.get("start_min").number + findTask(input, id).get("duration_min").number)) }
            if (i < items.size) { result.items.add(items[i].copy()) }
        }
    }; return result
}

class Search {
    let input: Json
    let started = MonoTime.now()
    let seen = HashSet<String>()
    public var expanded: Int64 = 0
    public var evaluated: Int64 = 0
    public var termination = "completed"
    public var best = Json(0)
    public var partial = Json(0)
    public init(input: Json) { this.input = input }
    public func stopped(): Bool {
        if (termination != "completed") { return true }
        if (expanded >= input.get("limits").get("max_states").number) { termination = "state_limit"; return true }
        if (expanded > 0 && MonoTime.now() - started >= input.get("limits").get("max_search_ms").number * Duration.millisecond) { termination = "time_limit"; return true }; return false
    }
    public func attempt(order: Json, policy: Int64): Json {
        if (stopped()) { return Json(0) }; expanded++
        let candidate = if (policy < 0) { order.copy() } else { materialize(input, order, policy == 1) }
        if (candidate.kind == 0 || seen.contains(candidate.render())) { return Json(0) }; seen.add(candidate.render()); evaluated++
        let report = evaluate(input, candidate); let objective = score(input, candidate, report)
        let node = Json(5); node.put("assignments", candidate); node.put("report", report); node.put("objective", objective)
        if (partial.kind == 0 || hardErrors(report, true) < hardErrors(partial.get("report"), true) || (hardErrors(report, true) == hardErrors(partial.get("report"), true) && lessScore(objective, partial.get("objective")))) { partial = node }
        if (hardErrors(report, false) == 0 && (best.kind == 0 || lessScore(objective, best.get("objective")) || (objective.render() == best.get("objective").render() && candidate.render() < best.get("assignments").render()))) { best = node }
        return node
    }
}
func addBeam(beam: Json, node: Json): Unit {
    if (node.kind == 0 || hardErrors(node.get("report"), true) > 0) { return }
    for (old in beam.items) { if (old.get("assignments").render() == node.get("assignments").render()) { return } }
    beam.items.add(node)
}
func trimBeam(beam: Json): Json {
    let selected = Json(4); let remaining = beam.items
    let used = HashSet<Int64>()
    for (_ in 0..3) {
        var bestIndex: Int64 = -1
        for (i in 0..remaining.size) {
            if (used.contains(i)) { continue }
            if (bestIndex < 0 || lessScore(remaining[i].get("objective"), remaining[bestIndex].get("objective")) || (remaining[i].get("objective").render() == remaining[bestIndex].get("objective").render() && remaining[i].get("assignments").render() < remaining[bestIndex].get("assignments").render())) { bestIndex = i }
        }
        if (bestIndex < 0) { break }; used.add(bestIndex); selected.items.add(remaining[bestIndex])
    }; return selected
}
func result(input: Json, outcome: String, node: Json, approved: Bool, expanded: Int64, evaluated: Int64, termination: String): Json {
    let output = Json(5); output.put("schema_version", stringNode("2.2")); output.put("request_id", input.get("request_id").copy()); output.put("op", input.get("op").copy()); output.put("engine", stringNode("cangjie-journey-planner")); output.put("outcome", stringNode(outcome))
    let report = node.get("report"); let assignments = node.get("assignments")
    output.put("assignments", if (approved) { assignments } else { Json(0) })
    for (field in ["used_route_ids", "charged_cost_ids", "total_cents", "unknown_cost_ids", "checks"]) { output.put(field, report.get(field).copy()) }
    output.put("unplaced", unplaced(input, assignments)); output.put("changes", changes(input, assignments)); output.put("objective", node.get("objective").copy())
    let search = Json(5); search.put("expanded_states", numberNode(expanded)); search.put("evaluated_candidates", numberNode(evaluated)); search.put("termination", stringNode(termination)); output.put("search", search); return output
}
func run(input: Json): Json {
    if (input.get("op").text == "evaluate") {
        let assignments = input.get("candidate"); let report = evaluate(input, assignments); let node = Json(5)
        node.put("assignments", assignments); node.put("report", report); node.put("objective", score(input, assignments, report))
        var unknown = false; for (item in report.get("checks").items) { if (item.get("state").text == "unknown") { unknown = true } }
        return result(input, if (hardErrors(report, false) > 0) { "conflict" } else if (unknown) { "provisional" } else { "feasible" }, node, true, 1, 1, "completed")
    }
    let search = Search(input); let base = filteredBaseline(input); var beam = Json(4)
    addBeam(beam, search.attempt(base, -1)); addBeam(beam, search.attempt(base, 1)); addBeam(beam, search.attempt(base, 0))
    let removable = Json(4)
    for (item in base.items) {
        let task = findTask(input, item.get("task_id").text)
        if (!task.get("protected").flag && task.get("priority").text != "must") { removable.items.add(item.get("task_id").copy()) }
    }
    var initial: Int64 = 0
    for (id in removable.items) {
        if (initial >= 8 || search.stopped()) { break }; initial++
        let trial = without(base, id.text, ""); addBeam(beam, search.attempt(trial, 1)); addBeam(beam, search.attempt(trial, 0))
    }
    for (day in input.get("days").items) {
        let items = dayItems(base, day.get("day_index").number).items
        for (i in 1..items.size) {
            if (initial >= 12 || search.stopped()) { break }; initial++
            if (findTask(input, items[i - 1].get("task_id").text).get("protected").flag || findTask(input, items[i].get("task_id").text).get("protected").flag) { continue }
            let trial = base.copy(); var one: Int64 = -1; var two: Int64 = -1
            for (j in 0..trial.items.size) { if (trial.items[j].get("task_id").text == items[i - 1].get("task_id").text) { one = j }; if (trial.items[j].get("task_id").text == items[i].get("task_id").text) { two = j } }
            let temp = trial.items[one]; trial.items[one] = trial.items[two]; trial.items[two] = temp
            addBeam(beam, search.attempt(trial, 0))
        }
    }
    beam = trimBeam(beam)
    for (priority in ["must", "want", "optional"]) {
        for (task in input.get("source_catalog").items) {
            let id = task.get("task_id").text
            if (!active(input, id) || task.get("priority").text != priority || search.stopped()) { continue }
            let next = Json(4)
            for (node in beam.items) {
                let candidate = node.get("assignments")
                if (findAssignment(candidate, id).kind != 0) { addBeam(next, node); continue }
                if (priority != "must") { addBeam(next, node) }
                var retained: Int64 = 0
                for (day in input.get("days").items) {
                    let index = day.get("day_index").number
                    if (!allowed(task, index) || (!task.get("backlog").flag && task.get("original_day_index").number != index)) { continue }
                    let count = dayItems(candidate, index).items.size
                    if (count >= 8) { continue }
                    // End placement first preserves accepted future times; other positions remain bounded candidates.
                    for (offset in 0..(count + 1)) {
                        if (retained >= 3 || search.stopped()) { break }
                        let position = if (offset == 0) { count } else { offset - 1 }
                        let order = inserted(input, candidate, id, index, position)
                        let proposed = search.attempt(order, 1)
                        if (proposed.kind != 0 && hardErrors(proposed.get("report"), true) == 0) { addBeam(next, proposed); retained++ }
                        if (retained < 3 && !search.stopped()) {
                            let early = search.attempt(order, 0)
                            if (early.kind != 0 && hardErrors(early.get("report"), true) == 0) { addBeam(next, early); retained++ }
                        }
                    }
                }
            }
            if (next.items.size > 0) { beam = trimBeam(next) }
        }
    }
    let chosen = if (search.best.kind != 0) { search.best } else { search.partial }
    require(chosen.kind != 0)
    if (search.best.kind == 0) { return result(input, "no_solution", chosen, false, search.expanded, search.evaluated, search.termination) }
    var unknown = false; for (item in chosen.get("report").get("checks").items) { if (item.get("state").text == "unknown") { unknown = true } }
    var activeCount: Int64 = 0; for (task in input.get("source_catalog").items) { if (active(input, task.get("task_id").text)) { activeCount++ } }
    let outcome = if (activeCount == 0 && input.get("days").items.size == 0) { "completed" } else if (unknown) { "provisional" } else if (chosen.get("assignments").render() == base.render()) { "unchanged" } else { "feasible" }
    return result(input, outcome, chosen, true, search.expanded, search.evaluated, search.termination)
}
func boundedInput(): String {
    let bytes = ArrayList<UInt8>(); let chunk = Array<UInt8>(8192, repeat: 0)
    while (true) { let count = getStdIn().read(chunk); if (count == 0) { break }; require(bytes.size + count <= 1000000); for (i in 0..count) { bytes.add(chunk[i]) } }; return String.fromUtf8(bytes.toArray())
}
main(): Int64 {
    try {
        let input = Parser(boundedInput()).parse(); inputValid(input)
        let output = run(input).render(); require(output.size <= 1000000); println(output); return 0
    } catch (_: Exception) { getStdErr().writeln("Invalid journey planner input or runtime failure."); return 1 }
}


