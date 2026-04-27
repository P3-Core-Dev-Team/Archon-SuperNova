"""
__main__.py — Entry-point so the package is runnable via:

    python -m discovery

Delegates entirely to the Typer app defined in cli.py.
"""
from discovery.cli import app

if __name__ == "__main__":
    app()
