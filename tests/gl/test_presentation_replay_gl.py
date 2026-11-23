"""Standalone OpenGL 3.3 replay hash invariance & --present verification tests (T13.9, ADR-053)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import moderngl
import numpy as np
import pytest

from harddaysengine.app.audio import AudioEngine
from harddaysengine.app.audit import AuditRig
from harddaysengine.app.campaign_audit import CampaignAudit, CampaignCheckpoint
from harddaysengine.app.game import GameSession
from harddaysengine.app.input import InputFrame, PlayerInput, ReplayRecording
from harddaysengine.assets.adf import DiskSet
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
