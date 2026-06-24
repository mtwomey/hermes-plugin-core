# hermes-plugin-core

Shared infrastructure for Hermes native plugins. Provides setup CLI machinery,
keychain helpers, logging, config.yaml management, compliance auditing, test harness,
and a scaffold generator for new plugins.

## Install (into Hermes venv)

```bash
~/.hermes/hermes-agent/venv/bin/pip install git+https://github.com/mtwomey/hermes-plugin-core
```

## Usage

Each plugin's `setup.py` imports from this package — see the `hermes-plugin-authoring` skill in Hermes.

## Plugin Sync

The package also installs `hermes-plugin-sync`, a safe updater that discovers locally installed Git-backed Hermes plugins under `$HERMES_HOME/plugins`, fast-forwards each one from `origin/main`, runs `install`/`test`/`audit`, and rolls back failed updates.

```bash
hermes-plugin-sync
hermes-plugin-sync --json
hermes-plugin-sync --dry-run
hermes-plugin-sync --plugins bigtime,imap
```

Safety behavior:

- skips dirty repos
- skips non-main branches
- continues after per-plugin failures
- rolls back post-pull validation failures
- reports that a Hermes restart is required when code changes land
