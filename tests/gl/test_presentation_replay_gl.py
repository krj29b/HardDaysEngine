"""Standalone OpenGL 3.3 replay hash invariance & --present verification tests (T13.9, ADR-053)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import moderngl
import numpy as np
import pytest
from PIL import Image

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.audit import AuditRig
from harddaysengine.app.campaign_audit import CampaignAudit, CampaignCheckpoint
from harddaysengine.app.game import GameSession
from harddaysengine.app.hd_host import HdHostPresenter
from harddaysengine.app.input import InputFrame, PlayerInput, ReplayRecording
from harddaysengine.assets.adf import DiskSet
from harddaysengine.assets.arena import composite_arena_background, decode_arena
from harddaysengine.assets.hd_pack import HdPack
from harddaysengine.assets.palette import palette_to_rgba_array
from harddaysengine.assets.picture import decode_picture, decode_picture_pack
from harddaysengine.render.presentation import (
    AspectMode,
    CropMode,
    DisplaySettings,
    FilterPreset,
    ScaleMode,
    SurroundMode,
    SurroundSettings,
)
from harddaysengine.render.renderer import HardDaysRenderer
from harddaysengine.render.scene import SceneRenderer, default_bitmap_font
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.gl


def _scripted_frames() -> tuple[InputFrame, ...]:
    frames: list[InputFrame] = []
    frames.extend(InputFrame() for _ in range(5))
    frames.append(InputFrame(players={KnightId.RED: PlayerInput(confirm=True)}))
    frames.extend(InputFrame() for _ in range(4))
    frames.append(InputFrame(players={KnightId.RED: PlayerInput(dy=1)}))
    frames.extend(InputFrame() for _ in range(3))
    frames.append(InputFrame(players={KnightId.RED: PlayerInput(confirm=True)}))
    frames.extend(InputFrame() for _ in range(5))
    frames.append(InputFrame(players={KnightId.RED: PlayerInput(dx=1)}))
    frames.extend(InputFrame() for _ in range(6))
    return tuple(frames)


def _run_presented_replay(
    ctx: moderngl.Context,
    renderer: HardDaysRenderer,
    scene: SceneRenderer,
    recording: ReplayRecording,
    settings: DisplaySettings,
    window_size: tuple[int, int],
) -> list[str]:
    audio = AudioEngine(headless=True)
    font = default_bitmap_font()
    out_tex = ctx.texture(window_size, 4)
    out_fbo = ctx.framebuffer(color_attachments=[out_tex])
    try:
        session = GameSession(seed=recording.seed, players=recording.players, audio=audio)
        hashes: list[str] = []
        for frame in recording.frames:
            session.step_tick(frame)
            before = session.state_hash()
            session.render_to_scene(scene, font)
            renderer.present_to_framebuffer(out_fbo, window_size, settings=settings)
            after = session.state_hash()
            assert after == before
            hashes.append(after)
        return hashes
    finally:
        out_fbo.release()
        out_tex.release()
        audio.close()


def test_replay_hashes_identical_under_every_preset_and_size() -> None:
    """One replay run under every FilterPreset & surround mode produces identical tick hashes."""
    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    try:
        recording = ReplayRecording(
            seed=0x13572468,
            players=(KnightId.RED,),
            frames=_scripted_frames(),
        )
        baseline: list[str] | None = None
        variants: list[tuple[DisplaySettings, tuple[int, int]]] = [
            (DisplaySettings(filter=preset), size)
            for preset in FilterPreset
            for size in ((1920, 1080), (1366, 768))
        ]
        variants.extend(
            [
                (
                    DisplaySettings(
                        filter=FilterPreset.CRT,
                        scale_mode=ScaleMode.FIT,
                        aspect=AspectMode.PAL,
                        crop=CropMode.PLAYFIELD,
                        surround=SurroundSettings(mode=SurroundMode.BLUR),
                    ),
                    (2560, 1080),
                ),
                (
                    DisplaySettings(
                        filter=FilterPreset.SMOOTH,
                        scale_mode=ScaleMode.SHARP_BILINEAR,
                        aspect=AspectMode.PAL,
                        crop=CropMode.PLAYFIELD,
                        surround=SurroundSettings(mode=SurroundMode.SOLID),
                    ),
                    (2560, 1080),
                ),
                (
                    DisplaySettings(
                        filter=FilterPreset.SCALEFX,
                        scale_mode=ScaleMode.FIT,
                        aspect=AspectMode.PAL,
                        crop=CropMode.PLAYFIELD,
                        surround=SurroundSettings(mode=SurroundMode.BLUR),
                    ),
                    (2560, 1080),
                ),
            ]
        )
        for settings, win_size in variants:
            hashes = _run_presented_replay(ctx, renderer, scene, recording, settings, win_size)
            if baseline is None:
                baseline = hashes
            else:
                assert hashes == baseline
        assert baseline is not None and len(baseline) == len(recording.frames)
    finally:
        scene.release()
        renderer.release()
        ctx.release()


@pytest.mark.adf
def test_real_disk_campaign_replay_identical_under_every_preset(adf_dir: Path) -> None:
    """Real-disk campaign replay produces identical checkpoint and final hashes across presets."""
    disks = DiskSet.from_directory(adf_dir)
    audit = CampaignAudit(disks)
    checkpoints: list[CampaignCheckpoint] = []
    for name in audit.run():
        checkpoints.append(
            CampaignCheckpoint(name, audit.session.tick_count, audit.session.state_hash())
        )
    recording = audit.recording()
    by_tick = {point.tick: point for point in checkpoints}

    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    font = default_bitmap_font()
    try:
        final_hashes: dict[FilterPreset, str] = {}
        for preset in FilterPreset:
            win_size = (
                (1920, 1080) if preset in (FilterPreset.FAITHFUL, FilterPreset.CRT) else (1366, 768)
            )
            settings = DisplaySettings(
                filter=preset,
                scale_mode=ScaleMode.SHARP_BILINEAR,
                aspect=AspectMode.PAL,
                crop=CropMode.PLAYFIELD,
            )
            out_tex = ctx.texture(win_size, 4)
            out_fbo = ctx.framebuffer(color_attachments=[out_tex])
            audio = AudioEngine(headless=True)
            try:
                session = GameSession(
                    seed=recording.seed,
                    players=recording.players,
                    disk_set=disks,
                    audio=audio,
                )
                for tick, frame in enumerate(recording.frames, 1):
                    session.step_tick(frame)
                    point = by_tick.get(tick)
                    if point is not None:
                        assert session.state_hash() == point.state_hash
                        session.render_to_scene(scene, font)
                        renderer.present_to_framebuffer(out_fbo, win_size, settings=settings)
                        assert session.state_hash() == point.state_hash
                final_hashes[preset] = session.state_hash()
            finally:
                audio.close()
                out_fbo.release()
                out_tex.release()

        assert len(set(final_hashes.values())) == 1
    finally:
        scene.release()
        renderer.release()
        ctx.release()


@pytest.mark.adf
def test_audit_rig_present_renders_real_preset_at_target_size(adf_dir: Path) -> None:
    """AuditRig --present path renders non-flat presented frames at exact requested WxH."""
    for preset, size in (
        (FilterPreset.CRT, (1920, 1080)),
        (FilterPreset.SMOOTH, (1366, 768)),
        (FilterPreset.SCALEFX, (1920, 1080)),
    ):
        rig = AuditRig(adf_dir, display=DisplaySettings(filter=preset), present_size=size)
        try:
            session = rig.new_session()
            for _ in range(5):
                session.step_tick(InputFrame())
            session.step_tick(InputFrame(players={KnightId.RED: PlayerInput(skip_intro=True)}))
            for _ in range(40):
                session.step_tick(InputFrame())
            shot = rig.capture(session, f"menu_{preset.value}")
            assert shot.rgb.shape == (size[1], size[0], 3)
            assert len(np.unique(shot.rgb.reshape(-1, 3), axis=0)) > 10
        finally:
            rig.release()


def _save_upscaled_rgba(
    pixels: np.ndarray,
    palette: tuple[tuple[int, int, int], ...],
    path: Path,
    scale: int,
) -> None:
    lut = palette_to_rgba_array(palette, size=32, zero_transparent=False)
    rgba = lut[pixels[:200, :320] & 0x1F]
    img = Image.fromarray(rgba, mode="RGBA").resize(
        (320 * scale, 200 * scale), Image.Resampling.BICUBIC
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _build_campaign_hd_pack(disks: DiskSet, pack_dir: Path, *, scale: int = 2) -> HdPack:
    """Create a real-disk-derived temporary 2x HD pack covering key campaign scenes."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "pack.toml").write_text(
        "schema_version = 1\n"
        'name = "Campaign Replay QA"\n'
        'version = "1.0"\n'
        f"scale = {scale}\n"
        'default_mode = "rgba"\n'
        'fallback_filter = "nearest"\n',
        encoding="utf-8",
    )
    test_pics = decode_picture_pack(disks.read_file("test"))
    _save_upscaled_rgba(
        test_pics[8].pixels, test_pics[8].palette, pack_dir / "pictures" / "test_8.png", scale
    )
    for pic_name in ("ch.piv", "highwood.piv"):
        pic = decode_picture(disks.read_file(pic_name))
        _save_upscaled_rgba(
            pic.pixels, pic.palette, pack_dir / "pictures" / f"{pic_name}.png", scale
        )
    arena = decode_arena("fol1.t", disks.read_file("fol1.t"))
    comp = composite_arena_background(arena, test_pics)
    _save_upscaled_rgba(comp.pixels, comp.palette, pack_dir / "arenas" / "fol1.t.png", scale)
    return HdPack.load(pack_dir)


