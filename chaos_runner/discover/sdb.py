"""Find SDB master and slave pods and sentinel information.

This module implements discovery functions for the SDB (an additional
Redis cluster used by DUPF deployments).  Unlike the distributed DDB
cluster, the SDB cluster typically has a single master and one or more
slaves.  Each pod exposes the standard Redis ``info replication``
command, which we use to determine its role.  In addition, SDB often
deploys a set of Redis Sentinel instances to monitor the master and
manage failover; those sentinels can be queried via the ``info
sentinel`` command.

The functions here follow the same conventions as other discovery
functions in :mod:`chaos_runner.discover`: they accept no arguments and
return either a single dict with ``pod`` and ``ip`` keys (for the
master) or a list of such dicts (for slaves).  Sentinel information is
returned as a dict containing parsed fields from the ``info sentinel``
output.
"""

from typing import Dict, List, Optional

from chaos_runner.tools.k8s import sh, exec_in_pod, get_pod_ip
from chaos_runner import config


def _list_pods_by_prefix(prefix: str) -> List[str]:
    """Return all pod names in the target namespace containing ``prefix``.

    The match is performed case‑insensitively on the pod name.  This helper
    function is used both for SDB pods (e.g. ``dupf-sdb-0``) and for
    sentinel pods (e.g. ``dupf-sdb-sentinel-0``).

    Args:
        prefix: Substring that must appear in the pod name.  Matching is
            case‑insensitive.

    Returns:
        A list of pod names matching the given prefix.  If no pods
        match, the list will be empty.
    """
    raw = sh(
        "kubectl -n {} get pod -o jsonpath='{{.items[*].metadata.name}}'".format(
            config.NS_TARGET
        )
    )
    names = [n for n in (raw or "").split() if n]
    prefix_lower = prefix.lower().strip()
    out: List[str] = []
    for name in names:
        lowered = name.lower()
        if lowered == prefix_lower or lowered.startswith(prefix_lower + "-"):
            out.append(name)
    return out


def _get_sdb_role(pod: str) -> Optional[str]:
    """Return the replication role (``master`` or ``slave``) for a given SDB pod.

    This function executes ``redis-cli info replication`` inside the SDB pod
    using the configured port and authentication.  It parses the output for
    a line starting with ``role:`` and returns the role value.  If the
    command fails or the output cannot be parsed, ``None`` is returned.

    Args:
        pod: Name of the pod to query.

    Returns:
        ``"master"``, ``"slave"`` or ``None`` if the role could not be
        determined.
    """
    # Build the command to run inside the pod.  We export the password in
    # REDISCLI_AUTH so ``redis-cli`` will authenticate automatically.  The
    # ``info replication`` section contains several lines including the
    # current role, the master link status and the number of connected
    # slaves.
    cmd = (
        'export REDISCLI_AUTH="{auth}"; '
        'redis-cli -p {port} info replication'
    ).format(auth=config.SDB_AUTH, port=int(config.SDB_PORT))
    try:
        raw = exec_in_pod(config.NS_TARGET, pod, cmd)
    except Exception:
        return None
    for line in (raw or "").splitlines():
        if not line:
            continue
        if line.strip().lower().startswith("role"):
            # Expected format: role:master or role:slave
            parts = line.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip().lower()
    return None


def find_sdb_master() -> Dict[str, str]:
    """Locate the SDB master pod and return its name and IP address.

    The discovery logic enumerates all pods whose names contain the
    configured :data:`config.SDB_POD_PREFIX` and queries each for its
    replication role.  The first pod reporting ``master`` is returned.
    If no master is found, a :class:`RuntimeError` is raised.

    Returns:
        A dictionary with keys ``pod`` and ``ip`` describing the master
        pod.

    Raises:
        RuntimeError: If no SDB pods are found or none reports itself as
            master.
    """
    pods = _list_pods_by_prefix(config.SDB_POD_PREFIX)
    if not pods:
        raise RuntimeError(
            "No SDB pods found in namespace {} matching prefix {}".format(
                config.NS_TARGET, config.SDB_POD_PREFIX
            )
        )
    for pod in pods:
        role = _get_sdb_role(pod)
        if role == "master":
            ip = get_pod_ip(config.NS_TARGET, pod)
            return {"pod": pod, "ip": ip}
    raise RuntimeError(
        "Unable to determine SDB master: no pod reported master role (checked {})".format(
            pods
        )
    )


