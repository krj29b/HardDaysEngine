"""GLSL 330 core shader resources for host presentation presets (ARCHITECTURE.md §13.4)."""

from __future__ import annotations

from functools import cache
from importlib.resources import files


@cache
def load_shader(filename: str) -> str:
    """Read a packaged GLSL shader source file from ``harddaysengine.render.shaders``."""
    return files(__package__).joinpath(filename).read_text(encoding="utf-8")
