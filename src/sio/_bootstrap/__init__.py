"""Bundled bootstrap content for `sio init`.

This package's only purpose is to anchor `importlib.resources.files()`
so it can read the skills/ and rules/ trees that hatch's `force-include`
copies into this directory at wheel-build time.

Making this a real subpackage (i.e. shipping this `__init__.py`) instead
of a namespace package eliminates a class of failures where another
`sio/` directory on the user's `sys.path` would shadow the lookup and
silently return zero bootstrap files.
"""
