"""Input-only campaign acceptance route (ARCHITECTURE.md §12.41)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harddaysengine.app.fight_audit import fight_host_input
from harddaysengine.app.game import GameScene, GameSession
from harddaysengine.app.input import InputFrame, PlayerInput, ReplayRecording
from harddaysengine.sim.m68k import s16
from harddaysengine.sim.overworld import list_rows
from harddaysengine.sim.types import KnightId

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from harddaysengine.assets.adf import DiskSet
    from harddaysengine.sim.sheet_types import SheetRegion

CAMPAIGN_SEED = 0x2468ACE1
CAMPAIGN_PLAYERS = tuple(KnightId)


@dataclass(slots=True, frozen=True)
class CampaignCheckpoint:
    """A named VBL and campaign replay state hash, stored only in local audit output."""

    name: str
    tick: int
    state_hash: str


def verify_campaign(
    disks: DiskSet, recording: ReplayRecording, checkpoints: Sequence[CampaignCheckpoint]
) -> str:
    """Replay serialized inputs in a fresh session and verify every checkpoint (§12.41)."""
    session = GameSession(seed=recording.seed, players=recording.players, disk_set=disks)
    by_tick = {point.tick: point for point in checkpoints}
    if not checkpoints or max(by_tick) != len(recording.frames):
        raise ValueError("campaign checkpoints must include the final recorded tick")
    for tick, frame in enumerate(recording.frames, 1):
        session.step_tick(frame)
        point = by_tick.get(tick)
        if point is not None and session.state_hash() != point.state_hash:
            raise ValueError(f"campaign replay diverged at {point.name} (tick {tick})")
    return session.state_hash()


def _direction(target: int, current: int) -> int:
    return (target > current) - (target < current)


class CampaignAudit:
    """Record a fresh campaign using only input frames; yield review checkpoints (§12.41)."""

    def __init__(self, disks: DiskSet) -> None:
        self.session = GameSession(seed=CAMPAIGN_SEED, players=CAMPAIGN_PLAYERS, disk_set=disks)
        self.frames: list[InputFrame] = []

    def recording(self) -> ReplayRecording:
        return ReplayRecording(CAMPAIGN_SEED, CAMPAIGN_PLAYERS, tuple(self.frames))

    def tick(self, frame: InputFrame | None = None) -> None:
        if frame is None:
            frame = InputFrame()
        self.frames.append(frame)
        self.session.step_tick(frame)

    def press(self, *, dx: int = 0, dy: int = 0, fire: bool = False, confirm: bool = False) -> None:
        self.tick(
            InputFrame(
                players={KnightId.RED: PlayerInput(dx=dx, dy=dy, fire=fire, confirm=confirm)}
            )
        )

    def tap(self, *, dx: int = 0, dy: int = 0, fire: bool = False, confirm: bool = False) -> None:
        self.press(dx=dx, dy=dy, fire=fire, confirm=confirm)
        self.tick()

    def until(self, ready: Callable[[], bool], limit: int = 1000) -> None:
        for _ in range(limit):
            if ready():
                return
            self.tick()
        raise RuntimeError(f"campaign wait expired in {self.session.scene.name}")

    def settle(self) -> None:
        self.until(lambda: not self.session.palette_fx.is_fading and not self.session.blocking_fade)

    def walk(self, x: int, y: int) -> None:
        world = self.session.overworld
        for _ in range(2000):
            if self.session.scene != GameScene.OVERWORLD or world.turn_index != 0:
                raise RuntimeError("campaign walk exhausted its turn")
            actor = world.current_actor
            if abs(actor.map_x - x) < 2 and abs(actor.map_y - y) < 2:
                return
            self.press(dx=_direction(x, actor.map_x), dy=_direction(y, actor.map_y))
        raise RuntimeError("campaign walk did not reach its destination")

    def arrive(self, kind: int, key: int | None = None) -> None:
        world = self.session.overworld
        self.press(fire=True)
        self.press(fire=True)
        self.tick()
        if world.menu:
            index = next(
                i
                for i, row in enumerate(list_rows(world))
                if row.kind == kind and (key is None or row.key == key)
            )
            self.tick(InputFrame(key=ord("1") + index))
            self.tick()
        if self.session.scene == GameScene.OVERWORLD:
            raise RuntimeError("campaign arrival did not open its caller")

    def pointer(self, x: int, y: int) -> None:
        screen = self.session.sheet_screen or self.session.town_screen
        if screen is None:
            raise RuntimeError("campaign pointer has no screen")
        for _ in range(400):
            state = screen.state
            if abs(state.pointer_x - x) < 2 and abs(state.pointer_y - y) < 2:
                return
            self.press(dx=_direction(x, state.pointer_x), dy=_direction(y, state.pointer_y))
        raise RuntimeError("campaign pointer did not reach its region")

    def click(self, region: SheetRegion) -> None:
        self.pointer(region.x + 2, region.y + 2)
        self.tap(fire=True)

    def close_sheet(self) -> None:
        screen = self.session.sheet_screen
        if screen is None:
            raise RuntimeError("campaign has no sheet to close")
        self.click(next(r for r in screen.state.regions if r.id == 7))
        self.settle()

    def next_round(self) -> None:
        for _ in range(4):
            if self.session.scene == GameScene.NEW_DAY:
                self.until(lambda: self.session.overworld.turns.wait_stage == 1)
                return
            self.settle()
            self.tick(InputFrame(key=ord("E")))
            self.tick()
        if self.session.scene != GameScene.NEW_DAY:
            raise RuntimeError("campaign turn order did not reach Next Day")
        self.until(lambda: self.session.overworld.turns.wait_stage == 1)

    def acknowledge_round(self) -> None:
        self.until(lambda: self.session.overworld.turns.wait_stage == 1)
        self.tap(fire=True)
        self.until(lambda: self.session.scene == GameScene.OVERWORLD)
        self.settle()

    def run(self) -> Iterator[str]:
        """Yield checkpoints along one continuous, unmodified campaign (§12.41)."""
        self.tap(confirm=True)
        for _ in range(3):
            self.tap(dy=1)
        self.tap(confirm=True)
        yield "knight_selection"
        for _ in range(4):
            self.tap(confirm=True)
        self.settle()
        yield "new_campaign"
        yield from self.fight_lair()
        yield from self.loot_and_shop()
        for _ in range(4):
            self.next_round()
            if self.session.overworld.day != 0:
                break
            self.acknowledge_round()
        else:
            raise RuntimeError("campaign day did not advance after four rounds")
        yield "day_and_moon_advanced"
        self.acknowledge_round()
        yield "campaign_continues"

    def fight_lair(self) -> Iterator[str]:
        """Walk into the original lair and play its natural waves to settlement (§12.41)."""
        world = self.session.overworld
        lair = world.lairs[19]
        self.walk(lair.map_x, lair.map_y)
        self.arrive(2, 25)
        yield "lair_entry"
        for tick in range(18000):
            if self.session.scene != GameScene.COMBAT:
                break
            self.tick(fight_host_input(self.session))
            if tick == 119:
                yield "lair_combat"
        if self.session.scene != GameScene.STATUS or self.session.last_defeat_mask:
            raise RuntimeError("campaign did not win its natural lair fight")
        if s16(lair.creatures_left) > 0:
            raise RuntimeError("campaign left creatures in its lair")
        self.settle()
        yield "lair_won"

    def loot_and_shop(self) -> Iterator[str]:
        """Take real loot, then buy through Highwood's market and return (§12.41)."""
        world = self.session.overworld
        screen = self.session.sheet_screen
        if screen is None or screen.state.number != 2:
            raise RuntimeError("campaign victory did not open loot sheet 2")
        for field in (74, 0):
            self.click(next(r for r in screen.state.regions if r.side == 1 and r.field == field))
        yield "loot_taken"
        self.close_sheet()
        self.next_round()
        self.acknowledge_round()
        town = next(n for n in world.data().nodes if n.kind == 25)
        self.walk(town.x + 5, town.y - 4)
        self.arrive(25)
        self.settle()
        yield "highwood"
        yield from self.visit_market()
        self.purchase_potion()
        yield "potion_purchased"
        self.close_sheet()
        self.leave_town()

    def visit_market(self) -> Iterator[str]:
        """Select the registered market button with ordinary pointer inputs (§12.41)."""
        town_screen = self.session.town_screen
        if town_screen is None:
            raise RuntimeError("campaign did not enter Highwood")
        market = next(b for b in town_screen.data.buttons if b.id == 4)
        self.pointer(market.x + 2, market.y + 2)
        self.tap(fire=True)
        self.settle()
        yield "market"

    def purchase_potion(self) -> None:
        """Verify the persistent record change against the runtime price (§12.41)."""
        world = self.session.overworld
        screen = self.session.sheet_screen
        if screen is None or screen.state.number != 6 or screen.rules.shop is None:
            raise RuntimeError("campaign market did not open sheet 6")
        actor = world.current_actor
        gold, count = actor.gold, world.inventories[actor.inventory].byte(0)
        self.click(next(r for r in screen.state.regions if r.side == 1 and r.field == 0))
        if (actor.gold, world.inventories[actor.inventory].byte(0)) != (
            gold - screen.rules.shop.prices[0],
            count + 1,
        ):
            raise RuntimeError("campaign market purchase did not settle at its decoded price")

    def leave_town(self) -> None:
        """Close Highwood through its original exit region (§12.41)."""
        town_screen = self.session.town_screen
        if town_screen is None:
            raise RuntimeError("campaign lost its town caller")
        exit_button = next(b for b in town_screen.data.buttons if b.id == 5)
        self.pointer(exit_button.x + 2, exit_button.y + 2)
        self.tap(fire=True)
        self.until(lambda: self.session.scene == GameScene.OVERWORLD)
        self.settle()
