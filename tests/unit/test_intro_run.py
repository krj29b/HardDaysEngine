"""The intro player on synthetic plans: series, picture setup, forward wipe, loader (§12.42)."""

from __future__ import annotations

from harddaysengine.sim.ending import SCREEN, WORK, EndingInputs
from harddaysengine.sim.ending_types import (
    Buffer,
    Cell,
    ClassTweak,
    Load,
    Overlay,
    PictureLoad,
    ScriptHunk,
    Series,
    SpawnSide,
    Store,
    TextFont,
)
from harddaysengine.sim.intro import IntroRun
from harddaysengine.sim.intro_types import (
    ForwardWipe,
    IntroLoad,
    IntroPlan,
    IntroStep,
    PictureCopy,
    PictureSetup,
    ProgressSpec,
)
from harddaysengine.sim.turn_rules import TextLine


class _RecordingIntroCanvas:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def named(self, kind: str) -> list[tuple[object, ...]]:
        return [c for c in self.calls if c[0] == kind]

    def flip(self) -> None:
        self.calls.append(("flip",))

    def copy(self, source: int, target: int) -> None:
        self.calls.append(("copy", source, target))

    def clear(self, target: int) -> None:
        self.calls.append(("clear", target))

    def draw(self, target: int, slot: int, frame: int, x: int, y: int, mirrored: bool) -> None:
        self.calls.append(("draw", target, slot, frame, x, y, mirrored))

    def overlay(self, frame: int, x: int, y: int) -> None:
        self.calls.append(("overlay", frame, x, y))

    def text(self, target: int, lines: tuple[TextLine, ...]) -> None:
        self.calls.append(("text", target, len(lines)))

    def message(self, target: int) -> None:
        self.calls.append(("message", target))

    def wipe(self, target: int, progress: int) -> None:
        self.calls.append(("wipe", target, progress))

    def mindscape(self, target: int) -> None:
        self.calls.append(("mindscape", target))

    def font_cel(self, target: int, frame: int, x: int, y: int) -> None:
        self.calls.append(("font_cel", target, frame, x, y))


def _make_plan(
    steps: tuple[IntroStep, ...], script: bytes = b"\x00\x01\x00\x00\x00\x00\xff\xff"
) -> IntroPlan:
    progress = ProgressSpec(
        gold_entries=((5, 0x0000), (9, 0x0FED), (10, 0x0DC9), (11, 0x0B95), (12, 0x0842)),
        white_word=0x0FFF,
        black_word=0x0000,
        tail_period=2,
        flash_period=1,
        logo_frame=0x49,
        logo_x=9,
        logo_y=60,
        logo_wait=2,
        flash_wait=1,
        credits=((TextLine("created by", 0, 85, 3),),),
    )
    load = IntroLoad(
        mindscape="mindscape",
        mindscape_scratch=Buffer.P3,
        pictures=(
            PictureLoad("bg1a.piv", Buffer.P2, 0, False, False),
            PictureLoad("bg1c.piv", Buffer.P3, -1, False, False),
            PictureLoad("bg1b.piv", Buffer.P4, -1, False, False),
            PictureLoad("bg4.piv", Buffer.P5, 3, False, False),
            PictureLoad("bg5a.piv", Buffer.P6, 4, False, True),
        ),
        cels=((0, "au1.cel"),),
        cels_with_progress=1,
        overlay="ov1.cel",
        stile="intro.stile",
        stile_bytes=1000,
        music="music.cmp",
        music_bytes=100,
        progress=progress,
    )
    return IntroPlan(
        flag_bit=0x80,
        steps=steps,
        load=load,
        class_tweaks=(ClassTweak(4, (10, 20, 30, 40)),),
        class_first=8,
        spawns=(
            SpawnSide(160, 0, 100, 1, Cell.LEFT_DEPTH),
            SpawnSide(120, 0, 100, 3, Cell.RIGHT_DEPTH),
        ),
        pair_frames=4,
        caption_entries=((1, 0x800),),
        fade_period=2,
        fade_wait=4,
        palette_period=2,
        next_program="Mog",
        overlay=Overlay((), 0, 2),
        callbacks=(),
        scripts=ScriptHunk(script, ()),
        font=TextFont("bold.f", 0, 2, 320),
        job_slots=8,
        stage_period=2,
        message="message.piv",
        palette_cell=tuple(range(32)),
    )


