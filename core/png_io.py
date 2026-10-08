"""Lightweight PNG reader and writer with zero external image library dependencies.

Supports:
- Decoding 8-bit RGB / RGBA PNG tiles (used by Terrarium and Mapbox elevation tiles).
- Encoding 16-bit single-channel (grayscale) PNG heightmaps (avoiding 8-bit terracing).
- Pure standard library (zlib, struct) + NumPy.
"""

from __future__ import annotations
import struct
import zlib
import numpy as np
from typing import Tuple

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def decode_png_rgb(png_bytes: bytes) -> np.ndarray:
    """Decode an 8-bit RGB or RGBA PNG from bytes into a NumPy uint8 array of shape (H, W, 3)."""
    if not png_bytes.startswith(PNG_SIGNATURE):
        raise ValueError("Invalid PNG signature")

    offset = 8
    width = 0
    height = 0
    bit_depth = 8
    color_type = 2
    idat_chunks = []

    while offset < len(png_bytes):
        length, chunk_type = struct.unpack(">I4s", png_bytes[offset:offset + 8])
        offset += 8
        chunk_data = png_bytes[offset:offset + length]
        offset += length
        _crc = png_bytes[offset:offset + 4]
        offset += 4

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if bit_depth != 8:
                raise NotImplementedError(f"Unsupported PNG bit depth: {bit_depth}")
            if color_type not in (2, 6):  # 2 = RGB, 6 = RGBA
                raise NotImplementedError(f"Unsupported PNG color type: {color_type}")
            if interlace != 0:
                raise NotImplementedError("Interlaced PNG is not supported")
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    raw_decompressed = zlib.decompress(b"".join(idat_chunks))
    channels = 3 if color_type == 2 else 4
    bpp = channels
    row_bytes = width * bpp
    stride = 1 + row_bytes

    if len(raw_decompressed) < height * stride:
        raise ValueError("Decompressed PNG stream is shorter than expected")

    raw = np.frombuffer(raw_decompressed, dtype=np.uint8, count=height * stride).reshape((height, stride))
    filters = raw[:, 0]
    scanlines = raw[:, 1:]
    recon = np.zeros((height, row_bytes), dtype=np.uint8)
    prev = np.zeros(row_bytes, dtype=np.uint8)

    for r in range(height):
        line = scanlines[r]
        filter_type = filters[r]
        if filter_type == 1:  # Sub: running sum per channel (uint8 wraps mod 256)
            recon[r] = np.cumsum(line.reshape((width, bpp)), axis=0, dtype=np.uint8).ravel()
        elif filter_type == 2:  # Up
            recon[r] = line + prev
        elif filter_type == 3:  # Average
            recon[r] = _unfilter_average(line.tobytes(), prev.tobytes(), bpp)
        elif filter_type == 4:  # Paeth
            recon[r] = _unfilter_paeth(line.tobytes(), prev.tobytes(), bpp)
        else:  # None (and unknown filters)
            recon[r] = line
        prev = recon[r]

    img_array = recon.reshape((height, width, channels))
    # Return RGB (discard alpha if present)
    return img_array[:, :, :3]


def _unfilter_average(line: bytes, prev: bytes, bpp: int) -> bytearray:
    out = bytearray(len(line))
    for i in range(bpp):
        out[i] = (line[i] + (prev[i] >> 1)) & 0xFF
    for i in range(bpp, len(line)):
        out[i] = (line[i] + ((out[i - bpp] + prev[i]) >> 1)) & 0xFF
    return out


def _unfilter_paeth(line: bytes, prev: bytes, bpp: int) -> bytearray:
    """Paeth reconstruction; inherently sequential, so kept as a tight inlined loop."""
    out = bytearray(len(line))
    for i in range(bpp):  # No left neighbour: the predictor reduces to the byte above
        out[i] = (line[i] + prev[i]) & 0xFF
    for i in range(bpp, len(line)):
        a = out[i - bpp]
        b = prev[i]
        c = prev[i - bpp]
        pa = b - c
        pb = a - c
        pc = pa + pb
        if pa < 0:
            pa = -pa
        if pb < 0:
            pb = -pb
        if pc < 0:
            pc = -pc
        if pa <= pb and pa <= pc:
            pred = a
        elif pb <= pc:
            pred = b
        else:
            pred = c
        out[i] = (line[i] + pred) & 0xFF
    return out


def encode_png_gray16(array_uint16: np.ndarray) -> bytes:
    """Encode a 2D NumPy uint16 array into a 16-bit Grayscale PNG byte string."""
    if array_uint16.ndim != 2:
        raise ValueError("Expected 2D array for grayscale 16-bit PNG")
    if array_uint16.dtype != np.uint16:
        raise ValueError(f"Expected uint16 dtype, got {array_uint16.dtype}")

    height, width = array_uint16.shape

    # PNG Signature
    chunks = [PNG_SIGNATURE]

    # IHDR Chunk
    # Width, Height, Bit depth (16), Color Type (0 = Grayscale), Comp (0), Filter (0), Interlace (0)
    ihdr_payload = struct.pack(">IIBBBBB", width, height, 16, 0, 0, 0, 0)
    ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_payload) & 0xFFFFFFFF)
    chunks.append(struct.pack(">I4s", len(ihdr_payload), b"IHDR") + ihdr_payload + ihdr_crc)

    # Convert array to big-endian bytes
    big_endian_data = array_uint16.astype(">u2").tobytes()
    row_bytes = width * 2

    # Add filter byte (0 = None) before each scanline
    filtered = bytearray(height * (1 + row_bytes))
    for r in range(height):
        dst = r * (1 + row_bytes)
        filtered[dst] = 0  # Filter type 0
        src = r * row_bytes
        filtered[dst + 1:dst + 1 + row_bytes] = big_endian_data[src:src + row_bytes]

    # Compress IDAT
    compressed = zlib.compress(bytes(filtered), level=6)
    idat_crc = struct.pack(">I", zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF)
    chunks.append(struct.pack(">I4s", len(compressed), b"IDAT") + compressed + idat_crc)

    # IEND Chunk
    iend_crc = struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    chunks.append(struct.pack(">I4s", 0, b"IEND") + iend_crc)

    return b"".join(chunks)
