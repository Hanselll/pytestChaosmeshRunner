# EMS Automation

将分散的 EMS Playwright 脚本整理成可复用的 Python 项目，当前覆盖：

- 配置树导出
- 配置页面读取
- 配置查询命令执行
- 配置写入
- 告警读取
- 告警规则读取与写入
- 性能页面读取
- DNN 网页配置与数据库配置对比

## 项目结构

- `ems_automation/settings.py`: 站点、Cookie、基础配置
- `ems_automation/browser.py`: Playwright 浏览器会话
- `ems_automation/ui.py`: 通用 UI 操作封装
- `ems_automation/config_ops.py`: 配置树、配置读写、配置查询、DNN 对比
- `ems_automation/alarm_ops.py`: 告警读取与规则操作
- `ems_automation/perf_ops.py`: 性能页面读取与创建类操作
- `ems_automation/cli.py`: 统一命令行入口

## 安装

```powershell
cd D:\!!!5GC测试\5gc-script\venv\ems_tester\ems_automation
..\Scripts\python.exe -m pip install -e .
..\Scripts\playwright.exe install msedge
```

## 常用命令

```powershell
..\Scripts\python.exe -m ems_automation.cli auth-login
..\Scripts\python.exe -m ems_automation.cli ne-list
..\Scripts\python.exe -m ems_automation.cli config-tree --ne 10.230.4.248 --output .\outputs\config_tree_with_fields.yaml
..\Scripts\python.exe -m ems_automation.cli config-read --task .\examples\config_read_task.json
..\Scripts\python.exe -m ems_automation.cli config-query --task .\examples\config_query_task.json
..\Scripts\python.exe -m ems_automation.cli config-write --task .\examples\config_write_task.json
..\Scripts\python.exe -m ems_automation.cli config-dnn-compare --task .\examples\dnn_compare_task.json
..\Scripts\python.exe -m ems_automation.cli config-vrf-compare --task .\examples\vrf_compare_task.json
..\Scripts\python.exe -m ems_automation.cli config-generic-compare --task .\scratch\tmp_generic_compare_dscp16.json
..\Scripts\python.exe -m ems_automation.cli testcase-run --task .\examples\dnn_create_and_compare_testcase.json
..\Scripts\python.exe -m ems_automation.cli testcase-run --task .\examples\vrf_create_and_compare_testcase.json
..\Scripts\python.exe -m ems_automation.cli alarm-read --target all --output-dir .\exports
..\Scripts\python.exe -m ems_automation.cli alarm-rule-read --rule-page "告警过滤规则" --output .\exports\alarm_filter_rules.json
..\Scripts\python.exe -m ems_automation.cli alarm-rule-write --task .\examples\alarm_rule_write_task.json
..\Scripts\python.exe -m ems_automation.cli perf-read --target all --output-dir .\perf_exports
..\Scripts\python.exe -m ems_automation.cli perf-metric-create --task .\examples\perf_metric_create_task.json
..\Scripts\python.exe -m ems_automation.cli perf-metric-query --task .\examples\perf_metric_query_task.json
..\Scripts\python.exe -m ems_automation.cli perf-pagination-check --task .\examples\perf_metric_query_task.json
..\Scripts\python.exe -m ems_automation.cli perf-export-verify --task .\examples\perf_metric_export_verify_task.json
..\Scripts\python.exe -m ems_automation.cli perf-db-compare --task .\examples\perf_metric_db_compare_task.json
..\Scripts\python.exe -m ems_automation.cli perf-monitor-create --task .\examples\perf_monitor_create_task.json
..\Scripts\python.exe -m ems_automation.cli perf-query-create --task .\examples\perf_query_create_task.json
..\Scripts\python.exe -m ems_automation.cli testcase-run --task .\examples\perf_metric_golden_testcase.json
```

## 半自动登录

推荐先执行一次：

```powershell
..\Scripts\python.exe -m ems_automation.cli auth-login
```

命令会打开可见的 Edge 浏览器窗口。你在浏览器里手工完成账号输入、拼图验证和登录，回到终端按 Enter，工具会自动校验首页并保存登录态到：

`artifacts/auth/storage_state.json`

后续自动化会优先复用这个 `storage_state`；如果没有它，才回退到 `EMS_COOKIE` 或 `cookie.txt`。

## 网元映射

不要在 testcase 里硬编码网元 IP。推荐在任务里使用：

