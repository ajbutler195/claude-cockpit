# Changelog

## [2026-10-27] - Type: Config
- Change: Plans the multi-provider extension and measures OpenAI's Codex CLI for it.
  - `docs/multi_provider/requirements.md` specifies `cockpit`: one command that starts a session on
    any account of any supported AI coding service, plus a usage screen for every provider.
  - `docs/multi_provider/implementation_plan.md` plans it in phases 0 to 7.
  - `scripts/measure_codex.py` measures the Codex facts the plan relies on. It works in throwaway
    home folders under `~/.cockpit/measure/codex`, records no tokens, and is not installed.
  - `docs/gotchas.md` gains entries #57 to #64 with the results, measured on Codex 0.162.1 and
    0.163.0. Its opening now covers other vendors' CLIs.
  - No installed tool changes.
- Files: CHANGELOG.md, docs/gotchas.md, docs/multi_provider/requirements.md,
  docs/multi_provider/implementation_plan.md, scripts/measure_codex.py
- State impact: None
- Field mutations: None
- Performance: Not measured

## [2026-10-10] - Type: Config
- Change: The README ends with a credit line naming the author and linking the GitHub profile.
- Files: README.md, CHANGELOG.md
- State impact: None
- Field mutations: None
- Performance: Not measured

## [2026-10-09] - Type: Config
- Change: First public version of claude-cockpit. It contains the profile tools (`new-profile.sh`,
  `claude-as`, `claude-login`, `claude-profiles`), the usage readers (`claude-usage`,
  `claude-cockpit`), the browser fleet switch (`claude-switch`), the no-browser swap
  (`claude-swap`), the 5-hour window opener (`claude-ping`), the optional background watcher with
  its usage history and run-out forecast (`claude-autoswitch`, `claude-meters`), the read-only
  health check (`verify.sh`), an installer, an uninstaller, a watcher installer, five test files
  with their runner, a screen-capture harness (`tests/render_cockpit_states.py`, needs `pyte`), a
  leak scanner with a pre-push hook, and the documentation. The cockpit's top line tells "alerts
  only" apart from "armed to swap once", and the first launch with no accounts says how to add one. The no-browser
  swap ships turned off behind the `"swap_enabled"` setting in `~/.claude-profiles/autoswitch.json`:
  while it is `false`, `claude-swap <name>`, `claude-swap --undo` and `claude-autoswitch --arm`
  refuse, and the cockpit's number keys and `u` key use the browser sign-in. The path to the
  Claude Code binary can be overridden with the `COCKPIT_CLAUDE_BIN` environment variable; the
  default stays `~/.local/bin/claude`.
- Files: README.md, LICENSE, CHANGELOG.md, .gitignore, install.sh, uninstall.sh,
  install_watcher.sh, docs/how_it_works.md, docs/safety.md, docs/gotchas.md,
  tools/claude-as.sh, tools/claude-usage.py, tools/claude-cockpit.py, tools/claude-switch.py,
  tools/claude-swap.py, tools/claude-autoswitch.py, tools/claude-meters.py, tools/claude-ping.py,
  tools/claude-open-in-chrome.sh, tools/new-profile.sh, tools/verify.sh,
  config/autoswitch.example.json, config/chrome_profiles.example.json,
  launchd/autoswitch.plist.template, tests/run_dev.py, tests/test_autoswap.py,
  tests/test_claude_swap.py, tests/test_fleet_dir.py, tests/test_meters.py,
  tests/test_swap_enabled.py, tests/render_cockpit_states.py, scripts/scan_personal.py,
  scripts/pre_push_hook.sh
- State impact: Guard added. The no-browser swap, the cockpit's swap path and auto-swap arming
  now refuse unless `"swap_enabled"` is JSON `true`. A missing key counts as off, so an
  `autoswitch.json` written before this version disables the swap until the key is added.
- Field mutations: None
- Performance: Not measured
