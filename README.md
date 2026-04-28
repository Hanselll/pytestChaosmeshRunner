# ChaosMesh Workflow Runner

`chaosmesh_workflow_runner_v13` 是一套 **基于 Chaos Mesh Workflow 的高阶实验编排工具**，用于在 Kubernetes 环境中自动生成/执行混沌用例（Workflow YAML），覆盖 **网络故障（delay/loss/partition）**、**PodKill**、**ContainerKill**，并支持 **角色感知目标解析（master/leader/talker 等）**。

---

## 1. 运行方式

```bash
# 在项目根目录
pytest --case chaos_runner/cases/xxx.yaml
```

如需只生成 workflow 而不真正执行，可加 `--dry-run`：

```bash
pytest --case chaos_runner/cases/xxx.yaml --dry-run
```

如需兼容旧入口，仍可继续使用：

```bash
python3 -m chaos_runner.runner --case chaos_runner/cases/xxx.yaml
```

如需让 PC 负责 `pytest` 调度与结果汇总，而将项目中的 `kubectl` 查询、target discovery、日志抓取以及 workflow `apply/delete` 都经由服务器 SSH 执行，可在配置中开启远端模式：

```yaml
EXECUTION_MODE: remote_apply
REMOTE_APPLY_HOST: your-server
REMOTE_APPLY_PORT: 22
REMOTE_APPLY_USER: your-user
REMOTE_APPLY_BASE_DIR: /tmp/chaos-runner
REMOTE_APPLY_SSH_EXTRA_OPTS: -T
```

然后执行：

```bash
py -m pytest --case chaos_runner/cases/xxx.yaml --runner-config-file path/to/config.yaml
```

如需把执行日志稳定保存在本地目录而不是系统临时目录，可增加：

```bash
py -m pytest --case chaos_runner/cases/xxx.yaml --runner-config-file path/to/config.yaml --runner-log-dir ..\\artifacts\\logs
```

旧入口同样支持：

```bash
py -m chaos_runner.runner --case chaos_runner/cases/xxx.yaml --config-file path/to/config.yaml --log-dir ..\\artifacts\\logs
```

### 1.1 观测日志（新增）

从当前版本开始，runner 会在 **执行 case 前后自动采集并记录观测信息**，日志文件路径会在启动时打印：

```text
[INFO] case-log: /tmp/chaos_case_<workflow-name>_<timestamp>.log
```

日志行带毫秒时间戳，格式示例：

```text
[2026-03-05 10:31:22.123] [PRE] pod status/node={...}
```

### 1.2 日志中包含哪些信息

每次执行会记录以下内容（PRE/POST 各一份）：

1. PodChaos 相关 Pod 信息（仅统计 PodChaos 最终选中的 Pod，不包含 NetworkChaos 选中的 Pod）
   - Pod 名称
   - Pod 运行状态（phase）
   - 所在节点（nodeName）
   - 若 Pod 在执行期间重建导致名字变化，会在 PRE/POST 对比中标注 `replaced_by=<new-pod-name>` 并显示新 Pod 节点
2. 组件角色状态
   - 角色状态以 `targets` 解析出的 Pod 列表作为组件范围（不是仅按最终进入 workflow 的 Pod）
   - DDB：masters / non-masters
   - RC：leader / followers
   - ETCD：leader / followers
3. 业务侧 LMT 信息（在 OAM 容器内执行）
   - 使用 table 形式采集（`--format table`）
   - 日志顺序为：先打印所有 PRE 表格，再打印所有 POST 表格
   - `lmt-cli list upfGetTalkerRole --format table`
   - `lmt-cli list upfGetNodeAssociateInfo --format table`
   - `lmt-cli list upfGetLicenseUsage --format table`
   - `lmt-cli list upfGetSessionNum --format table`
   - `lmt-cli list upfGetUpcSessionNum --format table`
   - `lmt-cli list upfGetUpuInstanceStatus --format table`
   - `lmt-cli list upfGetWholeMachineRate --format table`
   - `lmt-cli list upfGetUpuForwardRate --format table`
   - `lmt-cli list upfGetRoleInterfaceRate --format table`
4. 与 PodChaos 目标 Pod 相关的 k8s 事件
   - 仅在 POST 记录本次执行期间产生的事件（不再记录 PRE 基线）
   - 若目标 Pod 被重建并更名，也会一并记录新 Pod 相关事件

### 1.4 NetworkChaos 目标扩展说明（新增）

