"""Immutable wizard dialogue and substitution positions (ARCHITECTURE.md §12.35)."""

from dataclasses import dataclass

from harddaysengine.sim.rewards import GiftKind, GiftResult


@dataclass(slots=True, frozen=True)
class WizardSpeech:
    lines: tuple[str, ...]
    field: tuple[int, int] | None = None

    def substitute(self, value: str) -> tuple[str, ...]:
        """Copy the speech, replacing the writable field through its terminator (§12.35)."""
        if self.field is None:
            return self.lines
        row, column = self.field
        lines = list(self.lines)
        lines[row] = lines[row][:column] + value
        return tuple(lines)


@dataclass(slots=True, frozen=True)
class WizardDialogue:
    greeting: WizardSpeech
    items: tuple[WizardSpeech, ...]
    gold: tuple[WizardSpeech, ...]
    stats: tuple[tuple[int, WizardSpeech], ...]
    curse: WizardSpeech
    omen: WizardSpeech
    farewells: tuple[tuple[int, WizardSpeech], ...]
    line_step: int
    recency: int

    def gift(self, result: GiftResult) -> WizardSpeech:
        """Select the already-rolled message; duplicate stat offsets keep their row (§12.35)."""
        if result.kind == GiftKind.CURSE:
            return self.curse
        if result.kind == GiftKind.STAT:
            if not 0 <= result.message < len(self.stats):
                raise ValueError("wizard stat message outside decoded table")
            offset, speech = self.stats[result.message]
            if offset != result.stat:
                raise ValueError("wizard result and decoded stat row disagree")
            return speech
        messages = self.items if result.kind == GiftKind.ITEM else self.gold
        if not 0 <= result.message < len(messages):
            raise ValueError("wizard message outside decoded table")
        return messages[result.message]

    def farewell(self, kind: int) -> WizardSpeech:
        speech = self.omen
        for key, candidate in self.farewells:
            if key == kind:
                speech = candidate
        return speech
