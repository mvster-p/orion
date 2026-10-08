import sys


def main() -> int:
    try:
        import rich  # noqa: F401
    except ImportError:
        sys.stderr.write("ORION needs rich.\nInstall it with: python -m pip install -r requirements.txt\n")
        return 1
    from .cli import main as run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
