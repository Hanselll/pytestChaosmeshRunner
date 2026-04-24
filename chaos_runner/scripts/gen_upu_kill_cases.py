# -*- coding: utf-8 -*-
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "chaos_runner" / "cases"
PODKILL_DIR = CASES_DIR / "upu-podkill-cases"
CTRKILL_DIR = CASES_DIR / "upu-ctrkill-cases"

UPU_LABEL_KEY = "app.kubernetes.io/component"
UPU_MAIN_VALUES = [
    "dupf-pod-upu-1",
    "dupf-pod-upu-2",
    "dupf-pod-upu-3",
    "dupf-pod-upu-5",
    "dupf-pod-upu-6",
    "dupf-pod-upu-7",
    "dupf-pod-upu-9",
    "dupf-pod-upu-10",
    "dupf-pod-upu-11",
]
UPU_BACKUP_VALUES = [
    "dupf-pod-upu-4",
    "dupf-pod-upu-8",
    "dupf-pod-upu-12",
]

CONTAINER_NAMES = {
    "rc_leader": ["registry-center"],
    "rc_followers": ["registry-center"],
    "etcd_pods": ["etcd"],
    "ddb_shard0_master": ["redis"],
    "ddb_shard0_slaves": ["redis"],
    "sdb_master": ["sdb"],
    "sdb_slaves": ["sdb"],
    "sdb_pods": ["sdb"],
    "sdb_sentinels": ["sentinel"],
    "db_operator": ["manager"],
    "mq_proxy": ["mq-proxy-main"],
    "upu_main_pool": ["upu"],
    "upu_backup_pool": ["upu"],
}

COMMON_TARGETS = """
  - id: upu_main_pool
    finder: by_label_values
    label_key: "{upu_label_key}"
    label_values:
{upu_main_values}

  - id: upu_backup_pool
    finder: by_label_values
    label_key: "{upu_label_key}"
    label_values:
{upu_backup_values}
"""

