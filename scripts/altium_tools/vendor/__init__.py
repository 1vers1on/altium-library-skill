"""Bundled, dependency-free replacements for the toolkit's former third-party imports.

Nothing in this package imports anything outside the Python standard library, so the
toolkit runs on an offline machine with no ``pip install`` step. Three modules:

``cfb``       Compound File Binary (OLE2) reader, replacing ``olefile``.
``altium``    SchLib/PcbLib readers built on ``cfb``, replacing ``pyaltiumlib``.
``raster``    RGB canvas, vector-stroke text and a PNG encoder, replacing ``Pillow``.

The readers are written from the container and record layouts documented in
``references/binary-format.md`` rather than from the writer's own routines, so a
readback check still compares two independent implementations of the same format.
They are deliberately strict: an unexpected byte is an error, never a shrug.
"""