对于 NetworkChaos，runner 默认**不做**目标扩展，以保证 finder 解析结果被精确使用（例如 DDB 分片场景）。

如需按组件扩展，可在 case 顶层显式开启：

```yaml
network_expand_to_component_pods: true
```

开启后，runner 会将 `from/to` 中选中的 Pod 扩展为其所属组件（或网络分组）的全部 Pod，再写入最终 workflow YAML。

例如：

- `from` 里是单个 etcd Pod，会扩展为所有 etcd Pod；
- `to` 里是单个 upc-lb Pod，会扩展为所有 upc-lb Pod（同理 upu 只扩展到 upu 组）。

注意：扩展是按 `from`/`to` 各自目标组分别进行，NetworkChaos 仅发生在这两组目标之间，不会扩展为 namespace 内所有 Pod 两两互通故障。

### 1.5 EMS 告警收尾查询（可选）

runner 支持在非 dry-run 用例的最后一步调用项目内 EMS 自动化工具，查询并导出活动告警和历史告警：

```yaml
EMS_ALARM_ENABLED: true
EMS_ALARM_TARGET: all
EMS_ALARM_OUTPUT_DIR: ""
EMS_ALARM_TIMEOUT_SECONDS: 180
EMS_ALARM_PYTHON: ""
EMS_AUTOMATION_DIR: ems/ems_automation
```

启用后，runner 会执行：

```powershell
python -m ems_automation.cli alarm-read --target all --output-dir <case-log-dir>\ems_alarm_<workflow>_<timestamp>
```

输出包含 `alarm_export_summary.json`、`activity_alarm.json`、`history_alarm.json` 及对应 CSV。若 `EMS_ALARM_OUTPUT_DIR` 为空，结果默认写入本次 case log 目录下。

### 1.3 推荐查看方式

```bash
# 实时查看本次 case 日志
tail -f /tmp/chaos_case_<workflow-name>_<timestamp>.log

# 快速定位事件记录
grep "Runtime Target Events" -n /tmp/chaos_case_<workflow-name>_<timestamp>.log
```

常用字段：

- `wait_seconds`: runner 等待 workflow 运行的时间（秒）
- `cleanup`: 是否在等待结束后删除 workflow（true/false）

> ⚠️ 你的 `network.duration / deadline_seconds` 很长时，`wait_seconds` 必须足够大，否则 runner 会在网络故障尚未结束时清理 workflow。

---

## 2. Case 通用语法（所有 renderer 共享）

每个用例是一个 YAML 文件，推荐结构如下：

```yaml
name: <case-name>

workflow:
  name: <workflow-name>
  namespace: <workflow-namespace>   # 一般是 default

renderer: <renderer-name>           # 见下文“3. Renderers”

targets:
  - id: <target-id>
    finder: <finder-name>
    # finder=by_label 时需要额外提供 label
    # label: "key: value"

# 以下 section 是否需要，由 renderer 决定：
network: {...}
kill: {...}

wait_seconds: 60
cleanup: true
```

---

## 3. Targets 语法（finder 列表与参数）

`targets` 用于声明“逻辑目标”，runner 会在运行时解析出具体 Pod（以及 IP 等信息），并交给 renderer 生成 Workflow。

### 3.1 target 基本格式

```yaml
targets:
  - id: upc_talker
    finder: upc_talker
```

### 3.2 支持的 finder 一览（v15）

