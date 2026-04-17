"""Compatibility shim for the mailbox vectorization entrypoint."""

from scripts.vectorize_mailbox import main


if __name__ == "__main__":
    raise SystemExit(main())
