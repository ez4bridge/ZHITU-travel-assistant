# 可选仓颉行程校验模块

本目录集中保存仓颉代码、编译脚本和跨语言固定样例。主体应用是 Python + Vue，仓颉程序不会调用大模型或高德，也不承担 PDF 解析。

职责：预算、活动时间重叠、相邻活动通行时间、重复景点、缺失地点与路线的确定性校验。

接口：标准输入接收一个 UTF-8 JSON 对象；标准输出返回一个 UTF-8 JSON 校验结果；诊断放到标准错误。输入金额为整数分、时间为整数分钟。Python 负责把完整旅行方案规范化，仓颉负责校验规则。

Python 入口：`backend/validation.py`。默认 `VALIDATOR_BACKEND=auto`：只有找到 `bin/validator.exe`，且运行固定样例与 Python 结果一致后才启用。程序缺失、崩溃、超时、JSON 格式错误会回退 Python；普通的行程问题不会触发回退。

开发环境、可执行文件与构建产物保存在本目录的忽略目录，不修改全局仓颉设置。实际编译与测试情况以 `docs/ACCEPTANCE.md` 为准。

全国多城市扩展使用可选 journey 对象检查城市链、跨城覆盖、跨午夜绝对分钟、交通与活动冲突；intercity 边界不要求市内导航。对应 Python 在 backend/journey.py 和 validation.py，协议见 docs/API_CONTRACT.md。旧协议不含 journey 时行为不变；新增源码需重新编译并跑一致性检查，本地未编译时仍用 Python。

journey.stays 可选字段用于期望时长匹配：expected_halves／actual_halves 均为整数半天数，差异产生 stay_preference 提醒。Python 在规范化阶段按访问次序计算活动半天数，原生与 Python 引擎输出相同文案；新增固定样例与随机检查已覆盖。
