# -*- coding: utf-8 -*-
from chaos_runner.discover.pods import find_pods_by_name_prefix
from chaos_runner import config


def find_smf_pods():
    """
    Return all core SMF business pods.

    SMF currently has no confirmed runtime role split comparable to DUPF UPC
    talker/non-talker. Callers should treat this as a role-less pool and use
    expand=random/count in the case definition when selecting subsets.
    """
    ns = str(getattr(config, "NS_TARGET", "") or "").strip().lower()
    prefix = "smf-smf-"
    if ns.startswith("ns-") and "smf" not in ns:
        prefix = "{}-smf-".format(ns[3:])
    return find_pods_by_name_prefix(prefix)
