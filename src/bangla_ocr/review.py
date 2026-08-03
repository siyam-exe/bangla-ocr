from __future__ import annotations

import webbrowser
from pathlib import Path

from flask import Flask

from .application import create_application


def create_review_app(book_root: Path) -> Flask:
    """Compatibility entry point for opening one existing book workspace."""
    resolved = book_root.resolve()
    return create_application(
        output_root=resolved.parent,
        single_book_root=resolved,
    )


def run_review_server(
    book_root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    app = create_review_app(book_root)
    if open_browser:
        webbrowser.open(
            f"http://{host}:{port}/books/{book_root.resolve().name}"
        )
    app.run(
        host=host,
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )
