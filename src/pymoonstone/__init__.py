"""pymoonstone: clean-room reimplementation of Moonstone: A Hard Days Knight (1991).

Layers (enforced by `.importlinter` and `tests/unit/test_layering.py`):
``cli -> app -> render -> assets -> sim``. ``sim`` is the pure, deterministic 50 Hz kernel.
"""

__version__ = "0.1.0"
