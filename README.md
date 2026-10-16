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

## Bundled skills

Every skill that documents a plugin belongs in that plugin's repo, versioned with
the code it describes — not loose in `~/.hermes/skills`. Put each one at:

```
<plugin repo>/skills/<skill-name>/SKILL.md     # frontmatter name: must equal <skill-name>
```

`SetupCLI` discovers these automatically:

| Command | Effect |
|---|---|
| `setup.py install` | symlinks `~/.hermes/skills/<skill-name>` → `skills/<skill-name>/` |
| `setup.py uninstall` | removes those links (only if they point into this repo) |
| `setup.py status` | shows each link's state |
| `setup.py audit` | check 13: SKILL.md present, name matches dir, link installed |

`PluginConfig(extra_skills=[...])` picks specific skills (`[]` disables);
`extra_skills_category="research"` nests links under `~/.hermes/skills/research/`.
Install refuses to overwrite a real directory at the link path — that is usually an
old unversioned copy; merge it into the repo, remove it, and re-run.

This is separate from `skill-stub/`, which only redirects to the plugin's own
`<key>:<key>` skill.

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