| finder | 返回类型 | 说明 | 额外字段 |
|---|---|---|---|
| `upc_talker` | dict | 解析 UPC talker Pod | 无 |
| `upc_non_talkers` | list[dict] | 解析非 talker 的 UPC pods | 无 |
| `upc_pods` | list[dict] | 解析所有 UPC pods | 无 |
| `rc_leader` | dict | 解析 RC leader | 无 |
| `rc_followers` | list[dict] | 解析 RC follower 列表 | 无 |
| `rc_pods` | list[dict] | 解析所有 RC pods | 无 |
| `rc_etcd_leader` | dict | 解析 RC 依赖的 etcd leader | 无 |
| `etcd_followers` | list[dict] | 解析 etcd follower 列表 | 无 |
| `etcd_pods` | list[dict] | 解析所有 etcd pods | 无 |
| `ddb_masters` | list[dict] | 解析 DDB（Redis Cluster）masters | 无 |
| `ddb_non_masters` | list[dict] | 解析 DDB 非 master pods | 无 |
| `ddb_pods` | list[dict] | 通用 DDB finder：按角色/分片范围过滤 | `role`(`all/master/slave`)、`shard_scope`(`all/in/not_in`)、`shard` |
| `ddb_shard_master` | dict | 解析指定 DDB 分片中的 master | `shard: "0"` 或 `shard: "shd-0"` |
| `ddb_shard_slaves` | list[dict] | 解析指定 DDB 分片中的 slaves | `shard: "0"` 或 `shard: "shd-0"` |
| `ddb_other_shard_pods` | list[dict] | 解析除指定分片外的所有 DDB pods（含 master/slave） | `shard: "0"` 或 `shard: "shd-0"` |
| `ddb_shard_master_peers` | list[dict] | 指定分片 master 的分区对端集合（本分片 slaves + 其他分片全部） | `shard: "0"` 或 `shard: "shd-0"` |
| `sdb_master` | dict | 解析 SDB 当前 master（单主 + 多从） | 无 |
| `sdb_slaves` | list[dict] | 解析 SDB slave 列表 | 无 |
| `sdb_sentinel_info` | dict | 解析 SDB sentinel 的 `info sentinel`（包含 master_address 等） | 无 |
| `by_label` | list[dict] | 根据 label 精确匹配 pods | `label: "key: value"` |
| `by_label_prefix` | list[dict] | 根据 label 值前缀匹配 pods（适合 `dupf-pod-upu-*`） | `label_prefix: "key: prefix"`（也兼容 `label`） |

`dict` 典型结构：`{"pod": "<pod-name>", "ip": "<pod-ip>"}`  
`list[dict]` 为上述 dict 的列表。

#### finder=by_label 示例

```yaml
- id: mq
  finder: by_label
  label: "app.kubernetes.io/component: dupf-pod-mq-proxy"
```

> 注意：`label` 必须是 `"key: value"` 形式（有冒号）。  
> 部分 renderer 的 `network.labels` 支持 `"key=value"`，但 **targets.by_label 不支持**。


#### finder=by_label_prefix 示例（UPU 推荐）

```yaml
- id: upu_pool
  finder: by_label_prefix
  label_prefix: "app.kubernetes.io/component: dupf-pod-upu-"
```

该写法会匹配 `dupf-pod-upu-1`、`dupf-pod-upu-2` ... 等所有 UPU 实例。
随后可配合 `expand.mode: random` 随机选取一个 UPU 注入故障。

#### DDB 单分片动态识别示例（不使用 by_label）

```yaml
targets:
  - id: ddb_shard0_master
    finder: ddb_shard_master
    shard: "0"       # 或 "shd-0"

  - id: ddb_shard0_slaves
    finder: ddb_shard_slaves
    shard: "0"
```

上述 finder 会动态识别目标分片中的 master，并把同分片其余实例作为 slaves 返回，可用于注入该分片内 1 主 2 从的网络故障。

#### DDB 指定分片 master 与“本分片 slaves + 其他分片全部”分区示例

```yaml
targets:
  - id: ddb_shard0_master
    finder: ddb_shard_master
    shard: "0"

  - id: ddb_shard0_master_peers
    finder: ddb_shard_master_peers
    shard: "0"

chaos:
  steps:
    - id: partition_shard0_master_from_peers
      action:
        type: network_partition
        params:
          from: ddb_shard0_master
          to: ddb_shard0_master_peers
          direction: both
          duration: 120s
```

#### 通用 DDB finder（更高效的组合方式）

`ddb_pods` 支持在一次 DDB 拓扑快照上做组合过滤，减少为不同 finder 重复发现的开销。

```yaml
# 例1：指定分片 slaves
- id: shard0_slaves
  finder: ddb_pods
  role: slave
  shard_scope: in
  shard: "0"

# 例2：其他分片全部 pods
- id: other_shards_all
  finder: ddb_pods
  role: all
  shard_scope: not_in
  shard: "0"
```

---

## 4. Expand 语法（精细控制“从列表目标里选哪些 Pod”）

当某个 target 解析结果是 `list[dict]` 时（例如 `ddb_masters`、`sdb_slaves`、`by_label`、`by_label_prefix`），你可以用 `expand` 指定选取策略。

目前支持：

### 4.1 全选（all）

```yaml
expand: all
```

### 4.2 随机选 N 个（random）

```yaml
expand:
  mode: random
  count: 1
  # seed: 123   # 可选：便于复现
```

### 4.3 按下标选（indices）

```yaml
expand:
  mode: indices
  indices: [0, 2]
```

---

