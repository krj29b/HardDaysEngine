"""Draw the live display controls after the scene's presentation pass (§13.10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import moderngl

    from harddaysengine.render.renderer import HardDaysRenderer
    from harddaysengine.render.scene import BitmapFont

_WIDTH = 216
_ROW_HEIGHT = 14
_ROWS_TOP = 27
_COLORS = np.array(
    [(20, 24, 31), (117, 133, 149), (235, 241, 245), (45, 58, 71), (255, 214, 131)],
    dtype=np.uint8,
)


class DisplayPanel:
    """A small RGB panel positioned independently of the game's viewport and palette."""

    def __init__(self, renderer: HardDaysRenderer, font: BitmapFont) -> None:
        self._renderer = renderer
        self._font = font
        self._texture: moderngl.Texture | None = None
        self._content: tuple[tuple[tuple[str, str], ...], int] | None = None

    def _draw(self, rows: tuple[tuple[str, str], ...], selected: int) -> moderngl.Texture:
        height = _ROWS_TOP + len(rows) * _ROW_HEIGHT + 31
        texture = self._texture
        if texture is None or texture.size != (_WIDTH, height):
            self.release()
            texture = self._renderer.ctx.texture((_WIDTH, height), 3)
            self._texture = texture
        if self._content == (rows, selected):
            return texture

        pixels = np.zeros((height, _WIDTH), dtype=np.uint8)
        pixels[[0, -1], :] = 1
        pixels[:, [0, -1]] = 1
        font = self._font
        font.draw_text(pixels, "Display preview", 10, 10, color_index=4)
        for index, (label, value) in enumerate(rows):
            top = _ROWS_TOP + index * _ROW_HEIGHT
            color = 4 if index == selected else 2
            if index == selected:
                pixels[top : top + _ROW_HEIGHT, 6:-6] = 3
            font.draw_text(pixels, label, 10, top + 3, color_index=color)
            right = _WIDTH - 10 - len(value) * font.char_width
            font.draw_text(pixels, value, right, top + 3, color_index=color)
        font.draw_text(pixels, "UP/DOWN  LEFT/RIGHT", 10, height - 25, color_index=1)
        font.draw_text(pixels, "ENTER APPLY  ESC CANCEL", 10, height - 13, color_index=2)
        texture.write(_COLORS[pixels[::-1]].tobytes(), alignment=1)
        self._content = (rows, selected)
        return texture

    def draw(
        self,
        target: moderngl.Framebuffer,
        size: tuple[int, int],
        rows: tuple[tuple[str, str], ...],
        selected: int,
    ) -> None:
        """Draw the panel in the top-right of a window or test framebuffer."""
        texture = self._draw(rows, selected)
        width, height = size
        integer_scale = max(1, min(width // 420, height // 300))
        margin = min(8 * integer_scale, min(width, height) // 16)
        scale = min(
            integer_scale, (width - 2 * margin) / _WIDTH, (height - 2 * margin) / texture.height
        )
        panel_w = max(1, round(_WIDTH * scale))
        panel_h = max(1, round(texture.height * scale))
        rect = (width - margin - panel_w, height - margin - panel_h, panel_w, panel_h)
        self._renderer.blit_screen_texture(target, size, texture, rect)

    def release(self) -> None:
        """Release the current panel texture."""
        if self._texture is not None:
            self._texture.release()
            self._texture = None
            self._content = None
