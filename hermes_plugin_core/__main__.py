"""Entry point for `python -m hermes_plugin_core scaffold <name>`."""
import sys


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "scaffold":
        print("Usage: python -m hermes_plugin_core scaffold <plugin-name> [--output-dir DIR]")
        sys.exit(1)
    # Remove the "scaffold" sub-command so argparse in scaffold.main() sees only the plugin name
    sys.argv.pop(1)
    from hermes_plugin_core.scaffold import main as scaffold_main
    scaffold_main()


if __name__ == "__main__":
    main()