## 5. Network 通用语法（支持 delay / loss / partition）

在支持 NetworkChaos 的 renderer 中，`network` 通常具有以下字段：

```yaml
network:
  # 生效时长（两种写法都支持，deadline_seconds 优先）
  duration: 180
  # deadline_seconds: 180

  direction: both            # both / from / to（Chaos Mesh 语义）

  # action/actions（二选一）
  actions: [delay, loss]     # 或 [partition] / [delay] / [loss]
  # action: both             # 等价于 actions: [delay, loss]

  delay:
    latency: 300ms          # 支持随机范围："100ms~500ms" 或 {min: 100ms, max: 500ms}
    jitter: 10ms            # 支持随机范围

  loss:
    loss: "10"             # 支持随机范围："1~20" 或 {min: 1, max: 20}
    correlation: "0"         # v13 内部字段名是 correlation（旧 case 里写 corr 也常见，但建议统一）

  selectors:
    from: <target-id>
    to: <target-id>

  # labels 可选（v13 支持不填，自动用 resolved pods 精确选择）
  labels:
    from: "key=value"        # 或 "key: value"
    to: "key=value"          # 或 "key: value"
```

---

## 6. Kill 通用语法（PodKill / ContainerKill）

不同 renderer 支持的 kill 类型不同，请看“7. Renderers 语法”。

### 6.1 kill.items 通用字段

```yaml
kill:
  items:
    - target: <target-id>
      delay: 0               # 支持 0 / "200ms" / "1s" / 1(秒) / 0.5(秒)
      # 也支持随机范围："200ms~2s" 或 {min: 200ms, max: 2s}
      expand: ...            # target 是 list 时可用
```

---


> 新增：`kill.items[].delay`、`network.delay.latency`、`network.delay.jitter`、`network.loss.loss` 均支持“范围随机”写法：
> - 字符串区间：`"100ms~500ms"`、`"1~10"`
> - 对象区间：`{min: 100ms, max: 500ms}`、`{min: 1, max: 10}`
> 每次生成 workflow 时会在区间内随机采样一次。

## 7. Renderers：支持的 case 类型与完整语法

v13 内置 renderer：

- `parallel_podkill`
- `network_then_parallel_podkill`
- `network_parallel_containerkill`
- `podkill_then_network`（旧风格，字段不同）
- `cpu_stress_parallel`
- `memory_stress_parallel`
- `modular_chaos`（可组合/可扩展）

下面给出每类 renderer 的**完整语法**与**典型示例**。

---

### 7.1 renderer = `parallel_podkill`

**用途**：只做 PodKill；可对多个目标并行 kill，并支持每个目标独立 delay/expand。

#### 语法

```yaml
renderer: parallel_podkill

targets: [...]   # 必填

kill:
  items:
    - target: <target-id>     # dict 或 list
      delay: 0                # 支持 0/"200ms"/"1s"/1/0.5
      expand: ...             # 当 target 解析为 list 时必填（all/random/indices）
```

#### 示例：kill talker + kill 所有 ddb masters（延迟 5s）

```yaml
name: example_parallel_podkill
workflow:
  name: wf-example-parallel-kill
  namespace: default

renderer: parallel_podkill

targets:
  - id: upc
    finder: upc_talker
  - id: ddb
    finder: ddb_masters

kill:
  items:
    - target: upc
      delay: 0
    - target: ddb
      expand: all
      delay: 5

wait_seconds: 30
cleanup: true
```

---

### 7.2 renderer = `network_then_parallel_podkill`  ✅（推荐：网络故障期间并行 PodKill）

**用途**：先注入 NetworkChaos（可 delay/loss/partition），并在网络故障生效期间并行 PodKill。  
**v13 特性**：`network.labels` **可不填**，自动使用 resolved pods 精确选择（避免“只想隔离 master 却误伤所有 sdb”）。

#### 语法

```yaml
renderer: network_then_parallel_podkill

targets: [...]    # 必填

network:
  duration: 180                   # 或 deadline_seconds
  direction: both
  actions: [delay, loss, partition]  # 可选：delay / loss / partition / 组合
  delay: {latency: 100ms, jitter: 10ms}
  loss:  {loss: "1", correlation: "0"}

  selectors:
    from: <target-id>             # 必填：引用 targets 里的 id
    to: <target-id>               # 必填

  # labels: 可选（不填则用 resolved pods）
  # labels:
  #   from: "k=v" 或 "k: v"
  #   to: "k=v" 或 "k: v"

kill:
  items:
    - target: <target-id>         # 必填
      delay: "200ms"              # 必填（允许 0）
      expand: ...                 # target 为 list 时必填（all/random/indices）
```

