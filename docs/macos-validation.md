# macOS foundation validation — 11 November 2026

This report covers the initial M0/M2 software foundation. It is not a platform release
or physical-device acceptance report. See [setup](macos-setup.md) for commands and
the [project plan](../PRUSA_DIRECT_DRIVE_MACOS_PROJECT_PLAN.md) for remaining gates.

## Reference points

| Item | Inspected revision / environment |
| --- | --- |
| Prusa application baseline | `2b56c427e8826324933ed441270f5ac9920ddb5f` |
| macOS branch before implementation | `8e7dc4b6e010240b6a814e637af56c961a19b1d8` (plan document only) |
| Scientific Camera Capture | `38e691be39bf13bd3578f8a1afcbf6ead090fe96`, version `0.1.0` |
| Local host | Intel `x86_64`, macOS `15.8.1` (`24H32`) |
| Interpreter and UI | Python `3.12.3`, Tk `8.6`, PySide6 `6.12.0` |
| Other principal dependencies | NumPy `2.5.4`, Pillow `13.0.0`, pyserial `3.5`, trimesh `5.2.0`, matplotlib `3.11.2` |
| Test tooling | pytest `9.2.0`, pytest-qt `4.5.0` |
| Native camera packages | IDS peak and GI/Aravis absent from the application environment |

The separate camera checkout was inspected and tested without source changes. Its own
environment included the declared export extra (NumPy and tifffile) for pixel-format tests.

## Evidence

- Existing Prusa suite before new tests: **25 passed, 11 skipped**. The skipped tests
  require an absent, untracked `model.3mf`; they are not evidence of model/UI acceptance.
- Complete foundation suite with the optional simulator installed: **33 passed, 11 skipped**.
- Complete foundation suite after uninstalling the optional camera package: **30 passed,
  14 skipped**. Three additional skips are the optional simulator integration tests.
  This includes fresh-process Qt window construction/show/close and the native Tk launch
  check, with camera imports blocked and printer entry points guarded.
- `python -m pip check`, Python compilation and `git diff --check` passed.
- Independent camera suite: **56 passed, 1 skipped**. The skipped test requires explicit
  physical-camera opt-in. No native SDK was installed.
- The simulator smoke command wrote a new SCC dataset, saved a snapshot/event, closed
  acquisition, and read back a complete recording with checksum verification. This small
  smoke sample establishes API/storage compatibility, not sustained recording throughput.

The first sandboxed baseline attempt failed two localhost IPC tests because socket binding
was denied. Rerunning with localhost access produced the baseline above. A fresh-process
Qt launch also exposed hidden flags on locally installed Qt plugin files; those flags
prevent plugin discovery and are an environment issue, not hardware validation. Clearing
flags allowed both offscreen and native Cocoa minimal windows to launch. The workspace
subsequently re-applied hidden flags during tool activity, causing intermittent startup
failures. For the successful base-install suite, platform plugins were copied into a
temporary directory outside the workspace, made visible, and selected with
`QT_QPA_PLATFORM_PLUGIN_PATH`; no workaround was added to application or test code.
For the successful simulator-installed suite, the startup checks ran first after clearing
the local plugin flags. Both runs exercised every test; remaining tests kept their order.

## Remaining acceptance

- Physical printer USB serial, UDP metrics, homing, probing and STOP/retreat have not
  been exercised. The exact modified `M410` firmware remains required and unpublished.
- Physical camera model/firmware, USB topology, sensor format/stride, SSD throughput,
  recording loss telemetry and recovery remain unverified.
- Apple Silicon and an Intel/arm64 physical acceptance matrix remain outstanding.
- The full camera adapter, GUI camera controls and experiment/frame associations belong
  to M3–M5; this change only provides an explicit simulator seam.
- The macOS GitHub Actions workflow is added here; local checks do not establish a
  successful hosted CI run.