@pytest.mark.adf
def test_real_disk_campaign_replay_identical_in_classic_and_hd(
    adf_dir: Path, tmp_path: Path
) -> None:
    """Full 1,952-tick campaign replay hashes stay identical across Classic, HD, and F7 toggles."""
    disks = DiskSet.from_directory(adf_dir)
    audit = CampaignAudit(disks)
    checkpoints: list[CampaignCheckpoint] = []
    for name in audit.run():
        checkpoints.append(
            CampaignCheckpoint(name, audit.session.tick_count, audit.session.state_hash())
        )
    recording = audit.recording()
    by_tick = {point.tick: point for point in checkpoints}
    pack = _build_campaign_hd_pack(disks, tmp_path / "campaign_hd_pack", scale=2)

    ctx = moderngl.create_standalone_context(require=330)
    renderer = HardDaysRenderer(ctx)
    scene = SceneRenderer(renderer)
    font = default_bitmap_font()
    hd_host = HdHostPresenter(pack)
    win_size = (1280, 720)
    out_tex = ctx.texture(win_size, 4)
    out_fbo = ctx.framebuffer(color_attachments=[out_tex])
    audio = AudioEngine(headless=True)
    try:
        session = GameSession(
            seed=recording.seed,
            players=recording.players,
            disk_set=disks,
            audio=audio,
        )
        hd_enabled = True
        hd_rendered_count = 0
        for idx, (tick, frame) in enumerate(enumerate(recording.frames, 1)):
            session.step_tick(frame)
            point = by_tick.get(tick)
            if point is None:
                continue
            assert session.state_hash() == point.state_hash
            # Exercise live F7 toggles mid-campaign while verifying HD + Classic presentation.
            if idx % 3 == 0:
                hd_enabled = not hd_enabled
            compositor = hd_host.ensure_active(ctx, enabled=hd_enabled)
            hd_tex = (
                hd_host.render_session_hd(session, compositor) if compositor is not None else None
            )
            if hd_tex is not None:
                hd_rendered_count += 1
            else:
                session.render_to_scene(scene, font)
            preset = FilterPreset.CRT if hd_enabled else FilterPreset.FAITHFUL
            renderer.present_to_framebuffer(
                out_fbo,
                win_size,
                settings=DisplaySettings(filter=preset),
                hd_tex=hd_tex,
                hd_scale=hd_host.scale if hd_tex is not None else 1,
            )
            assert session.state_hash() == point.state_hash

        assert hd_rendered_count > 0
        assert session.state_hash() == checkpoints[-1].state_hash
        compositor = hd_host.compositor
        hd_host.release()
        if compositor is not None:
            assert compositor.active_texture_count == 0
    finally:
        hd_host.release()
        audio.close()
        out_fbo.release()
        out_tex.release()
        scene.release()
        renderer.release()
        ctx.release()
