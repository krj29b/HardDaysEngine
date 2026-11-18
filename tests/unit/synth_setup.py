"""Shared synthetic synth data and a recording Paula for the sound tests (ARCHITECTURE.md §6.2)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from harddaysengine.app.paula import SET
from harddaysengine.assets.synth_data import SynthData, SynthInstrument

TABLE = 0x700
PITCH = 0x100
VIBRATO = 0x300
ENVELOPE = 0x380
STREAMS = 0x400
STACKS = (0x060, 0x080, 0x0A0, 0x0C0)
PARK = 0
REST = 0x40
START_PERIOD = 0x123
ATTENUATION = 16


@dataclass
class FakePaula:
    """Records every register write; keeps the interrupt bits like the chip."""

    log: list[tuple[object, ...]] = field(default_factory=list)
    requests: int = 0
    enabled: int = 0

    def dmacon(self, value: int) -> None:
        self.log.append(("dmacon", value))

    def intena(self, value: int) -> None:
        self.log.append(("intena", value))
        bits = value & 0x7FFF
        self.enabled = self.enabled | bits if value & SET else self.enabled & ~bits

    def intreq(self, value: int) -> None:
        self.log.append(("intreq", value))
        bits = value & 0x7FFF
        self.requests = self.requests | bits if value & SET else self.requests & ~bits

    def location(self, channel: int, value: int) -> None:
        self.log.append(("location", channel, value))

    def length(self, channel: int, words: int) -> None:
        self.log.append(("length", channel, words))

    def period(self, channel: int, value: int) -> None:
        self.log.append(("period", channel, value))

    def volume(self, channel: int, value: int) -> None:
        self.log.append(("volume", channel, value))


INSTRUMENTS = (
    SynthInstrument(loop=-1, loop_offset=0, words=4, sample=8, wave=True, pitch=PITCH),
    SynthInstrument(loop=1, loop_offset=0, words=100, sample=0x40, wave=False, pitch=PITCH),
    SynthInstrument(loop=-1, loop_offset=8, words=16, sample=16, wave=True, pitch=PITCH),
)


def synth_data(streams: dict[int, bytes], *, rows: bytes = b"", envelope: bytes = b"") -> SynthData:
    memory = bytearray(TABLE + 168 * 4)
    for index in range(64):
        struct.pack_into(">H", memory, PITCH + 2 * index, 200 + index)
    memory[VIBRATO : VIBRATO + len(rows)] = rows
    memory[ENVELOPE : ENVELOPE + len(envelope)] = envelope
    position = STREAMS
    for sequence, stream in streams.items():
        struct.pack_into(">I", memory, TABLE + 4 * sequence, position)
        memory[position : position + len(stream)] = stream
        position += len(stream) + 1
    return SynthData(
        memory=bytes(memory),
        waves=bytes(64),
        sequence_table=TABLE,
        sequence_count=168,
        instruments=INSTRUMENTS,
        vibrato_base=VIBRATO,
        vibrato_row=15,
        envelope_base=ENVELOPE,
        envelope_row=8,
        tempo=0,
        tempo_dividend=10,
        attenuation=ATTENUATION,
        rest=REST,
        park=PARK,
        start_period=START_PERIOD,
        init_period=0x100,
        init_length=8,
        wipe_start=0x40,
        wipe_bytes=4 + 4 * 0x20,
        stack_tops=STACKS,
    )
