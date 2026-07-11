"""Command-line entrypoint for inspecting the OS foundation."""

from __future__ import annotations

import json

from .providers import DEFAULT_PROVIDER_SPECS
from .runtime import RuntimeSpec
from .sandboxes import default_sandboxes


def main() -> None:
    """Print the currently planned foundation capabilities as JSON."""

    payload = {
        "runtime": RuntimeSpec().to_dict(),
        "providers": [spec.to_dict() for spec in DEFAULT_PROVIDER_SPECS],
        "sandboxes": [spec.to_dict() for spec in default_sandboxes()],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
