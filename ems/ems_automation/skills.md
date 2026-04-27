# EMS Automation Skills

适用于当前项目 `D:\!!!5GC测试\5gc-script\venv\ems_tester\ems_automation` 的可复用提示词模板。

## 使用原则

- 默认项目入口是 `py -m ems_automation.cli`
- 配置类操作优先区分三种语义：
  - `config-read`: 读取页面字段和值
  - `config-query`: 执行 `SHOW ...` 并提取结果
  - `config-write`: 填写并下发配置
- 告警类操作优先区分三种语义：
  - `alarm-read`: 读取告警总览、活动告警、历史告警
  - `alarm-rule-read`: 读取告警规则列表
  - `alarm-rule-write`: 新增告警规则
- 遇到失败时，优先查看项目下的调试目录：
  - `debug_config_read`
  - `debug_config_query`
  - `debug_config_write`
  - `debug_alarm_open`
  - `alarm_rule_debug`

## Skill 1: 导出配置树

### 提示词

```text
帮我导出 EMS 指定网元的配置树，并保留每个叶子节点的字段信息。使用 ems_automation 项目现有 CLI，不要重写脚本。网元 IP 是：{ne_ip}，输出文件是：{output_yaml}
```

### 预期动作

```powershell
py -m ems_automation.cli config-tree --ne {ne_ip} --output {output_yaml}
```

### 适用场景

- 不清楚配置项在树里的准确路径
- 想确认某个叶子节点有哪些字段

## Skill 2: 读取配置页面字段

### 提示词

```text
帮我读取 EMS 某个配置页面当前显示的字段和值。使用 config-read，不要执行下发。任务文件路径是：{task_json}
```

### 任务文件模板

```json
{
  "network_element": "10.230.4.248",
  "path_by_name": [
    "业务开通配置",
    "VRF配置",
    "增加VRF配置"
  ]
}
```

### 预期动作

```powershell
py -m ems_automation.cli config-read --task {task_json}
```

### 适用场景

- 想知道页面上有哪些字段
- 想判断新增/修改配置页面有哪些必填项

## Skill 3: 查询配置数据

### 提示词

```text
帮我查询 EMS 某个 SHOW 类配置项的实际返回数据。使用 config-query，进入指定树节点后执行命令，并从文本结果区提取结构化结果。任务文件路径是：{task_json}
```

### 任务文件模板

```json
{
  "network_element": "10.230.4.248",
  "path_by_name": [
    "网络基础配置",
    "接口配置",
    "子接口配置",
    "查询子接口配置"
  ],
  "params": {},
  "command_text": "SHOW UPFSUBINTERFACECONF;",
  "result_wait_ms": 12000
}
```

### 预期动作

```powershell
py -m ems_automation.cli config-query --task {task_json}
```

### 结果重点

- `result_text`: 文本结果区原始查询结果
- `headers`: 表头
- `rows`: 结构化后的数据行

## Skill 4: 下发配置

### 提示词

```text
帮我在 EMS 上填写并下发某个配置。使用 config-write，不要重写旧脚本。先按任务文件填写字段，再执行提交，并返回页面尾部文本和调试信息。任务文件路径是：{task_json}
```

### 任务文件模板

```json
{
  "network_element": "10.230.4.248",
  "path_by_name": [
    "业务开通配置",
    "VRF配置",
    "增加VRF配置"
  ],
  "params": {
    "VRF ID": "888"
  },
  "submit": true
}
```

### 预期动作

```powershell
py -m ems_automation.cli config-write --task {task_json}
```

### 关键约束

- 不写 `"submit": true` 就只会填值，不会真正执行
- 如果失败，优先检查 `debug_config_write`

## Skill 5: 读取告警数据

### 提示词

```text
帮我导出 EMS 告警数据。使用 alarm-read，输出总览、活动告警、历史告警，并把结果写入指定目录。输出目录是：{output_dir}
```

### 预期动作

```powershell
py -m ems_automation.cli alarm-read --target all --output-dir {output_dir}
```

### 结果文件

- `alarm_export_summary.json`
- `alarm_overview.json`
- `activity_alarm.json`
- `history_alarm.json`
- 对应 CSV 文件

### 常见误判

- CLI 只打印输出路径，不代表没取到数据
- 真实数量应看 `alarm_export_summary.json` 或 CLI 里的 `counts`

## Skill 6: 读取告警规则

### 提示词

```text
帮我读取 EMS 某个告警规则页签下的规则列表。使用 alarm-rule-read，输出 JSON 到指定文件。规则页签名称是：{rule_page}，输出文件是：{output_json}
```

### 预期动作

```powershell
py -m ems_automation.cli alarm-rule-read --rule-page "{rule_page}" --output {output_json}
```

### 适用场景

- 告警过滤规则
- 告警自动确认规则
- 北向告警过滤规则
- 告警重定义规则

## Skill 7: 新增告警规则

### 提示词

```text
帮我在 EMS 上新增一条告警规则。使用 alarm-rule-write，按任务文件填写字段，默认先不提交；如果任务文件里 submit=true，再执行确认。任务文件路径是：{task_json}
```

### 任务文件模板

```json
{
  "rule_page": "告警过滤规则",
  "fields": {
    "规则名称": "demo-rule-from-json",
    "规则类型": "黑名单过滤",
    "描述": "created by ems automation"
  },
  "submit": false
}
```

### 预期动作

```powershell
py -m ems_automation.cli alarm-rule-write --task {task_json}
```

## Skill 8: 排障模式

### 提示词

```text
帮我排查 ems_automation 执行失败原因。不要直接猜测，先读取对应 debug 目录下的 txt/html/png，再结合当前 CLI 代码定位问题，最后给出最小修改方案。
```

### 排障优先级

- 首页打开失败：看 `debug_alarm_open`
- 配置查询失败：看 `debug_config_query`
- 配置写入失败：看 `debug_config_write`
- 告警规则写入失败：看 `alarm_rule_debug`

### 常见问题模式

- 首页是空白壳子，SPA 未挂载
- 菜单能打开，但树路径最后一级名字不完全一致
- 任务文件缺少 `submit: true`
- 查询类场景误用了 `config-read`
- 写入类页面存在额外必填字段
- 告警数据已导出，但 CLI 只打印了路径

## Skill 9: 新需求实现

### 提示词

```text
请基于 ems_automation 现有结构实现新功能，优先复用现有模块，不要再写散装脚本。先判断这个需求应落在 config_ops、alarm_ops、ui、cli 中哪一层，再补齐 README 和 examples。
```

### 实现约束

- 入口统一走 `ems_automation/ems_automation/cli.py`
- 页面交互统一沉淀到 `ui.py`
- 配置相关能力放到 `config_ops.py`
- 告警相关能力放到 `alarm_ops.py`
- 示例任务文件放到 `examples/`

## Skill 10: 结果汇总

### 提示词

```text
帮我把本次 EMS 自动化执行结果整理成简短报告。优先提炼总数、关键网元、关键告警/配置项、失败点和调试文件路径，不要粘贴整页原始文本。
```

### 推荐输出结构

- 执行动作
- 核心结果
- 异常/风险
- 相关文件
