// Travel Assistant deterministic validator. Mulan PSL v2.
// A bounded JSON codec for the internal integer/UTF-8 contract; no stdx dependency.
import std.collection.*
import std.env.*

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
        keys.add(key)
        items.add(value)
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
        while (position < bytes.size && bytes[position] >= 48 && bytes[position] <= 57) {
            result.number = result.number * 10 + Int64(bytes[position]) - 48
            position++; count++
            if (count > 18) { throw Exception("integer too large") }
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
func issue(code: String, message: String, day: Int64, stop: String): Json {
    let result = Json(5)
    result.put("code", stringNode(code)); result.put("message", stringNode(message))
    result.put("day_index", if (day < 0) { Json(0) } else { numberNode(day) })
    result.put("stop_id", if (stop == "") { Json(0) } else { stringNode(stop) })
    return result
}

func validate(input: Json): Json {
    let errors = input.get("pre_errors")
    let warnings = input.get("pre_warnings")
    let unknowns = input.get("unknowns")
    if (input.get("total_cents").number > input.get("budget_cents").number) {
        errors.items.add(issue("over_budget", "估算总费用超过全员预算", -1, ""))
    }
    let seen = HashSet<String>()
    let days = input.get("days").items
    let legs = input.get("legs").items
    for (dayIndex in 0..days.size) {
        let stops = days[dayIndex].get("stops").items
        for (i in 0..stops.size) {
            let stop = stops[i]
            let id = stop.get("id").text
            if (stop.get("end_min").number <= stop.get("start_min").number) {
                errors.items.add(issue("invalid_duration", "活动结束时间必须晚于开始时间", dayIndex, id))
            }
            if (i > 0 && stop.get("start_min").number < stops[i - 1].get("end_min").number) {
                errors.items.add(issue("time_overlap", "与上一个活动的时间重叠", dayIndex, id))
            }
            let identity = stop.get("identity").text
            if (stop.get("check_duplicate").flag) {
                if (seen.contains(identity)) {
                    warnings.items.add(issue("duplicate_stop", "行程中出现重复地点，请确认是否有意安排", dayIndex, id))
                }
                seen.add(identity)
            }
            if (stop.get("needs_poi").flag && !stop.get("poi_ok").flag) {
                unknowns.items.add(issue("missing_location", "地点未定位，路线和通行时间无法验证", dayIndex, id))
            }
        }
        if (stops.size < 2) { continue }
        for (i in 0..(stops.size - 1)) {
            let before = stops[i]; let after = stops[i + 1]
            var found = false
            for (leg in legs) {
                if (leg.get("day_index").number == dayIndex && leg.get("from_id").text == before.get("id").text && leg.get("to_id").text == after.get("id").text) {
                    found = true
                    if (!leg.get("available").flag) {
                        unknowns.items.add(issue("missing_route", "相邻地点没有可用的真实路线", dayIndex, after.get("id").text))
                    } else if (after.get("start_min").number - before.get("end_min").number < leg.get("duration_min").number) {
                        errors.items.add(issue("transfer_time", "活动之间预留的时间不足以完成实际路线", dayIndex, after.get("id").text))
                    }
                    break
                }
            }
            if (!found) { unknowns.items.add(issue("missing_route", "相邻地点没有可用的真实路线", dayIndex, after.get("id").text)) }
        }
    }
    let status = if (errors.items.size > 0 || warnings.items.size > 0) { "issues" }
        else if (unknowns.items.size > 0) { "passed_with_unknowns" } else { "passed" }
    let output = Json(5)
    output.put("status", stringNode(status)); output.put("errors", errors)
    output.put("warnings", warnings); output.put("unknowns", unknowns)
    output.put("total_cents", input.get("total_cents")); output.put("budget_cents", input.get("budget_cents"))
    return output
}

main(): Int64 {
    try {
        let input = getStdIn().readToEnd().getOrThrow()
        println(validate(Parser(input).parse()).render())
        return 0
    } catch (_: Exception) {
        getStdErr().writeln("Invalid validator input or runtime failure.")
        return 1
    }
}
