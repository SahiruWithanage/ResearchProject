"""Browser UI for the simulator: a thin shell over the existing pipeline.

The UI never re-implements simulator behaviour. It enumerates options by
introspecting the plug-and-play registries (`ui.introspect`), validates
drafts with the loader's own `parse_config`, runs experiments through the
same `Environment` the CLI uses, and writes the same four log files via
`RunLogger`. Configs built here are ordinary YAML files, interchangeable
with `experiments/run_phase1.py`.

Launch with:  python -m ui
"""