#### 示例 A：Sentinel ↔ 当前 SDB Master 分区 + 并行 kill master

```yaml
name: sdb-brain-split-and-kill-master
workflow:
  name: sdb-brain-split-and-kill-master
  namespace: default

renderer: network_then_parallel_podkill

targets:
  - id: sdb_master
    finder: sdb_master
  - id: sdb_sentinels
    finder: by_label
    label: "app.kubernetes.io/instance: dupf-sdb-sentinel"

network:
  duration: 500
  actions: [partition]
  direction: both
  selectors:
    from: sdb_sentinels
    to: sdb_master
  # labels 不填：自动使用 resolved pods 精确选择

kill:
  items:
    - target: sdb_master
      delay: 0

wait_seconds: 520
cleanup: true
```

#### 示例 B：delay+loss 网络退化 + 随机 kill 一个 UPC 非 talker（30s 网络故障保持）

```yaml
name: upc-net-impair-then-kill-nontalker
workflow:
  name: upc-net-impair-then-kill-nontalker
  namespace: default

renderer: network_then_parallel_podkill

targets:
  - id: upc_non_talkers
    finder: upc_non_talkers
  - id: rc
    finder: rc_leader

network:
  duration: 30
  actions: [delay, loss]
  delay: {latency: 200ms, jitter: 10ms}
  loss:  {loss: "5", correlation: "0"}
  direction: both
  selectors:
    from: upc_non_talkers
    to: rc
  # 若你想用 labels 做“整组”而非精确 pods，可填 labels

kill:
  items:
    - target: upc_non_talkers
      expand:
        mode: random
        count: 1
      delay: 1

wait_seconds: 60
cleanup: true
```

---

### 7.3 renderer = `network_parallel_containerkill` ✅（网络故障 + 并行 ContainerKill）

**用途**：网络故障（delay/loss/partition）与容器 kill（PodChaos.container-kill）并行执行；支持每个 Pod 独立 delay，并支持 `containerMap` 精确指定每个 Pod kill 哪些容器。

> 说明：该 renderer 的 `kill` 可以不写（只做网络故障），但若 `kill.items` 为空，生成的 workflow 会包含一个空的 kill-parallel（某些 Dashboard 版本可能不友好）。

#### 语法

```yaml
renderer: network_parallel_containerkill

targets: [...]    # network.selectors 引用的目标必须存在

network:
  duration: 180
  actions: [delay, loss, partition]
  direction: both
  selectors:
    from: <target-id>          # 必填
    to: <target-id>            # 必填
  # labels 可选：不填则用 resolved pods

kill:
  containerNames: [<c1>, <c2>]     # 可选：全局默认容器名
  items:
    - target: <target-id>
      delay: "200ms"
      expand: ...                 # list target 可选
      containerNames: [<c1>]      # 可选：覆盖全局默认
      containerMap:               # 可选：按 Pod 精确指定容器
        <pod-a>: [<c1>, <c2>]
        <pod-b>: [<c1>]
```

#### 示例 A：只做 Sentinel ↔ Master 分区（不写 kill）

```yaml
name: sdb-partition-sentinel-master
workflow:
  name: sdb-partition-sentinel-master
  namespace: default

renderer: network_parallel_containerkill

targets:
  - id: sdb_master
    finder: sdb_master
  - id: sdb_sentinels
    finder: by_label
    label: "app.kubernetes.io/instance: dupf-sdb-sentinel"

network:
  duration: 180
  actions: [partition]
  direction: both
  selectors:
    from: sdb_sentinels
    to: sdb_master

wait_seconds: 15
cleanup: true
```

#### 示例 B：网络退化 + 随机 kill 一个 mq 容器

```yaml
name: net-mq-containerkill
workflow:
  name: net-mq-containerkill
  namespace: default

renderer: network_parallel_containerkill

targets:
  - id: upc
    finder: upc_talker
  - id: mq
    finder: by_label
    label: "app.kubernetes.io/component: dupf-pod-mq-proxy"

network:
  duration: 30
  actions: [delay, loss]
  delay: {latency: 300ms, jitter: 10ms}
  loss:  {loss: "10", correlation: "0"}
  direction: both
  selectors:
    from: upc
    to: mq
  # labels 可不填：会用 resolved pods

kill:
  items:
    - target: mq
      expand: {mode: random, count: 1}
      delay: "200ms"
      containerNames: ["mq-proxy-main"]

wait_seconds: 60
cleanup: true
```

