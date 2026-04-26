# Architecture Diagram

The editable project architecture diagram is in:

- `docs/architecture.drawio`

Open it with diagrams.net / draw.io. The diagram covers the main flow:

1. Case YAML, runtime config, and CLI/pytest entry points enter `runner.execute_case_from_args`.
2. `workflow_factory` validates the case, resolves role-aware targets, adapts legacy cases, and renders Chaos Mesh Workflow YAML.
3. The runner optionally splits network faults from kill/stress faults, verifies NetworkChaos rules, and executes workflows locally or through remote apply.
4. Chaos Mesh applies `NetworkChaos`, `PodChaos`, and `StressChaos` to target 5GC pods.
5. Observer hooks collect PRE/POST state, logs, events, LMT output, and execution result metadata.

The diagram also marks extension points per layer:

- Inputs: add case YAMLs, runtime config keys, pytest options, or runner CLI arguments.
- Runner: add orchestration phases, pre/post actions, or phase-specific workflow handling.
- Workflow factory: add finder names, renderer modules, or modular fault builders.
- Execution: add new apply backends behind `run_workflow`.
- Kubernetes / Chaos Mesh: add new Chaos Mesh kinds by pairing schema validation with renderer/builder support.
- Artifacts: add observer log sections, LMT commands, or result metadata fields.