CASE_DEFS = [
    {
        "slug": "rc-leader-upu-main",
        "targets": """
  - id: rc_leader
    finder: rc_leader
  - id: rc_component
    finder: rc_pods
""",
        "network_groups": ["rc_component"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("rc_leader", None), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "rc-leader-upu-backup",
        "targets": """
  - id: rc_leader
    finder: rc_leader
  - id: rc_component
    finder: rc_pods
""",
        "network_groups": ["rc_component"],
        "upu_target": "upu_backup_pool",
        "kill_targets": [("rc_leader", None), ("upu_backup_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "rc-follower-upu-main",
        "targets": """
  - id: rc_followers
    finder: rc_followers
  - id: rc_component
    finder: rc_pods
""",
        "network_groups": ["rc_component"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("rc_followers", {"mode": "random", "count": 1}), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "rc-follower-upu-backup",
        "targets": """
  - id: rc_followers
    finder: rc_followers
  - id: rc_component
    finder: rc_pods
""",
        "network_groups": ["rc_component"],
        "upu_target": "upu_backup_pool",
        "kill_targets": [("rc_followers", {"mode": "random", "count": 1}), ("upu_backup_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "etcd-one-upu-main",
        "targets": """
  - id: etcd_pods
    finder: etcd_pods
""",
        "network_groups": ["etcd_pods"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("etcd_pods", {"mode": "random", "count": 1}), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "etcd-two-upu-main",
        "targets": """
  - id: etcd_pods
    finder: etcd_pods
""",
        "network_groups": ["etcd_pods"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("etcd_pods", {"mode": "random", "count": 2}), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "ddb-shard0-master-upu-main",
        "targets": """
  - id: ddb_shard0_master
    finder: ddb_shard_master
    shard: "0"
  - id: ddb_all
    finder: ddb_pods
    role: all
""",
        "network_groups": ["ddb_all"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("ddb_shard0_master", None), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "ddb-shard0-master-upu-backup",
        "targets": """
  - id: ddb_shard0_master
    finder: ddb_shard_master
    shard: "0"
  - id: ddb_all
    finder: ddb_pods
    role: all
""",
        "network_groups": ["ddb_all"],
        "upu_target": "upu_backup_pool",
        "kill_targets": [("ddb_shard0_master", None), ("upu_backup_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "ddb-shard0-slave-upu-main",
        "targets": """
  - id: ddb_shard0_slaves
    finder: ddb_shard_slaves
    shard: "0"
  - id: ddb_all
    finder: ddb_pods
    role: all
""",
        "network_groups": ["ddb_all"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("ddb_shard0_slaves", {"mode": "random", "count": 1}), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "ddb-shard0-slave-upu-backup",
        "targets": """
  - id: ddb_shard0_slaves
    finder: ddb_shard_slaves
    shard: "0"
  - id: ddb_all
    finder: ddb_pods
    role: all
""",
        "network_groups": ["ddb_all"],
        "upu_target": "upu_backup_pool",
        "kill_targets": [("ddb_shard0_slaves", {"mode": "random", "count": 1}), ("upu_backup_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "ddb-shard0-master-slaves-upu-main",
        "targets": """
  - id: ddb_shard0_master
    finder: ddb_shard_master
    shard: "0"
  - id: ddb_shard0_slaves
    finder: ddb_shard_slaves
    shard: "0"
  - id: ddb_all
    finder: ddb_pods
    role: all
""",
        "network_groups": ["ddb_all"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("ddb_shard0_master", None), ("ddb_shard0_slaves", "all"), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "ddb-shard0-master-slaves-upu-backup",
        "targets": """
  - id: ddb_shard0_master
    finder: ddb_shard_master
    shard: "0"
  - id: ddb_shard0_slaves
    finder: ddb_shard_slaves
    shard: "0"
  - id: ddb_all
    finder: ddb_pods
    role: all
""",
        "network_groups": ["ddb_all"],
        "upu_target": "upu_backup_pool",
        "kill_targets": [("ddb_shard0_master", None), ("ddb_shard0_slaves", "all"), ("upu_backup_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "sdb-master-upu-main",
        "targets": """
  - id: sdb_master
    finder: sdb_master
  - id: sdb_pods
    finder: sdb_pods
""",
        "network_groups": ["sdb_pods"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("sdb_master", None), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "sdb-master-slave-upu-main",
        "targets": """
  - id: sdb_master
    finder: sdb_master
  - id: sdb_slaves
    finder: sdb_slaves
  - id: sdb_pods
    finder: sdb_pods
""",
        "network_groups": ["sdb_pods"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("sdb_master", None), ("sdb_slaves", {"mode": "random", "count": 1}), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "sdb-all-upu-main",
        "targets": """
  - id: sdb_master
    finder: sdb_master
  - id: sdb_slaves
    finder: sdb_slaves
  - id: sdb_pods
    finder: sdb_pods
""",
        "network_groups": ["sdb_pods"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("sdb_master", None), ("sdb_slaves", "all"), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "sdb-sentinels2-master-upu-main",
        "targets": """
  - id: sdb_sentinels
    finder: by_label
    label: "app.kubernetes.io/instance: dupf-sdb-sentinel"
  - id: sdb_master
    finder: sdb_master
  - id: sdb_pods
    finder: sdb_pods
""",
        "network_groups": ["sdb_sentinels", "sdb_pods"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("sdb_sentinels", {"mode": "random", "count": 2}), ("sdb_master", None), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "dbop-ddb-shard0-master-upu-main",
        "targets": """
  - id: db_operator
    finder: by_label
    label: "app.kubernetes.io/instance: dupf-db-operator"
  - id: ddb_shard0_master
    finder: ddb_shard_master
    shard: "0"
  - id: ddb_all
    finder: ddb_pods
    role: all
""",
        "network_groups": ["db_operator", "ddb_all"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("db_operator", {"mode": "random", "count": 1}), ("ddb_shard0_master", None), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "dbop-sdb-master-upu-main",
        "targets": """
  - id: db_operator
    finder: by_label
    label: "app.kubernetes.io/instance: dupf-db-operator"
  - id: sdb_master
    finder: sdb_master
  - id: sdb_pods
    finder: sdb_pods
""",
        "network_groups": ["db_operator", "sdb_pods"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("db_operator", {"mode": "random", "count": 1}), ("sdb_master", None), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "mq-one-upu-main",
        "targets": """
  - id: mq_proxy
    finder: by_label
    label: "app.kubernetes.io/component: dupf-pod-mq-proxy"
""",
        "network_groups": ["mq_proxy"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("mq_proxy", {"mode": "random", "count": 1}), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "mq-two-upu-main",
        "targets": """
  - id: mq_proxy
    finder: by_label
    label: "app.kubernetes.io/component: dupf-pod-mq-proxy"
""",
        "network_groups": ["mq_proxy"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("mq_proxy", {"mode": "random", "count": 2}), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "mq-three-upu-main",
        "targets": """
  - id: mq_proxy
    finder: by_label
    label: "app.kubernetes.io/component: dupf-pod-mq-proxy"
""",
        "network_groups": ["mq_proxy"],
        "upu_target": "upu_main_pool",
        "kill_targets": [("mq_proxy", {"mode": "random", "count": 3}), ("upu_main_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "mq-one-upu-backup",
        "targets": """
  - id: mq_proxy
    finder: by_label
    label: "app.kubernetes.io/component: dupf-pod-mq-proxy"
""",
        "network_groups": ["mq_proxy"],
        "upu_target": "upu_backup_pool",
        "kill_targets": [("mq_proxy", {"mode": "random", "count": 1}), ("upu_backup_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "mq-two-upu-backup",
        "targets": """
  - id: mq_proxy
    finder: by_label
    label: "app.kubernetes.io/component: dupf-pod-mq-proxy"
""",
        "network_groups": ["mq_proxy"],
        "upu_target": "upu_backup_pool",
        "kill_targets": [("mq_proxy", {"mode": "random", "count": 2}), ("upu_backup_pool", {"mode": "random", "count": 1})],
    },
    {
        "slug": "upu-main-dbop-ddb-sdb-mq-rc-etcd",
        "targets": """
  - id: db_operator
    finder: by_label
    label: "app.kubernetes.io/instance: dupf-db-operator"
  - id: ddb_shard0_master
    finder: ddb_shard_master
    shard: "0"
  - id: ddb_all
    finder: ddb_pods
    role: all
  - id: sdb_master
    finder: sdb_master
  - id: sdb_pods
    finder: sdb_pods
  - id: mq_proxy
    finder: by_label
    label: "app.kubernetes.io/component: dupf-pod-mq-proxy"
  - id: rc_leader
    finder: rc_leader
  - id: rc_component
    finder: rc_pods
  - id: etcd_pods
    finder: etcd_pods
""",
        "network_groups": ["db_operator", "ddb_all", "sdb_pods", "mq_proxy", "rc_component", "etcd_pods"],
        "upu_target": "upu_main_pool",
        "kill_targets": [
            ("upu_main_pool", {"mode": "random", "count": 1}),
            ("db_operator", {"mode": "random", "count": 1}),
            ("ddb_shard0_master", None),
            ("sdb_master", None),
            ("mq_proxy", {"mode": "random", "count": 1}),
            ("rc_leader", None),
            ("etcd_pods", {"mode": "random", "count": 1}),
        ],
    },
]


def _yaml_list(values, indent):
    return "\n".join([" " * indent + '- "{}"'.format(v) for v in values])


def _expand_text(expand, indent):
    if expand is None:
        return ""
    if expand == "all":
        return " " * indent + "expand: all\n"
    lines = [" " * indent + "expand:"]
    for key, value in expand.items():
        lines.append(" " * (indent + 2) + "{}: {}".format(key, value))
    return "\n".join(lines) + "\n"


def _common_targets():
    return COMMON_TARGETS.format(
        upu_label_key=UPU_LABEL_KEY,
        upu_main_values=_yaml_list(UPU_MAIN_VALUES, 6),
        upu_backup_values=_yaml_list(UPU_BACKUP_VALUES, 6),
    ).strip("\n")


def _network_faults(case_def):
    lines = []
    for idx, source_target in enumerate(case_def["network_groups"]):
        lines.extend(
            [
                "      - type: network_delay",
                "        selectors:",
                "          from: {}".format(source_target),
                "          to: {}".format(case_def["upu_target"]),
                "        direction: both",
                "        duration: 30s",
                "        delay:",
                "          latency: 300ms",
                "          jitter: 1000ms",
                "      - type: network_loss",
                "        selectors:",
                "          from: {}".format(source_target),
                "          to: {}".format(case_def["upu_target"]),
                "        direction: both",
                "        duration: 30s",
                "        loss:",
                "          loss: \"10\"",
                "          correlation: \"0\"",
            ]
        )
    return "\n".join(lines)


def _kill_faults(case_def, kill_type):
    lines = []
    for target, expand in case_def["kill_targets"]:
        lines.extend(
            [
                "      - type: {}".format(kill_type),
                "        target: {}".format(target),
                "        delay: 0s~1s",
                "        duration: 1s",
            ]
        )
        if kill_type == "container_kill":
            lines.append("        containerNames: [{}]".format(", ".join(CONTAINER_NAMES[target])))
        extra = _expand_text(expand, 8)
        if extra:
            lines.append(extra.rstrip("\n"))
    return "\n".join(lines)


def _render_case(case_def, kill_type, case_prefix):
    slug = case_def["slug"]
    name = "{}_{}".format(case_prefix, slug.replace("-", "_"))
    workflow = "wf-{}-{}".format(case_prefix.replace("_", "-"), slug)[:52]
    return """name: {name}

workflow:
  name: {workflow}
  namespace: default

renderer: modular_chaos

targets:
{targets}
{common_targets}
stages:
  - mode: parallel
    faults:
{network_faults}
{kill_faults}

wait_seconds: 40
cleanup: true
""".format(
        name=name,
        workflow=workflow,
        targets=case_def["targets"].strip("\n"),
        common_targets=_common_targets(),
        network_faults=_network_faults(case_def),
        kill_faults=_kill_faults(case_def, kill_type),
    )


def main():
    PODKILL_DIR.mkdir(parents=True, exist_ok=True)
    CTRKILL_DIR.mkdir(parents=True, exist_ok=True)
    for case_def in CASE_DEFS:
        slug = case_def["slug"].replace("-", "_")
        (PODKILL_DIR / "upu_podkill_{}.yaml".format(slug)).write_text(
            _render_case(case_def, "pod_kill", "upu_podkill"),
            encoding="utf-8",
        )
        (CTRKILL_DIR / "upu_ctrkill_{}.yaml".format(slug)).write_text(
            _render_case(case_def, "container_kill", "upu_ctrkill"),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