---

### 7.4 renderer = `podkill_then_network`（旧风格：先 kill 再网络）

**用途**：先并行 kill 两个目标（固定写法：kill.targets 里放两个 id），再注入网络 delay+loss。  
⚠️ 该 renderer 的 `network` 字段与其他 renderer 不一致，属于历史兼容。

#### 语法

```yaml
renderer: podkill_then_network

targets: [...]   # 需要至少包含 kill.targets 中引用的两个 target id

kill:
  targets: [<target-a>, <target-b>]   # 必须 ≥2，通常是 [upc_talker, rc_leader]

network:
  deadline_sec: 60
  direction: both
  upc_label_kv: "key: value"          # 注意这里必须是 key: value
  rc_label_kv:  "key: value"
  latency: "100ms"
  jitter: "10ms"
  loss: "1"
  corr: "0"
```

#### 示例

```yaml
name: example_podkill_then_network
workflow:
  name: upc-rc-concurrency
  namespace: default

renderer: podkill_then_network

targets:
  - id: upc_talker
    finder: upc_talker
  - id: rc_leader
    finder: rc_leader

kill:
  targets: [upc_talker, rc_leader]

network:
  deadline_sec: 60
  direction: both
  upc_label_kv: "app.kubernetes.io/component: dupf-pod-upc"
  rc_label_kv: "app.kubernetes.io/component: dupf-registry-center"
  latency: "100ms"
  jitter: "10ms"
  loss: "1"
  corr: "0"

wait_seconds: 80
cleanup: true
```

---

## 8. 选型建议（用哪个语法最合适）

- 想做 **“网络故障期间并行 kill”**：优先用 `network_then_parallel_podkill`
- 想做 **“网络故障 + container-kill”**：用 `network_parallel_containerkill`
- 只想并行 kill 一批 pods：用 `parallel_podkill`
- 老用例兼容：`podkill_then_network`（不建议新写）

---

## 9. v13 关键增强点（你最关心的）

- NetworkChaos 支持 `partition`
- `network.labels` 可不填：自动 fallback 到 resolved pods（精确到 Pod 名）
- 解决“master/slave label 相同导致误伤整组”的问题（例如只隔离 SDB master）


---


### 7.5 renderer = `cpu_stress_parallel`

**用途**：对一个或多个指定角色 Pod 同时注入 CPU 压力（StressChaos），并支持为每个 target 单独设置 CPU 参数。

```yaml
renderer: cpu_stress_parallel

targets:
  - id: rc_leader
    finder: rc_leader

stress:
  # 支持单目标（兼容旧写法）：
  # target: rc_leader
  # expand: all | {mode: random, count: 1} | {indices: [0]}

  # 全局默认值（可被每个 target 覆盖）
  duration: 30s                 # 支持随机范围："10s~60s" / {min: 10s, max: 60s}
  cpu:
    workers: 2
    load: 80

  # 支持多目标（推荐）：可同时对多个角色注入 stress
  # 每个 target 可单独设置 cpu 参数
  targets:
    - target: rc_leader
      cpu:
        workers: 1
        load: 60
    - target: rc_followers
      expand:
        mode: random
        count: 1
      cpu:
        workers: 2
        load: 90
```

> 说明：`stress.targets[]` 中可选填写 `cpu`/`memory` 和 `duration` 来覆盖全局默认值。

### 7.6 renderer = `memory_stress_parallel`

**用途**：对一个或多个指定角色 Pod 同时注入内存压力（StressChaos），并支持为每个 target 单独设置内存参数。

```yaml
renderer: memory_stress_parallel

targets:
  - id: sdb_master
    finder: sdb_master

stress:
  # 全局默认值（可被每个 target 覆盖）
  duration: 45s
  memory:
    workers: 1
    size: 256MB

  targets:
    - target: sdb_master
      memory:
        workers: 1
        size: 192MB
    - target: sdb_slaves
      expand:
        mode: random
        count: 1
      memory:
        workers: 2
        size: 384MB
```



### 7.7 renderer = `modular_chaos` ✅（模块化可扩展组合）

**用途**：在一个 workflow 中按 stage 组合多种故障（网络 / kill / stress），适合表达“同一背景故障下执行不同动作”的复杂实验。  
当前内置故障类型：
- `pod_kill`
- `container_kill`
- `network_delay`
- `network_loss`
- `network_partition`
- `cpu_stress`
- `memory_stress`