`network_element_ref`

并在项目根目录的 [inventory.json](/D:/!!!5GC测试/5gc-script/venv/ems_tester/ems_automation/inventory.json) 维护映射，例如：

```json
{
  "network_elements": {
    "upf_primary": {
      "display_name": "10.230.4.248",
      "ip": "10.230.4.248",
      "alias": "upf-primary",
      "match_texts": ["10.230.4.248", "upf-primary"]
    }
  }
}
```

也可以用环境变量临时覆盖：

```powershell
$env:EMS_NETWORK_ELEMENT = "10.230.4.248"
```

如果不确定当前页面实际显示了哪些网元，可先执行：

```powershell
..\Scripts\python.exe -m ems_automation.cli ne-list
```

## Cookie 配置

优先级如下：

1. 环境变量 `EMS_COOKIE`
2. 项目根目录下的 `cookie.txt`

示例：

```powershell
$env:EMS_COOKIE = "grafana_session=...; JSESSIONID=...;"
```

## DNN 对比任务示例

`config-dnn-compare` 会：

1. 在网页端进入 `业务开通配置 -> DNN配置 -> DNN VRF映射配置 -> 查询DNN VRF映射配置`
2. 执行 `SHOW UPFDNNVRFCONF`
3. 从数据库读取同网元配置，支持两种来源：
   - `config_snapshot`: `cm_ne.running_conf -> upf -> upfDnnVrfConf`
   - `mml_exec_log_show`: `cm_mml_exec_log` 中最近一次成功的 `SHOW UPFDNNVRFCONF`
4. 以网页端字段为准比对 `DNN名称 / N3 VRFID / N6 VRFID / N9 VRFID / S1U VRFID`

示例任务：

```json
{
  "network_element": "10.230.4.248",
  "dnn_name": "dnn1",
  "db": {
    "host": "127.0.0.1",
    "port": 55432,
    "user": "postgres",
    "password": "Mecdev_2023",
    "database": "db_upf",
    "schema": "ems_upf",
    "psql_path": "C:\\Program Files\\PostgreSQL\\14\\bin\\psql.exe",
    "source": "config_snapshot"
  }
}
```

如果不传 `dnn_name`，会按网页查询结果中的所有 DNN 逐条对比。输出中会包含：

- `web_rows`
- `db_rows`
- `comparisons`
- `db_only_rows`
- `summary`

## VRF 对比任务示例

`config-vrf-compare` 会：

1. 在网页端进入 `业务开通配置 -> VRF配置 -> 查询VRF配置`
2. 执行 `SHOW UPFVRFCONF`
3. 从数据库读取 `cm_ne.running_conf -> upf -> upfVrfConf`
4. 以网页端字段 `VRF ID` 为准对比网页行和数据库行

示例任务：
```json
{
  "network_element": "10.230.4.248",
  "vrf_id": "777",
  "db": {
    "host": "127.0.0.1",
    "port": 55432,
    "user": "postgres",
    "password": "Mecdev_2023",
    "database": "db_upf",
    "schema": "ems_upf",
    "psql_path": "C:\\Program Files\\PostgreSQL\\14\\bin\\psql.exe",
    "source": "mml_exec_log_show"
  }
}
```

## 测试用例组织

推荐把可执行测试统一组织成一个 JSON 用例文件，顶层固定为：

- `defaults`: 公共任务默认值，例如 `network_element`、数据库连接
- `variables`: 用例变量，例如 `dnn_name`
- `setup`: 前置清理或准备步骤
- `steps`: 主验证步骤
- `teardown`: 收尾清理步骤

每个步骤统一声明：

- `id`
- `action`
- `task`
- `assertions`
- `continue_on_failure`

首条标准化用例见：

- `examples/dnn_create_and_compare_testcase.json`
- `examples/vrf_create_and_compare_testcase.json`
- `examples/config_case_manifest_10.json`
- `examples/perf_metric_golden_testcase.json`

执行方式：

```powershell
..\Scripts\python.exe -m ems_automation.cli testcase-run --task .\examples\dnn_create_and_compare_testcase.json
..\Scripts\python.exe -m ems_automation.cli testcase-run --task .\examples\vrf_create_and_compare_testcase.json
..\Scripts\python.exe -m ems_automation.cli testcase-run --task .\examples\perf_metric_golden_testcase.json
```
