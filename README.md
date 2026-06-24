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