---

#### 7.7.1 基本语法（推荐 `stages`）

```yaml
renderer: modular_chaos

targets: [...]

stages:
  - mode: parallel   # parallel / serial
    faults:
      - type: pod_kill
        target: rc_leader
        delay: "0s~1s"
      - type: network_loss
        selectors: {from: upc, to: rc_leader}
        duration: 30s
        loss: {loss: "1~10", correlation: "0"}
```

也可使用简化写法（单 stage）：

```yaml
renderer: modular_chaos
mode: parallel
faults: [...]
```

> `stages` 存在时，顶层会按 **Serial** 串行执行各 stage；每个 stage 内部由 `mode` 决定并行/串行。

---

#### 7.7.2 `network_*` 在 modular 中的方向语义（`direction`）

`network_delay / network_loss / network_partition` 都支持：

```yaml
- type: network_delay
  selectors:
    from: upc_lb
    to: rc_leader
  direction: both   # both / from / to
```

方向含义与 Chaos Mesh 一致：

- `both`：双向影响（`from -> to` 和 `to -> from` 都受影响）
- `to`：仅影响 **from 发往 to** 的流量（可理解为“打 to 方向”）
- `from`：仅影响 **to 发往 from** 的流量（可理解为“打 from 方向”）

可用记忆方式：
- `selectors.from=A, selectors.to=B`
  - `direction: to` => 主要影响 **A -> B**
  - `direction: from` => 主要影响 **B -> A**
  - `direction: both` => **A <-> B**

---

#### 7.7.3 modular 各故障类型常用字段速查

##### A) `pod_kill`

```yaml
- type: pod_kill
  target: rc_leader
  # target 为 list 时可配 expand
  # expand: all / {mode: random, count: 1} / {indices: [0]}
  delay: "0s~1s"
  duration: 1s      # 可选；建议显式设置短时，避免 stage 挂起太久
```

##### B) `container_kill`

```yaml
- type: container_kill
  target: upc_lb
  expand: {mode: random, count: 1}
  containerNames: [upc-lb]   # 必填
  delay: "0s~1s"
  duration: 1s
```

##### C) `network_delay`

```yaml
- type: network_delay
  selectors: {from: upc_lb, to: rc_leader}
  duration: 30s
  direction: both
  delay:
    latency: "100ms~500ms"
    jitter: "5ms~20ms"
```

##### D) `network_loss`

```yaml
- type: network_loss
  selectors: {from: upc_lb, to: rc_leader}
  duration: 30s
  direction: both
  loss:
    loss: "1~10"
    correlation: "0"
```

##### E) `network_partition`

```yaml
- type: network_partition
  selectors: {from: sdb_sentinels, to: sdb_master}
  duration: 20s
  direction: both
```

---

#### 7.7.4 示例 1：同一网络背景下，先 container kill 再 pod kill（两阶段）

```yaml
name: modular_net_loss_delay_kill_two_stage
workflow:
  name: wf-modular-net-loss-delay-kill-two-stage
  namespace: default

renderer: modular_chaos

targets:
  - id: rc_leader
    finder: rc_leader
  - id: upc_lb
    finder: by_label
    label: "app.kubernetes.io/dupf.service: dupf-pod-upc-lb"

stages:
  - mode: parallel
    faults:
      - type: network_delay
        selectors: {from: upc_lb, to: rc_leader}
        direction: both
        duration: 30s
        delay: {latency: 300ms, jitter: 10ms}
      - type: network_loss
        selectors: {from: upc_lb, to: rc_leader}
        direction: both
        duration: 30s
        loss: {loss: "10", correlation: "0"}
      - type: container_kill
        target: rc_leader
        containerNames: [registry-center]
        delay: "0s~1s"
        duration: 1s
      - type: container_kill
        target: upc_lb
        expand: {mode: random, count: 1}
        containerNames: [upc-lb]
        delay: "0s~1s"
        duration: 1s

  - mode: parallel
    faults:
      - type: network_delay
        selectors: {from: upc_lb, to: rc_leader}
        direction: both
        duration: 30s
        delay: {latency: 300ms, jitter: 10ms}
      - type: network_loss
        selectors: {from: upc_lb, to: rc_leader}
        direction: both
        duration: 30s
        loss: {loss: "10", correlation: "0"}
      - type: pod_kill
        target: rc_leader
        delay: "0s~1s"
        duration: 1s
      - type: pod_kill
        target: upc_lb
        expand: {mode: random, count: 1}
        delay: "0s~1s"
        duration: 1s

wait_seconds: 120
cleanup: true
```