def _make_inputs() -> EndingInputs:
    pal = tuple(0x8000 | i for i in range(32))
    palettes = dict.fromkeys(
        ("mindscape", "bg1a.piv", "bg1c.piv", "bg1b.piv", "bg4.piv", "bg5a.piv"),
        pal,
    )
    return EndingInputs(palettes=palettes, message=pal, widths={0: (16, 16, 16)})


def _finish(run: IntroRun) -> int:
    while not run.advance():
        pass
    return run.tick


def test_series_spawns_on_left_and_runs_remaining_counted_frames() -> None:
    plan = _make_plan((Store(Cell.SPEED, 1), Series((0, 0), first_frame=2)))
    canvas = _RecordingIntroCanvas()
    run = IntroRun(plan, _make_inputs(), canvas)
    run.tables[0] = (0,) * 32
    # pair_frames=4, first_frame=2 -> 2 passes per script * 2 scripts = 4 flips, 4 ticks
    assert _finish(run) == 4
    assert len(canvas.named("flip")) == 4
    draws = canvas.named("draw")
    assert all(d[4] == 160 and d[6] is False for d in draws)


def test_picture_setup_copies_buffers_and_palette_tables() -> None:
    setup = PictureSetup((PictureCopy(Buffer.P5, Buffer.P2, 3, 0),))
    plan = _make_plan((setup,))
    canvas = _RecordingIntroCanvas()
    run = IntroRun(plan, _make_inputs(), canvas)
    run.tables[3] = (0x111,) * 32
    assert _finish(run) == 0
    assert canvas.named("copy") == [("copy", int(Buffer.P5), int(Buffer.P2))]
    assert run.tables[0] == (0x111,) * 32


def test_forward_wipe_scrolls_to_limit_and_triggers_music_at_step_4() -> None:
    wipe = ForwardWipe(
        entries=((9, 0x0A00),),
        thresholds=(4, 20),
        steps=(2, 4),
        music_step=4,
        limit=10,
    )
    plan = _make_plan((Store(Cell.SPEED, 1), wipe))
    canvas = _RecordingIntroCanvas()
    run = IntroRun(plan, _make_inputs(), canvas)
    run.tables[0] = (0,) * 32
    assert _finish(run) == 4
    progress_steps = [c[2] for c in canvas.named("wipe")]
    # 0 <= 4 -> +2=2; 2 <= 4 -> +2=4; 4 <= 4 -> +2=6; 6 <= 20 -> +4=10 (music triggered!)
    assert progress_steps == [2, 4, 6, 10]
    assert run.music is True
    assert run.tables[0][9] == 0x0A00
    assert canvas.calls[-2:] == [("copy", SCREEN, WORK), ("copy", SCREEN, 0)]


def test_intro_load_runs_mindscape_banner_and_credits_and_snapshots() -> None:
    plan = _make_plan((Load(),))
    canvas = _RecordingIntroCanvas()
    run = IntroRun(plan, _make_inputs(), canvas, flags=0x10)
    ticks = _finish(run)
    assert ticks > 0
    assert canvas.named("mindscape") == [("mindscape", SCREEN)]
    assert canvas.named("font_cel") == [("font_cel", WORK, 0x49, 9, 60)]
    assert canvas.named("text") == [("text", WORK, 1)]
    assert run.page == 1
    assert run.tables[4][8:12] == (10, 20, 30, 40)
    snap = run.snapshot()
    assert snap["page"] == 1 and snap["done"] is True
