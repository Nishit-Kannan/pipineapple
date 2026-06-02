"""Tool layer — one module per external shell tool we wrap.

Each module knows how to invoke its tool, parse its output, and tear it
down cleanly. This is the only layer that should call ``subprocess`` or
shell out. Services and routes import functions from here; nothing in
``app.tools`` imports from ``app.services`` or ``app.routes``.
"""
