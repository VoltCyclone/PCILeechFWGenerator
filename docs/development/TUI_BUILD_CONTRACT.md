# TUI build contract

The TUI's start button and command layer both call `BuildOrchestrator`, which
runs `sys.executable -m pcileechfwgenerator.pcileech_main ... build ...`.
Install this checkout in the active environment with `pip install -e '.[tui]'`.
The unified CLI owns host collection, local/container templating and host Vivado.
The TUI no longer runs simulated extraction/generation stages, a discarded
standalone profiler, or a separate privileged VFIO container on this path.

## Supported settings

| TUI setting | Meaning in the shared pipeline |
| --- | --- |
| Selected device's BDF | `--bdf`; the selected device determines the target |
| `board_type` | `--board`; select a real board, not the `default` sentinel |
| `local_build=True` | `--container-mode local`; local templating |
| `local_build=False` | `--container-mode container`; requires a container runtime, with no automatic local fallback |
| `output_directory` | `--output`; exact artifact directory, shared by templating and host Vivado; default `<datastore>/output` |
| `datastore_directory` | `--datastore`; capture directory; default `pcileech_datastore` |
| `donor_info_file` | Existing donor-template JSON override file, passed as `--donor-template`; does not replace a validated capture |
| `behavior_profiling` | Local mode only; `--profile <profile_duration>` when enabled, explicit `--profile 0` otherwise |
| `profile_duration` | Finite positive duration up to 3600 seconds; fractions are preserved; used only when profiling is enabled |
| `debug_mode` | Global `--verbose`; requires logging enabled |
| `enable_logging=False` | Global `--quiet`; error diagnostics remain available |
| `name`, `description`, `device_id`, `created_at`, `last_used` | Profile metadata; not firmware overrides or target selection |

Both modes still need Linux live collection and host Vivado. Container mode is
host-context-only during templating; it does not support live behavioral
profiling. If environment/VFIO policy changes the requested local profiling
state, generation fails explicitly instead of silently changing that setting.
The CLI's `--build-dir` is an alias for `--output`.
The duration applies to each existing generator/profile-artifact capture, not
the total build time. Both now receive the requested duration.

Paths are expanded and resolved, then passed as individual arguments without a
shell. Container commands mount the selected output directory at `/artifacts`
and an optional donor template read-only at `/donor/template.json`. Exported
JSON templates retain the requested filename/destination. Host paths with spaces
are covered by tests.

## Legacy settings and migration

Existing profiles remain readable and are never automatically rewritten. The
configuration dialog labels overrides that are unavailable. A build rejects
non-neutral values in these fields before host collection or module installation:

| Fields | Neutral value required |
| --- | --- |
| `advanced_sv`, `enable_variance`, `enable_performance_counters`, `performance_counters` | `False` |
| `donor_dump`, `auto_install_headers`, `skip_board_check`, `disable_ftrace`, `flash_after_build` | `False` |
| `device_type` | `generic` (automatic donor detection) |
| `optimization_level` | `balanced` |
| `enable_error_counters`, `power_management`, `error_handling` | `True` (legacy neutral sentinel) |
| `custom_parameters`, `feature_flags` | `{}` |
| `compatibility_overrides` | `[]` |

Neutral legacy sentinels do **not** assert that a firmware feature is enabled,
implemented or hardware-validated. Those fields have no configurable backend
contract yet. No arbitrary custom flags are forwarded. Direct CLI requests for
`--advanced-sv`, `--enable-variance` or non-generic `--device-type` also fail
explicitly; they previously either did nothing or broke the container parser.

For an old profile, review the error list, set unsupported overrides to their
neutral values, and save deliberately. New default and built-in profiles use
supported settings. The donor-dump button can clear an old request; it cannot
enable that unavailable path or implicitly change local/container selection.

## Progress, failure and cancellation

The CLI return code controls success. TUI progress no longer advances through
sleep-based mock stages. A nonzero exit remains a failure and trailing stderr is
retained. Stop signals the POSIX process group, escalates when necessary, waits
for process termination and reports cancellation rather than completion.
Command-layer undo owns newly created output files only, preserving files that
already existed before that build.

Real Python subprocess tests cover exit failure, idle output, cancellation and a
descendant that ignores SIGTERM. They do not prove cleanup of driver bindings,
device state or containers managed by an external daemon. Those checks still require
separate Linux/container and hardware acceptance testing.

## Validation evidence

The initial default-command parser test and unsupported-board-check test failed
before the change. A further regression reproduced the generator ignoring the
requested profiling duration. Commands now pass the actual unified and stage-two parsers.
Local-builder and Vivado calls are mocked for forwarding checks; container
runtime invocations are mocked for argument/mount checks. No live donor, Linux
container, Vivado, RTL simulation or board test was run for this increment.

Final software suite: **2,735 passed, 15 skipped, 99 deselected** on macOS arm64,
Python 3.12.4. The contract module passes targeted Flake8 and mypy; added/changed
lines pass Flake8. Full repository lint is not claimed.
