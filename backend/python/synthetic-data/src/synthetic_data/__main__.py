"""Entry point enabling `python -m synthetic_data`."""
import sys
from synthetic_data.cli import app

if __name__ == "__main__":
    # Support both: `python -m synthetic_data generate ...`
    # and (legacy): `python -m synthetic_data ...`
    app()