def find_sdb_slaves() -> List[Dict[str, str]]:
    """Return a list of SDB pods that are acting as slaves.

    Each slave entry is a dictionary with ``pod`` and ``ip`` keys.  The
    ordering of the returned list corresponds to the order of pods in the
    underlying Kubernetes enumeration.  If no slaves are present, an empty
    list is returned.

    Returns:
        A list of dictionaries with the names and IP addresses of SDB slave
        pods.
    """
    pods = _list_pods_by_prefix(config.SDB_POD_PREFIX)
    out: List[Dict[str, str]] = []
    for pod in pods:
        role = _get_sdb_role(pod)
        if role == "slave":
            ip = get_pod_ip(config.NS_TARGET, pod)
            out.append({"pod": pod, "ip": ip})
    return out


def find_sdb_pods() -> List[Dict[str, str]]:
    """Return all non-sentinel SDB pods."""
    pods = _list_pods_by_prefix(config.SDB_POD_PREFIX)
    sentinel_prefix = str(config.SDB_SENTINEL_POD_PREFIX or "").strip().lower()
    out: List[Dict[str, str]] = []
    for pod in pods:
        low = str(pod or "").strip().lower()
        if sentinel_prefix and (low == sentinel_prefix or low.startswith(sentinel_prefix + "-")):
            continue
        ip = get_pod_ip(config.NS_TARGET, pod)
        out.append({"pod": pod, "ip": ip})
    return out


def _parse_sentinel_info(raw: str) -> Dict[str, Optional[str]]:
    """Parse ``redis-cli info sentinel`` output into a structured dict.

    The sentinel ``info`` output contains a number of key/value pairs as
    well as ``masterN`` lines describing the monitored master.  We extract
    ``sentinel_masters`` (count of masters monitored by this sentinel) and
    parse the first master line (``master0``) into its constituent fields.

    Args:
        raw: Raw text returned by ``redis-cli info sentinel``.

    Returns:
        A dictionary with the following keys:

        ``sentinel_masters`` (int): Number of masters monitored.
        ``master_name`` (str): Name of the monitored master (e.g. "mymaster").
        ``master_address`` (str): Address of the master in ``host:port`` format.
        ``master_slaves`` (int): Number of slave replicas for the master.
        ``master_sentinels`` (int): Number of sentinel instances monitoring the
            master.
    """
    out: Dict[str, Optional[str]] = {
        "sentinel_masters": None,
        "master_name": None,
        "master_address": None,
        "master_slaves": None,
        "master_sentinels": None,
    }
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("sentinel_masters"):
            # sentinel_masters:<count>
            parts = line.split(":", 1)
            if len(parts) == 2:
                try:
                    out["sentinel_masters"] = int(parts[1].strip())
                except ValueError:
                    out["sentinel_masters"] = None
        elif line.startswith("master0"):
            # master0:name=mymaster,status=ok,address=10.233.99.86:17369,slaves=2,sentinels=3
            # Strip the ``master0:`` prefix and split by commas
            kv_str = line.split(":", 1)[1] if ":" in line else ""
            for item in kv_str.split(","):
                if "=" not in item:
                    continue
                key, val = item.split("=", 1)
                key = key.strip().lower()
                val = val.strip()
                if key == "name":
                    out["master_name"] = val
                elif key == "address":
                    out["master_address"] = val
                elif key == "slaves":
                    try:
                        out["master_slaves"] = int(val)
                    except ValueError:
                        out["master_slaves"] = None
                elif key == "sentinels":
                    try:
                        out["master_sentinels"] = int(val)
                    except ValueError:
                        out["master_sentinels"] = None
    return out


def find_sdb_sentinel_info() -> Dict[str, Optional[str]]:
    """Query one of the SDB sentinel pods and return parsed sentinel info.

    The function locates pods whose names contain the configured
    :data:`config.SDB_SENTINEL_POD_PREFIX` and executes ``redis-cli info
    sentinel`` inside the first such pod.  The raw output is passed to
    :func:`_parse_sentinel_info` to extract the monitored master
    information.  A ``pod`` key is added to the returned dictionary to
    indicate which sentinel pod was queried.

    Returns:
        A dictionary with sentinel and master fields as described in
        :func:`_parse_sentinel_info`, plus an additional ``pod`` key.

    Raises:
        RuntimeError: If no sentinel pods are found in the target namespace.
    """
    sentinel_pods = _list_pods_by_prefix(config.SDB_SENTINEL_POD_PREFIX)
    if not sentinel_pods:
        raise RuntimeError(
            "No SDB sentinel pods found in namespace {} matching prefix {}".format(
                config.NS_TARGET, config.SDB_SENTINEL_POD_PREFIX
            )
        )
    pod = sentinel_pods[0]
    cmd = (
        'export REDISCLI_AUTH="{auth}"; '
        'redis-cli -p {port} info sentinel'
    ).format(auth=config.SDB_AUTH, port=int(config.SDB_SENTINEL_PORT))
    raw = exec_in_pod(config.NS_TARGET, pod, cmd)
    info = _parse_sentinel_info(raw)
    info["pod"] = pod
    return info
