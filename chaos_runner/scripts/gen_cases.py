#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, os, textwrap

TEMPLATE = """name: {case_name}
workflow:
  name: {wf_name}
  namespace: default

renderer: {renderer}

targets:
{targets}

{body}

wait_seconds: {wait_seconds}
cleanup: true
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--out", default="chaos_runner/cases/generated")
    ap.add_argument("--renderer", default="parallel_podkill", choices=["parallel_podkill","podkill_then_network"])
    ap.add_argument("--wait", type=int, default=25)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    for i in range(args.count):
        case_name = "case_{:04d}".format(i)
        wf_name = "wf-{}".format(case_name)

        if args.renderer == "parallel_podkill":
            targets = textwrap.indent(textwrap.dedent("""- id: upc
  finder: upc_talker
- id: ddb
  finder: ddb_masters
"""), "  ")
            body = textwrap.dedent("""kill:
  items:
    - target: upc
      delay: 0
    - target: ddb
      expand: all
      delay: 0
""").rstrip()
        else:
            targets = textwrap.indent(textwrap.dedent("""- id: upc_talker
  finder: upc_talker
- id: rc_leader
  finder: rc_leader
"""), "  ")
            body = textwrap.dedent("""kill:
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
""").rstrip()

        content = TEMPLATE.format(
            case_name=case_name, wf_name=wf_name, renderer=args.renderer,
            targets=targets.rstrip(), body=body, wait_seconds=args.wait
        )

        with open(os.path.join(args.out, "{}.yaml".format(case_name)), "w", encoding="utf-8") as f:
            f.write(content)

    print("generated {} cases into {}".format(args.count, args.out))

if __name__ == "__main__":
    main()
