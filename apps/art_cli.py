import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.creative_claw_cli import main as creative_claw_main


def main() -> int:
    """Compatibility wrapper for the legacy CLI entrypoint."""
    return creative_claw_main(["chat", "cli", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