---

#### 7.7.5 示例 2：单向退化（只影响 upc_lb -> rc_leader）+ 随机 kill 1 个 upc_lb

```yaml
name: modular_one_way_to_rc_with_random_kill
workflow:
  name: wf-modular-one-way-to-rc-with-random-kill
  namespace: default

renderer: modular_chaos

targets:
  - id: rc_leader
    finder: rc_leader
  - id: upc_lb
    finder: by_label
    label: "app.kubernetes.io/dupf.service: dupf-pod-upc-lb"

stages:
  - mode: parallel
    faults:
      - type: network_delay
        selectors: {from: upc_lb, to: rc_leader}
        direction: to
        duration: 20s
        delay: {latency: 500ms, jitter: 20ms}
      - type: network_loss
        selectors: {from: upc_lb, to: rc_leader}
        direction: to
        duration: 20s
        loss: {loss: "5~15", correlation: "0"}
      - type: pod_kill
        target: upc_lb
        expand: {mode: random, count: 1}
        delay: "0s~1s"
        duration: 1s

wait_seconds: 40
cleanup: true
```

---

#### 7.7.6 示例 3：`serial` stage 内逐步注入（先 delay 再 loss）

```yaml
name: modular_serial_net_steps
workflow:
  name: wf-modular-serial-net-steps
  namespace: default

renderer: modular_chaos

targets:
  - id: upc_non_talkers
    finder: upc_non_talkers
  - id: rc_leader
    finder: rc_leader

stages:
  - mode: serial
    faults:
      - type: network_delay
        selectors: {from: upc_non_talkers, to: rc_leader}
        direction: both
        duration: 15s
        delay: {latency: 150ms, jitter: 10ms}
      - type: network_loss
        selectors: {from: upc_non_talkers, to: rc_leader}
        direction: both
        duration: 15s
        loss: {loss: "3", correlation: "0"}

  - mode: parallel
    faults:
      - type: container_kill
        target: rc_leader
        containerNames: [registry-center]
        delay: "500ms"
        duration: 1s

wait_seconds: 45
cleanup: true
```

---

#### 7.7.7 排障建议（modular 常见“看起来没执行”问题）

1. **确认 stage 是否串行推进**：顶层 `templates[entry].templateType` 应为 `Serial`（当使用 `stages` 时）。
2. **检查 kill fault 是否有边界时长**：建议为 `pod_kill/container_kill` 显式写 `duration: 1s`。
3. **检查 `wait_seconds` 是否覆盖总时长**：建议 ≥ 所有 stage 关键故障持续时间之和 + 余量。
4. **确认 target 解析是否稳定**：`expand.mode=random` 每次可能选不同 pod，排障时可加 `seed` 固定选择。

#### 7.7.8 新增 case（分区 + 分阶段 kill）

仓库已补充以下 `modular_chaos` 用例，可直接运行：

- `chaos_runner/cases/modular_partition_ddb_with_upc_upu_upclb_kill.yaml`
  - DDB：`ddb_shard_master(shard=0)` 与 `ddb_shard_master_peers(shard=0)` 分区
  - 同时对 `upc_talker` + 随机 1 个 `upu_pool` + 随机 1 个 `upc_lb_pool` 做 stage-1 `container_kill`、stage-2 `pod_kill`。
- `chaos_runner/cases/modular_partition_sdb_with_upc_upu_upclb_kill.yaml`
- `chaos_runner/cases/modular_partition_rc_with_upc_upu_upclb_kill.yaml`
- `chaos_runner/cases/modular_partition_rc_etcd_with_upc_upu_upclb_kill.yaml`
- `chaos_runner/cases/modular_partition_sentinel_with_sdbmaster_upc_upu_upclb_kill.yaml`
  - Sentinel ↔ SDB Master 分区，并对主用 `sdb_master` 分 stage 执行 `container_kill` + `pod_kill`。

上述 case 中 UPU 目标统一使用：

```yaml
- id: upu_pool
  finder: by_label_prefix
  label_prefix: "app.kubernetes.io/component: dupf-pod-upu-"
```

#### 可扩展性说明

`modular_chaos` 使用故障构建器注册表（`FAULT_BUILDERS`）实现，新增故障类型只需新增一个 `@fault_builder("new_type")` 函数，即可与已有故障组合使用。
