"""Custom MCP tool handlers for DSLogic U3Pro16."""

from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _fmt_state(word: int, ch_num: int = 16) -> str:
    parts = []
    for i in range(ch_num):
        val = (word >> i) & 1
        parts.append(f"CH{i}={val}")
    return " ".join(parts)


def dslogic_get_status(inst, alias: str = "default") -> str:
    """Read hardware status."""
    status = inst.read_hw_status()
    return (
        f"[PASS] DSLogic status=0x{status:02X}\n"
        f"  FPGA_DONE={bool(status & 0x40)}\n"
        f"  GPIF_DONE={bool(status & 0x80)}\n"
        f"  SYS_EN={bool(status & 0x04)}"
    )


def dslogic_set_threshold(inst, vth: float = 1.65) -> str:
    """Set logic threshold voltage (default 1.65 V for 3.3 V logic)."""
    inst.set_threshold(float(vth), max25=True)
    return f"[PASS] Threshold set to {float(vth):.2f} V"


def dslogic_load_fpga(inst, bitstream_path: str = r"D:\DSView\res\DSLogicU3Pro16.bin") -> str:
    """Load FPGA bitstream from file (only needed if FPGA_DONE is not set)."""
    path = Path(bitstream_path)
    if not path.exists():
        return f"[FAIL] Bitstream not found: {bitstream_path}"
    inst.load_fpga_bitstream(str(path))
    return f"[PASS] FPGA bitstream loaded from {bitstream_path}"


