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


def _paeth_predictor(a: int, b: int, c: int) -> int:
    """Paeth filter predictor for PNG reconstruction."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    elif pb <= pc:
        return b
    else:
        return c


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
    bytes_per_pixel = channels
    row_bytes = width * bytes_per_pixel
    stride = 1 + row_bytes

    if len(raw_decompressed) < height * stride:
        raise ValueError("Decompressed PNG stream is shorter than expected")

    recon = bytearray(height * row_bytes)
    src_idx = 0

    for r in range(height):
        filter_type = raw_decompressed[src_idx]
        src_idx += 1
        scanline = raw_decompressed[src_idx:src_idx + row_bytes]
        src_idx += row_bytes

        row_start = r * row_bytes
        prev_row_start = (r - 1) * row_bytes if r > 0 else None

        for c in range(row_bytes):
            val = scanline[c]
            a = recon[row_start + c - bytes_per_pixel] if c >= bytes_per_pixel else 0
            b = recon[prev_row_start + c] if prev_row_start is not None else 0
            c_diag = recon[prev_row_start + c - bytes_per_pixel] if (prev_row_start is not None and c >= bytes_per_pixel) else 0

            if filter_type == 0:  # None
                recon_val = val
            elif filter_type == 1:  # Sub
                recon_val = (val + a) & 0xFF
            elif filter_type == 2:  # Up
                recon_val = (val + b) & 0xFF
            elif filter_type == 3:  # Average
                recon_val = (val + ((a + b) // 2)) & 0xFF
            elif filter_type == 4:  # Paeth
                recon_val = (val + _paeth_predictor(a, b, c_diag)) & 0xFF
            else:
                recon_val = val

            recon[row_start + c] = recon_val

    img_array = np.frombuffer(recon, dtype=np.uint8).reshape((height, width, channels))
    # Return RGB (discard alpha if present)
    return img_array[:, :, :3]


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
