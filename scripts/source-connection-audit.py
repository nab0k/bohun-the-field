#!/usr/bin/env python3
"""Explicit local-files-only entry point. No import-time I/O."""


def main(argv=None):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from collectors.source_registry.onboarding.audit import main as audit_main
    return audit_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
