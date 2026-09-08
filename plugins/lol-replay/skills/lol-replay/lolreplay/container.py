"""ROFL container parsing.

ROFL2 (patch 13.x and later, magic ``RIOT\\x02\\x00``):

    offset 0   : magic (6 bytes)
    offset 6   : 8 unknown bytes (hash / nonce)
    offset 14  : u8 length + game version string (e.g. "16.17.810.4348")
    then       : frames, each = 17-byte header + data
                 header = <u32 id><u32 next_id><u8 type><u32 uncompressed_len><u32 compressed_len>
                 compressed_len == 0 means the data is stored raw (uncompressed_len bytes)
                 otherwise the data is one zstd frame
    then       : 256-byte signature
    then       : metadata JSON (utf-8) and, as the very last 4 bytes, its length (u32 LE)

ROFL1 (older files, magic ``RIOT\\x00\\x00``): only metadata is supported here; the
payload of those files is Blowfish encrypted and out of scope.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, Union

MAGIC_ROFL2 = b"RIOT\x02\x00"
MAGIC_ROFL1 = b"RIOT\x00\x00"
SIGNATURE_LEN = 256
VERSION_LEN_OFFSET = 14
FRAME_HEADER_FMT = "<IIBII"
FRAME_HEADER_LEN = struct.calcsize(FRAME_HEADER_FMT)
FRAME_CHUNK = 1
FRAME_KEYFRAME = 2
FRAME_STARTUP = 3
FRAME_RAW_CHUNK = 4
KNOWN_FRAME_TYPES = frozenset({FRAME_CHUNK, FRAME_KEYFRAME, FRAME_STARTUP, FRAME_RAW_CHUNK})
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
MAX_DECOMPRESSED = 256 * 1024 * 1024
ROFL1_HEADER_FMT = "<HIIIIII"


class RoflFormatError(ValueError):
    """The file is not a ROFL replay we can read."""


@dataclass(frozen=True)
class FrameHeader:
    id: int
    next_id: int
    type: int
    uncompressed_len: int
    compressed_len: int
    data_offset: int

    @property
    def stored_len(self) -> int:
        return self.compressed_len if self.compressed_len else self.uncompressed_len

    @property
    def is_compressed(self) -> bool:
        return self.compressed_len > 0

    @property
    def is_game_chunk(self) -> bool:
        return self.type in (FRAME_CHUNK, FRAME_RAW_CHUNK)


@dataclass(frozen=True)
class RoflFile:
    path: str
    format_version: int
    game_version: str
    header_unknown: bytes
    frames: Tuple[FrameHeader, ...]
    metadata_raw: bytes
    signature: bytes
    raw: bytes

    def metadata(self) -> dict:
        try:
            return json.loads(self.metadata_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RoflFormatError(f"metadata is not valid JSON: {exc}") from exc

    def chunks(self) -> Tuple[FrameHeader, ...]:
        return tuple(sorted((f for f in self.frames if f.is_game_chunk), key=lambda f: f.id))

    def keyframes(self) -> Tuple[FrameHeader, ...]:
        return tuple(sorted((f for f in self.frames if f.type == FRAME_KEYFRAME), key=lambda f: f.id))

    def frame_data(self, frame: FrameHeader) -> bytes:
        blob = self.raw[frame.data_offset: frame.data_offset + frame.stored_len]
        if not frame.is_compressed:
            return blob
        out = _zstd_decompress(blob)
        if len(out) != frame.uncompressed_len:
            raise RoflFormatError(
                f"frame {frame.id} type {frame.type}: expected {frame.uncompressed_len} bytes, got {len(out)}")
        return out


def _zstd_decompress(blob: bytes) -> bytes:
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RoflFormatError("payload needs the 'zstandard' package: pip install zstandard") from exc
    try:
        return zstandard.ZstdDecompressor().decompress(blob, max_output_size=MAX_DECOMPRESSED)
    except zstandard.ZstdError as exc:
        raise RoflFormatError(f"zstd decompression failed: {exc}") from exc


def read_rofl(path: Union[str, Path]) -> RoflFile:
    """Read and index a .rofl file. Frames are indexed, not decompressed."""
    p = Path(path)
    if not p.is_file():
        raise RoflFormatError(f"not a file: {p}")
    return parse_rofl_bytes(p.read_bytes(), str(p))


def parse_rofl_bytes(raw: bytes, path: str = "<memory>") -> RoflFile:
    if raw.startswith(MAGIC_ROFL2):
        return _parse_rofl2(raw, path)
    if raw.startswith(MAGIC_ROFL1):
        return _parse_rofl1(raw, path)
    raise RoflFormatError(f"{path}: bad magic {raw[:6]!r}; expected a .rofl replay")


def _parse_rofl2(raw: bytes, path: str) -> RoflFile:
    if len(raw) < VERSION_LEN_OFFSET + 1 + 4:
        raise RoflFormatError(f"{path}: file too small")
    meta_len = struct.unpack_from("<I", raw, len(raw) - 4)[0]
    meta_start = len(raw) - 4 - meta_len
    version_len = raw[VERSION_LEN_OFFSET]
    frames_start = VERSION_LEN_OFFSET + 1 + version_len
    if meta_start < frames_start:
        raise RoflFormatError(f"{path}: metadata length {meta_len} overlaps the header")
    game_version = raw[VERSION_LEN_OFFSET + 1: frames_start].decode("ascii", "replace")
    frames = _parse_frame_directory(raw, frames_start, meta_start, path)
    frames_end = _frames_end(frames, frames_start)
    signature = raw[frames_end:meta_start]
    return RoflFile(
        path=path, format_version=2, game_version=game_version, header_unknown=raw[6:VERSION_LEN_OFFSET],
        frames=frames, metadata_raw=raw[meta_start: len(raw) - 4], signature=signature, raw=raw)


def _frames_end(frames: Tuple[FrameHeader, ...], frames_start: int) -> int:
    if not frames:
        return frames_start
    last = frames[-1]
    return last.data_offset + last.stored_len


def _parse_frame_directory(raw: bytes, start: int, limit: int, path: str) -> Tuple[FrameHeader, ...]:
    frames = []
    pos = start
    while pos + FRAME_HEADER_LEN <= limit:
        fid, next_id, ftype, ulen, clen = struct.unpack_from(FRAME_HEADER_FMT, raw, pos)
        data_offset = pos + FRAME_HEADER_LEN
        stored = clen if clen else ulen
        if ulen == 0 or ftype not in KNOWN_FRAME_TYPES or data_offset + stored > limit:
            break
        if clen and raw[data_offset:data_offset + 4] != ZSTD_MAGIC:
            break
        frames.append(FrameHeader(fid, next_id, ftype, ulen, clen, data_offset))
        pos = data_offset + stored
    if not frames:
        raise RoflFormatError(f"{path}: no frames found (unsupported layout?)")
    trailing = limit - pos
    if trailing > SIGNATURE_LEN * 2:
        raise RoflFormatError(f"{path}: {trailing} unexplained bytes before metadata")
    return tuple(frames)


def _parse_rofl1(raw: bytes, path: str) -> RoflFile:
    off = len(MAGIC_ROFL1) + SIGNATURE_LEN
    if len(raw) < off + struct.calcsize(ROFL1_HEADER_FMT):
        raise RoflFormatError(f"{path}: truncated ROFL1 header")
    _hlen, _flen, meta_off, meta_len, _ph_off, _ph_len, _payload_off = struct.unpack_from(ROFL1_HEADER_FMT, raw, off)
    if meta_off + meta_len > len(raw):
        raise RoflFormatError(f"{path}: metadata range out of file")
    metadata_raw = raw[meta_off: meta_off + meta_len]
    try:
        version = json.loads(metadata_raw.decode("utf-8")).get("gameVersion", "")
    except (UnicodeDecodeError, json.JSONDecodeError):
        version = ""
    return RoflFile(path=path, format_version=1, game_version=str(version), header_unknown=b"",
                    frames=(), metadata_raw=metadata_raw, signature=raw[6:off], raw=raw)