def dslogic_capture(
    inst,
    samplerate_mhz: float = 100.0,
    samples: int = 1024,
    ch_en: int = 0xFFFF,
    output_path: str = "capture.bin",
) -> str:
    """Single buffered capture and save to file."""
    from instrument_mcp.dslogic_usb import DSL_BUFFER500x16, SR_MHZ

    inst.arm_and_start(
        mode_id=DSL_BUFFER500x16,
        samplerate=SR_MHZ(int(samplerate_mhz)),
        limit_samples=int(samples),
        ch_en=int(ch_en),
    )
    raw = inst.read_capture(limit_samples=int(samples), ch_en=int(ch_en))
    inst.stop()

    Path(output_path).write_bytes(raw)

    ch_num = bin(int(ch_en)).count("1") or 16
    fmt = f"<{len(raw) // 2}H"
    words = struct.unpack(fmt, raw[: (len(raw) // 2) * 2])
    summary = []
    for i in range(min(5, len(words))):
        summary.append(f"sample[{i}]=0x{words[i]:04X}")

    return (
        f"[PASS] Captured {len(raw)} bytes ({len(words)} samples, {ch_num}ch) "
        f"to {output_path}\n  First samples: {', '.join(summary)}"
    )


def _extract_channel_blocks(chunk: bytes, ch_num: int) -> List[List[int]]:
    """Extract LA_CROSS_DATA chunk into per-channel uint64 blocks.

    DSLogic stream format: groups of (ch_num * 8) bytes.
    Each 8-byte block holds 64 consecutive samples for one channel.
    Channel order: CH0, CH1, ..., CH(ch_num-1), then repeat.
    Within a block, bit i corresponds to sample i.
    """
    block_size = 8
    group_size = ch_num * block_size
    full_groups = len(chunk) // group_size

    blocks: List[List[int]] = [[] for _ in range(ch_num)]
    for g in range(full_groups):
        base = g * group_size
        for ch in range(ch_num):
            u64 = struct.unpack_from("<Q", chunk, base + ch * block_size)[0]
            blocks[ch].append(u64)

    return blocks


def _detect_edges(
    blocks: List[int],
    prev_bit: Optional[int],
    base_sample: int,
) -> Tuple[List[Tuple[int, int, int]], int]:
    """Detect 0->1 and 1->0 transitions in a channel's uint64 blocks.

    Returns:
        (edges, last_bit) where edges is a list of (sample_index, from, to).
    """
    edges: List[Tuple[int, int, int]] = []
    last_bit = prev_bit

    for i, block in enumerate(blocks):
        block_base = base_sample + i * 64
        first_bit = block & 1

        # Edge from previous block's last bit to this block's first bit.
        if last_bit is not None and first_bit != last_bit:
            edges.append((block_base, last_bit, first_bit))
        last_bit = first_bit

        # Inner edges: edge_mask bit t is 1 when sample t and sample t+1 differ.
        edge_mask = block ^ (block >> 1)

        pos = 1
        temp_mask = edge_mask
        while temp_mask and pos < 64:
            if temp_mask & 1:
                bit = (block >> pos) & 1
                edges.append((block_base + pos, 1 - bit, bit))
                last_bit = bit
            temp_mask >>= 1
            pos += 1

    return edges, last_bit if last_bit is not None else 0


def _apply_debounce(
    edges: List[Tuple[int, int, int]],
    total_samples: int,
    debounce_samples: int,
) -> List[Tuple[int, int, int]]:
    """Remove edges whose new level does not last at least debounce_samples."""
    if debounce_samples <= 0 or len(edges) < 1:
        return edges

    filtered: List[Tuple[int, int, int]] = []
    for i, (sample, from_lvl, to_lvl) in enumerate(edges):
        next_sample = edges[i + 1][0] if i + 1 < len(edges) else total_samples
        duration = next_sample - sample
        if duration >= debounce_samples:
            filtered.append((sample, from_lvl, to_lvl))

    return filtered


def dslogic_monitor_level(
    inst,
    samplerate_mhz: float = 1.0,
    duration: float = 30.0,
    channel: int = -1,
    edge_type: str = "both",
    debounce_us: float = 0.0,
    stop_on_first_edge: bool = False,
) -> str:
    """Continuously monitor logic levels and report edge transitions (DSLogic U3Pro16 only).

    This command is specific to the DreamSourceLab DSLogic U3Pro16 logic analyzer.
    It uses the device's LA_CROSS_DATA stream format and will not work with other
    logic analyzers or DSLogic models without verification.

    Args:
        samplerate_mhz: Sampling rate in MHz (1~125). Lower rates reduce CPU load.
        duration: Monitoring duration in seconds.
        channel: Channel index to monitor (0=CH0). -1 monitors all 16 channels.
        edge_type: Which edges to report: "rising", "falling", or "both".
        debounce_us: Ignore pulses shorter than this many microseconds.
        stop_on_first_edge: If True, stop immediately after the first matching edge.
    """
    from instrument_mcp.dslogic_usb import DSL_STREAM125x16_16, SR_MHZ

    samplerate = SR_MHZ(int(samplerate_mhz))
    channel_int = int(channel)
    stop_on_first = bool(stop_on_first_edge)
    debounce_samples = int(debounce_us * samplerate / 1e6)

    edge_type_lower = str(edge_type).lower()
    if edge_type_lower not in {"rising", "falling", "both"}:
        return f"[FAIL] Invalid edge_type: {edge_type}. Use rising/falling/both."

    # DSL_STREAM125x16_16 always emits 16-channel LA_CROSS_DATA.
    ch_num = 16
    if channel_int >= 0:
        monitored_channels = [channel_int]
    else:
        monitored_channels = list(range(ch_num))

    inst.set_threshold(1.65, max25=True)
    inst.start_stream(
        mode_id=DSL_STREAM125x16_16,
        samplerate=samplerate,
        ch_en=0xFFFF,
    )

    last_bits: List[Optional[int]] = [None] * ch_num
    initial_state: Optional[int] = None
    t0 = time.time()
    total_samples = 0
    events: List[dict] = []
    final_state = 0
    stopped_early = False

    def _edge_matches(from_bit: int, to_bit: int) -> bool:
        if edge_type_lower == "rising":
            return from_bit == 0 and to_bit == 1
        if edge_type_lower == "falling":
            return from_bit == 1 and to_bit == 0
        return True

    def callback(chunk: bytes) -> bool:
        nonlocal total_samples, final_state, stopped_early, initial_state

        channel_blocks = _extract_channel_blocks(chunk, ch_num)
        block_count = len(channel_blocks[0]) if channel_blocks and channel_blocks[0] else 0
        if block_count == 0:
            return True

        samples_in_chunk = block_count * 64
        total_samples += samples_in_chunk
        base_ts = total_samples - samples_in_chunk

        for ch in monitored_channels:
            if last_bits[ch] is None and channel_blocks[ch]:
                last_bits[ch] = channel_blocks[ch][0] & 1

            edges, new_last = _detect_edges(channel_blocks[ch], last_bits[ch], base_ts)
            if debounce_samples > 0:
                edges = _apply_debounce(edges, total_samples, debounce_samples)

            for sample_idx, from_bit, to_bit in edges:
                if _edge_matches(from_bit, to_bit):
                    rel_time_us = (sample_idx / samplerate) * 1e6
                    events.append(
                        {
                            "channel": ch,
                            "from": from_bit,
                            "to": to_bit,
                            "sample": sample_idx,
                            "time_us": round(rel_time_us, 1),
                        }
                    )
                    if stop_on_first:
                        stopped_early = True
                        final_state = 0
                        for c in range(ch_num):
                            if last_bits[c]:
                                final_state |= 1 << c
                            elif c == ch:
                                final_state |= to_bit << c
                        return False

            last_bits[ch] = new_last

        if initial_state is None:
            initial_state = 0
            for ch in range(ch_num):
                if last_bits[ch]:
                    initial_state |= 1 << ch

        final_state = 0
        for ch in range(ch_num):
            if last_bits[ch]:
                final_state |= 1 << ch

        if duration and (time.time() - t0) >= duration:
            return False
        return True

    try:
        inst.stream_sync(callback=callback, chunk_size=16 * 1024)
    finally:
        inst.stop()

    if channel_int >= 0:
        lines = [f"CH{channel_int}: {sum(1 for e in events if e['channel'] == channel_int)} events"]
    else:
        counts: Dict[int, int] = {}
        for e in events:
            counts[e["channel"]] = counts.get(e["channel"], 0) + 1
        lines = [f"CH{ch}: {cnt}" for ch, cnt in sorted(counts.items()) if cnt]

    preview = events[:10]
    preview_str = "\n".join(f"  {e}" for e in preview) if preview else "  (none)"
    stop_note = " (stopped on first edge)" if stopped_early else ""

    initial_state_val = initial_state if initial_state is not None else 0

    return (
        f"[PASS] Monitored {total_samples} samples over {duration:.1f}s{stop_note}\n"
        f"  Initial state: 0x{initial_state_val:04X} ({_fmt_state(initial_state_val, ch_num)})\n"
        f"  Final state: 0x{final_state:04X} ({_fmt_state(final_state, ch_num)})\n"
        f"  Recorded {len(events)} {edge_type_lower} edge events\n"
        f"  Per-channel counts: {', '.join(lines) if lines else 'none'}\n"
        f"  First events:\n{preview_str}"
    )
