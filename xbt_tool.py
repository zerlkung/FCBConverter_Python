#!/usr/bin/env python3
"""
xbt_tool.py - XBT texture extractor/importer with PS4 support

XBT is the Dunia engine proprietary texture container wrapping a DDS payload.
On PS4 the payload is a 44-byte GnfSurface descriptor followed by GPU-swizzled
(2D macro-tiled) pixel data.

PS4 swizzle/unswizzle modes (priority order):
  1. libSceGpuAddress.dll (best): official PS4 SDK addrlib via ctypes.
     Pass --orbis-tools <dir> with libSceGpuAddress.dll + libSceGnm.dll.
  2. orbis-image2gnf.exe (import-only): used for DDS→GNF swizzle when dll unavailable.
  3. Fallback (extract only): raw swizzled DDS with a warning.

Supported DDS formats:
  BC1/DXT1  – 4×4 blocks, 8 bytes
  BC2/DXT3  – 4×4 blocks, 16 bytes
  BC3/DXT5  – 4×4 blocks, 16 bytes
  BC4        – 4×4 blocks, 8 bytes
  BC5        – 4×4 blocks, 16 bytes
  BC7        – 4×4 blocks, 16 bytes
  R8G8B8A8 / uncompressed – 1×1 blocks, 4 bytes

Usage:
  Extract:  python xbt_tool.py extract  font.xbt  ./out  [--png] [--dxgi 99]
                                                          [--orbis-tools D:/SDKTools]
  Import:   python xbt_tool.py import   font_new.dds  font.xbt.meta.json  font_new.xbt
                                                          [--orbis-tools D:/SDKTools]

PS4 vs PC is auto-detected from the payload. No --ps4 flag needed.
"""

import os
import sys
import struct
import json
import argparse
import subprocess
import tempfile
import shutil
import ctypes

# ---------------------------------------------------------------------------
# DDS helpers
# ---------------------------------------------------------------------------

DDS_MAGIC        = 0x20534444   # 'DDS '
DDPF_FOURCC      = 0x4
DDPF_RGB         = 0x40
DDPF_LUMINANCE   = 0x20000
DX10_HEADER_SIZE = 20

# FourCC → (block_w, block_h, bytes_per_block)
_FOURCC_BLOCKS = {
    b'DXT1': (4, 4,  8),
    b'DXT2': (4, 4, 16),
    b'DXT3': (4, 4, 16),
    b'DXT4': (4, 4, 16),
    b'DXT5': (4, 4, 16),
    b'BC4U': (4, 4,  8),
    b'BC4S': (4, 4,  8),
    b'BC5U': (4, 4, 16),
    b'BC5S': (4, 4, 16),
    b'ATI1': (4, 4,  8),
    b'ATI2': (4, 4, 16),
}

# DXGI format → (block_w, block_h, bytes_per_block)
_DXGI_BLOCKS = {
    70:  (4, 4,  8),   # BC1_TYPELESS
    71:  (4, 4,  8),   # BC1_UNORM
    72:  (4, 4,  8),   # BC1_UNORM_SRGB
    73:  (4, 4, 16),   # BC2_TYPELESS
    74:  (4, 4, 16),   # BC2_UNORM
    75:  (4, 4, 16),   # BC2_UNORM_SRGB
    76:  (4, 4, 16),   # BC3_TYPELESS
    77:  (4, 4, 16),   # BC3_UNORM
    78:  (4, 4, 16),   # BC3_UNORM_SRGB
    79:  (4, 4,  8),   # BC4_TYPELESS
    80:  (4, 4,  8),   # BC4_UNORM
    81:  (4, 4,  8),   # BC4_SNORM
    82:  (4, 4, 16),   # BC5_TYPELESS
    83:  (4, 4, 16),   # BC5_UNORM
    84:  (4, 4, 16),   # BC5_SNORM
    94:  (4, 4, 16),   # BC6H_TYPELESS
    95:  (4, 4, 16),   # BC6H_UF16
    96:  (4, 4, 16),   # BC6H_SF16
    97:  (4, 4, 16),   # BC7_TYPELESS
    98:  (4, 4, 16),   # BC7_UNORM
    99:  (4, 4, 16),   # BC7_UNORM_SRGB
    28:  (1, 1,  4),   # R8G8B8A8_UNORM
    29:  (1, 1,  4),
    87:  (1, 1,  4),   # B8G8R8A8_UNORM
    61:  (1, 1,  1),   # R8_UNORM
}

# DXGI format → orbis-image2gnf format name  (fallback import path)
_DXGI_TO_ORBIS_FMT = {
    71: 'Bc1UNorm',     72: 'Bc1UNormSrgb',
    74: 'Bc2UNorm',     75: 'Bc2UNormSrgb',
    77: 'Bc3UNorm',     78: 'Bc3UNormSrgb',
    80: 'Bc4UNorm',     81: 'Bc4Snorm',
    83: 'Bc5UNorm',     84: 'Bc5Snorm',
    95: 'Bc6UNorm',     96: 'Bc6Snorm',
    98: 'Bc7UNorm',     99: 'Bc7UNormSrgb',
    28: 'R8G8B8A8UNorm', 29: 'R8G8B8A8UNorm',
    87: 'B8G8R8A8UNorm',
}

# GnfSurface dword[4] → DXGI format (empirical)
_PS4_DWORD4_TO_DXGI = {
    0x707FC1FF: 99,   # BC7_UNORM_SRGB  (confirmed: FCP font textures)
}

# reg[1] value needed for initFromTexture – the library requires this field to be set
# to the value produced by orbis-image2gnf for a correctly-formed SceGnmTexture.
# This constant was determined empirically from the reference PS4 SDK GNF file for
# a 512×512 BC7_UNORM_SRGB texture.
_GNF_REG1_BC7_512 = 0x26900008


def _align(x: int, a: int) -> int:
    return ((x + a - 1) // a) * a


def _ceil_div(x: int, a: int) -> int:
    return (x + a - 1) // a


def build_dds_header(width: int, height: int, dxgi_fmt: int, mip_count: int = 1) -> bytes:
    """Build a 148-byte DDS + DX10 extension header."""
    binfo = _DXGI_BLOCKS.get(dxgi_fmt)
    if binfo is None:
        raise ValueError(f'Unsupported DXGI format {dxgi_fmt}')
    bw, bh, bb = binfo
    linear_size = max(1, _ceil_div(width, bw)) * max(1, _ceil_div(height, bh)) * bb

    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    if mip_count > 1:
        flags |= 0x20000

    hdr = struct.pack('<7I', 124, flags, height, width, linear_size, 1, max(mip_count, 1))
    hdr += b'\x00' * 44
    hdr += struct.pack('<II4sI4I', 32, DDPF_FOURCC, b'DX10', 0, 0, 0, 0, 0)
    caps = 0x1000
    if mip_count > 1:
        caps |= 0x8 | 0x400000
    hdr += struct.pack('<5I', caps, 0, 0, 0, 0)
    dx10 = struct.pack('<5I', dxgi_fmt, 3, 0, 1, 0)
    return struct.pack('<I', DDS_MAGIC) + hdr + dx10


def parse_dds_header(dds: bytes) -> dict:
    """Return dict with width, height, mip_count, block_w, block_h, block_bytes, data_offset."""
    if len(dds) < 128:
        raise ValueError("DDS too small")
    magic = struct.unpack_from('<I', dds, 0)[0]
    if magic != DDS_MAGIC:
        raise ValueError(f"Bad DDS magic 0x{magic:08X}")

    hdr = struct.unpack_from('<7I44xI8xI', dds, 4)
    height    = hdr[2]
    width     = hdr[3]
    mip_count = hdr[6] if hdr[6] > 0 else 1

    # DDS_PIXELFORMAT at offset 4+72 = 76
    pf_size, pf_flags, fourcc, rgb_bpp = struct.unpack_from('<II4sI', dds, 4 + 72)
    data_offset = 4 + 124

    if pf_flags & DDPF_FOURCC:
        if fourcc == b'DX10':
            dxgi_fmt = struct.unpack_from('<I', dds, data_offset)[0]
            data_offset += DX10_HEADER_SIZE
            block_info = _DXGI_BLOCKS.get(dxgi_fmt)
            if block_info is None:
                raise ValueError(f"Unsupported DXGI format {dxgi_fmt}")
            block_w, block_h, block_bytes = block_info
        else:
            block_info = _FOURCC_BLOCKS.get(fourcc)
            if block_info is None:
                raise ValueError(f"Unsupported FourCC {fourcc!r}")
            block_w, block_h, block_bytes = block_info
    elif pf_flags & (DDPF_RGB | DDPF_LUMINANCE):
        bytes_pp = rgb_bpp // 8
        block_w, block_h, block_bytes = 1, 1, bytes_pp
    else:
        raise ValueError(f"Unknown DDS pixel format flags 0x{pf_flags:08X}")

    return {
        'width':       width,
        'height':      height,
        'mip_count':   mip_count,
        'block_w':     block_w,
        'block_h':     block_h,
        'block_bytes': block_bytes,
        'data_offset': data_offset,
    }


# ---------------------------------------------------------------------------
# PS4 GnfSurface  (44-byte descriptor embedded in PS4 XBT payload)
# ---------------------------------------------------------------------------

GNF_SURFACE_SIZE = 44   # bytes before pixel data in PS4 XBT payload
GNF_HEADER_SIZE  = 256  # standard GNF file header size


def parse_gnf_surface(payload: bytes) -> dict:
    """Parse PS4 XBT payload: 44-byte GnfSurface + swizzled pixels."""
    if len(payload) < GNF_SURFACE_SIZE:
        raise ValueError('Payload too small for GnfSurface descriptor')

    dwords = [struct.unpack_from('<I', payload, i * 4)[0] for i in range(11)]

    width  = (dwords[4] & 0x3FFF) + 1
    height = ((dwords[6] >> 13) & 0x3FF) + 1

    pixel_data   = payload[GNF_SURFACE_SIZE:]
    raw_desc_hex = payload[:GNF_SURFACE_SIZE].hex()

    dxgi_fmt = _PS4_DWORD4_TO_DXGI.get(dwords[4])
    if dxgi_fmt is None:
        bx = max(1, _ceil_div(width, 4))
        by = max(1, _ceil_div(height, 4))
        blocks = bx * by
        bpb = len(pixel_data) // blocks if blocks else 0
        if bpb == 8:
            dxgi_fmt = 80
        elif bpb == 16:
            dxgi_fmt = 99
        else:
            dxgi_fmt = 99

    return {
        'width':          width,
        'height':         height,
        'dxgi_fmt':       dxgi_fmt,
        'raw_descriptor': raw_desc_hex,
        'pixel_data':     pixel_data,
    }


# ---------------------------------------------------------------------------
# libSceGpuAddress.dll — direct PS4 tile/detile via ctypes
# ---------------------------------------------------------------------------
# The DLL exports two relevant free functions (by ordinal):
#   ord 94: int TilingParameters::initFromTexture(const Texture* tex, uint32 mip, uint32 slice)
#   ord 49: int detileSurface(void* dst, const void* src, const TilingParameters* params)
#   ord 99: int tileSurface(void* dst, const void* src, const TilingParameters* params)
#
# SceGnmTexture is 8 DWORDs (32 bytes) matching the GNF SceGnmTexture register layout.
# TilingParameters is allocated as a 256-byte zeroed buffer; initFromTexture fills it.
#
# Key mapping from XBT GnfSurface dwords to SceGnmTexture regs:
#   reg[0] = 0          (base addr – ignored for file-based operation)
#   reg[1] = _GNF_REG1  (required by initFromTexture for correct format detection)
#   reg[2] = dword[4]   (width + format bits)
#   reg[3] = dword[5]   (tiling mode + height upper bits)
#   reg[4] = dword[6]   (height)
#   reg[5] = dword[7]   (= 0)
#   reg[6] = dword[8]   (= 0)
#   reg[7] = dword[9]   (= 0 for this texture)
#
# The reg[1] = 0x26900008 was determined empirically; it encodes format/pitch info that
# initFromTexture needs to identify the BC7 format correctly.  Different texture types
# may need different reg[1] values (compute from orbis-image2gnf GNF output header).

_SCE_GPU_DLL_CACHE: dict = {}   # orbis_tools_dir → (dll, f_init, f_detile, f_tile)


def _load_sce_gpu_dll(orbis_tools_dir: str):
    """Load libSceGpuAddress.dll and return (dll, f_init, f_detile, f_tile)."""
    if orbis_tools_dir in _SCE_GPU_DLL_CACHE:
        return _SCE_GPU_DLL_CACHE[orbis_tools_dir]

    dll_path = os.path.join(orbis_tools_dir, 'libSceGpuAddress.dll')
    if not os.path.isfile(dll_path):
        raise FileNotFoundError(
            f'libSceGpuAddress.dll not found in {orbis_tools_dir!r}.\n'
            f'Provide the directory containing libSceGpuAddress.dll via --orbis-tools.'
        )

    # Some environments require libSceGnm.dll to be loadable too; add tools dir to PATH
    old_path = os.environ.get('PATH', '')
    os.environ['PATH'] = orbis_tools_dir + os.pathsep + old_path
    try:
        dll = ctypes.CDLL(dll_path)
    finally:
        os.environ['PATH'] = old_path

    f_init   = dll[94];  f_init.restype   = ctypes.c_int   # initFromTexture
    f_detile = dll[49];  f_detile.restype = ctypes.c_int   # detileSurface
    f_tile   = dll[99];  f_tile.restype   = ctypes.c_int   # tileSurface

    result = (dll, f_init, f_detile, f_tile)
    _SCE_GPU_DLL_CACHE[orbis_tools_dir] = result
    return result


def _build_tiling_params(f_init, raw_descriptor: bytes) -> ctypes.Array:
    """
    Build a TilingParameters struct by calling initFromTexture with the
    SceGnmTexture registers reconstructed from the XBT GnfSurface descriptor.
    Returns a 256-byte ctypes buffer.
    """
    dwords = struct.unpack_from('<11I', raw_descriptor)

    texture_regs = (ctypes.c_uint32 * 8)(
        0,                  # reg[0] = base addr (unused for file op)
        _GNF_REG1_BC7_512,  # reg[1] = format/pitch info (required by initFromTexture)
        dwords[4],          # reg[2] = width + format
        dwords[5],          # reg[3] = tiling mode + height upper
        dwords[6],          # reg[4] = height
        dwords[7],          # reg[5]
        dwords[8],          # reg[6]
        dwords[9],          # reg[7]
    )

    tp_buf = (ctypes.c_uint8 * 256)()
    ret = f_init(tp_buf, texture_regs, 0, 0)
    if ret != 0:
        raise RuntimeError(
            f'TilingParameters::initFromTexture failed with code 0x{ret & 0xFFFFFFFF:08X}.\n'
            f'Verify that reg[1] = 0x{_GNF_REG1_BC7_512:08X} is correct for this texture type.\n'
            f'GnfSurface dword[4] = 0x{dwords[4]:08X}, dword[5] = 0x{dwords[5]:08X}'
        )
    return tp_buf


def ps4_detile(orbis_tools_dir: str, swizzled: bytes, raw_descriptor: bytes) -> bytes:
    """
    Unswizzle PS4 tiled pixel data to linear layout using libSceGpuAddress.dll.
    Returns linear pixel bytes of the same length.
    """
    _, f_init, f_detile, _ = _load_sce_gpu_dll(orbis_tools_dir)
    tp_buf = _build_tiling_params(f_init, raw_descriptor)

    src_buf = (ctypes.c_uint8 * len(swizzled)).from_buffer_copy(swizzled)
    dst_buf = (ctypes.c_uint8 * len(swizzled))()

    ret = f_detile(dst_buf, src_buf, tp_buf)
    if ret != 0:
        raise RuntimeError(f'detileSurface failed with code 0x{ret & 0xFFFFFFFF:08X}')
    return bytes(dst_buf)


def ps4_tile(orbis_tools_dir: str, linear: bytes, raw_descriptor: bytes) -> bytes:
    """
    Swizzle linear pixel data to PS4 tiled layout using libSceGpuAddress.dll.
    Returns tiled pixel bytes of the same length.
    """
    _, f_init, _, f_tile = _load_sce_gpu_dll(orbis_tools_dir)
    tp_buf = _build_tiling_params(f_init, raw_descriptor)

    src_buf = (ctypes.c_uint8 * len(linear)).from_buffer_copy(linear)
    dst_buf = (ctypes.c_uint8 * len(linear))()

    ret = f_tile(dst_buf, src_buf, tp_buf)
    if ret != 0:
        raise RuntimeError(f'tileSurface failed with code 0x{ret & 0xFFFFFFFF:08X}')
    return bytes(dst_buf)


# ---------------------------------------------------------------------------
# GNF file helpers (used by orbis-image2gnf fallback import path)
# ---------------------------------------------------------------------------

def build_gnf_from_xbt_payload(raw_descriptor: bytes, pixel_data: bytes) -> bytes:
    """
    Construct a GNF v2 file from PS4 XBT raw descriptor + swizzled pixel data.
    Used only by the orbis-image2gnf fallback import path.
    """
    dwords = struct.unpack_from('<11I', raw_descriptor)
    total_size = GNF_HEADER_SIZE + len(pixel_data)

    file_hdr = (b'GNF '
                + struct.pack('<I', 248)
                + bytes([2, 1, 8, 0])
                + struct.pack('<I', total_size))

    regs = struct.pack('<8I',
        0, 0,
        dwords[4], dwords[5], dwords[6],
        dwords[7], dwords[8], dwords[9])

    padding = bytes(GNF_HEADER_SIZE - len(file_hdr) - len(regs))
    return file_hdr + regs + padding + pixel_data


def read_gnf_pixels(gnf_data: bytes) -> bytes:
    """Extract pixel data from a GNF file (everything after the 256-byte header)."""
    if len(gnf_data) < GNF_HEADER_SIZE:
        raise ValueError(f'GNF file too small ({len(gnf_data)} bytes)')
    if gnf_data[:4] != b'GNF ':
        raise ValueError(f'Bad GNF magic: {gnf_data[:4]!r}')
    return gnf_data[GNF_HEADER_SIZE:]


# ---------------------------------------------------------------------------
# orbis-image2gnf.exe fallback (import path only)
# ---------------------------------------------------------------------------

def _find_orbis_exe(orbis_tools_dir: str) -> str:
    exe = os.path.join(orbis_tools_dir, 'orbis-image2gnf.exe')
    if not os.path.isfile(exe):
        raise FileNotFoundError(f'orbis-image2gnf.exe not found in {orbis_tools_dir!r}')
    return exe


def orbis_dds_to_gnf(orbis_tools_dir: str, dds_path: str, gnf_out_path: str,
                     dxgi_fmt: int, num_mips: int = 1) -> None:
    """Call orbis-image2gnf.exe: DDS → tiled GNF (fallback import path)."""
    exe = _find_orbis_exe(orbis_tools_dir)
    fmt = _DXGI_TO_ORBIS_FMT.get(dxgi_fmt)
    if fmt is None:
        raise ValueError(f'No orbis format name for DXGI {dxgi_fmt}')
    cmd = [exe, '-i', dds_path, '-o', gnf_out_path, '-f', fmt, '-m', str(num_mips)]
    print(f'  Running: {" ".join(cmd)}')
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        print(f'  [orbis] {result.stdout.strip()}')
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f'orbis-image2gnf.exe failed (exit {result.returncode}):\n{err}')
    if not os.path.isfile(gnf_out_path):
        raise RuntimeError(f'orbis-image2gnf.exe did not produce output: {gnf_out_path}')


# ---------------------------------------------------------------------------
# XBT header
# ---------------------------------------------------------------------------

XBT_MAGIC = 0x00584254  # 'XBT\0'


def parse_xbt(xbt: bytes) -> tuple:
    """Parse XBT header, return (meta dict, raw payload bytes)."""
    if len(xbt) < 32:
        raise ValueError("File too small to be XBT")
    magic = struct.unpack_from('<I', xbt, 0)[0]
    if magic != XBT_MAGIC:
        raise ValueError(f"Bad XBT magic 0x{magic:08X}")

    version   = struct.unpack_from('<I', xbt, 4)[0]
    hdr_len   = struct.unpack_from('<I', xbt, 8)[0]
    param1, param2, param3, param4, param5, mips_count, param7, param8 = struct.unpack_from('8B', xbt, 12)
    unk1, unk2, unk3 = struct.unpack_from('<III', xbt, 20)

    mips_name = b''
    if hdr_len > 32:
        raw = xbt[32:hdr_len]
        end = raw.find(b'\x00')
        mips_name = raw[:end] if end >= 0 else raw

    meta = {
        'version':    version,
        'hdr_len':    hdr_len,
        'param1':     param1,
        'param2':     param2,
        'param3':     param3,
        'param4':     param4,
        'param5':     param5,
        'mips_count': mips_count,
        'param7':     param7,
        'param8':     param8,
        'unk1':       unk1,
        'unk2':       unk2,
        'unk3':       unk3,
        'mips_name':  mips_name.decode('latin-1'),
    }
    return meta, xbt[hdr_len:]


def build_xbt(meta: dict, payload: bytes) -> bytes:
    """Rebuild XBT bytes from metadata + payload."""
    mips_name_b = meta['mips_name'].encode('latin-1')

    body = struct.pack('<III', XBT_MAGIC, meta['version'], 0)
    body += struct.pack('8B',
        meta['param1'], meta['param2'], meta['param3'], meta['param4'],
        meta['param5'], meta['mips_count'], meta['param7'], meta['param8'])
    body += struct.pack('<III', meta['unk1'], meta['unk2'], meta['unk3'])

    if mips_name_b:
        body += mips_name_b
    body += b'\x00'
    pad = (4 - (len(body) % 4)) % 4
    body += b'\x00' * pad

    hdr_len = len(body)
    body = body[:8] + struct.pack('<I', hdr_len) + body[12:]
    return body + payload


# ---------------------------------------------------------------------------
# PNG export via Pillow (optional)
# ---------------------------------------------------------------------------

def dds_to_png(dds: bytes, png_path: str) -> bool:
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(dds))
        img.save(png_path)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Extract command
# ---------------------------------------------------------------------------

def cmd_extract(args):
    xbt_path      = args.xbt
    out_dir       = args.output
    to_png        = args.png
    dxgi_override = getattr(args, 'dxgi', None)
    orbis_tools   = getattr(args, 'orbis_tools', None)

    with open(xbt_path, 'rb') as f:
        xbt_data = f.read()

    meta, payload = parse_xbt(xbt_data)
    print(f"XBT version:  {meta['version']}")
    print(f"Header len:   {meta['hdr_len']}")
    print(f"Mips name:    {meta['mips_name']!r}")
    print(f"Payload:      {len(payload)} bytes")

    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(xbt_path))[0]

    is_ps4 = not payload.startswith(b'DDS ')

    if is_ps4:
        print("Format:       PS4 (GnfSurface + swizzled pixels)")
        gnf = parse_gnf_surface(payload)

        if dxgi_override is not None:
            gnf['dxgi_fmt'] = dxgi_override
            print(f"DXGI override: {dxgi_override}")

        dxgi_fmt = gnf['dxgi_fmt']
        width    = gnf['width']
        height   = gnf['height']
        print(f"Dimensions:   {width}×{height}")
        print(f"DXGI format:  {dxgi_fmt}")

        if _DXGI_BLOCKS.get(dxgi_fmt) is None:
            raise ValueError(
                f"Unknown DXGI format {dxgi_fmt}. Use --dxgi <num> to specify manually."
            )

        meta['ps4_xbt']        = True
        meta['raw_descriptor'] = gnf['raw_descriptor']
        meta['ps4_width']      = width
        meta['ps4_height']     = height
        meta['ps4_dxgi_fmt']   = dxgi_fmt

        if orbis_tools:
            raw_desc = bytes.fromhex(gnf['raw_descriptor'])
            print("Deswizzling via libSceGpuAddress.dll ...")
            linear = ps4_detile(orbis_tools, gnf['pixel_data'], raw_desc)
            dds_header = build_dds_header(width, height, dxgi_fmt, mip_count=1)
            dds_data   = dds_header + linear
        else:
            print("[warn] No --orbis-tools provided. Outputting raw swizzled DDS.")
            print("       Pixel data will be scrambled without libSceGpuAddress.dll.")
            print("       Pass --orbis-tools <dir> with libSceGpuAddress.dll for correct output.")
            dds_header = build_dds_header(width, height, dxgi_fmt, mip_count=1)
            dds_data   = dds_header + gnf['pixel_data']

    else:
        print("Format:       PC (DDS payload)")
        dds_data = payload
        try:
            dds_info = parse_dds_header(dds_data)
            print(f"DDS size:     {dds_info['width']}×{dds_info['height']}")
            print(f"Mip levels:   {dds_info['mip_count']}")
            print(f"Block:        {dds_info['block_w']}×{dds_info['block_h']} {dds_info['block_bytes']}B/block")
        except Exception as ex:
            print(f"[warn] Could not parse DDS header: {ex}")

    dds_out = os.path.join(out_dir, base + '.dds')
    with open(dds_out, 'wb') as f:
        f.write(dds_data)
    print(f"Saved DDS:    {dds_out}")

    if to_png:
        png_out = os.path.join(out_dir, base + '.png')
        ok = dds_to_png(dds_data, png_out)
        if ok:
            print(f"Saved PNG:    {png_out}")
        else:
            print("[warn] PNG export failed – install Pillow or check format.")

    meta_out = os.path.join(out_dir, base + '.xbt.meta.json')
    with open(meta_out, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)
    print(f"Saved meta:   {meta_out}")


# ---------------------------------------------------------------------------
# Import command
# ---------------------------------------------------------------------------

def cmd_import(args):
    dds_path    = args.dds
    meta_path   = args.meta
    out_path    = args.output
    orbis_tools = getattr(args, 'orbis_tools', None)

    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)

    with open(dds_path, 'rb') as f:
        dds_data = f.read()

    is_ps4 = meta.get('ps4_xbt', False)

    is_gnf_input = dds_data[:4] == b'GNF '
    dds_info = None

    if is_gnf_input:
        print("Input is GNF format – pixel data is already tiled, using directly.")
    else:
        try:
            dds_info = parse_dds_header(dds_data)
            print(f"DDS size:     {dds_info['width']}×{dds_info['height']}")
            print(f"Block:        {dds_info['block_w']}×{dds_info['block_h']} {dds_info['block_bytes']}B/block")
        except Exception as ex:
            raise RuntimeError(f"Cannot parse DDS header: {ex}")

    if is_ps4:
        raw_desc = bytes.fromhex(meta['raw_descriptor'])
        dxgi_fmt = meta.get('ps4_dxgi_fmt', 99)

        if is_gnf_input:
            # GNF file contains pre-tiled PS4 pixels after the 256-byte header
            swizzled = read_gnf_pixels(dds_data)
            print(f"GNF pixels:   {len(swizzled)} bytes")
            ps4_payload = raw_desc + swizzled
            xbt_data = build_xbt(meta, ps4_payload)
            print(f"PS4 payload:  {len(ps4_payload)} bytes  "
                  f"({GNF_SURFACE_SIZE}B descriptor + {len(swizzled)}B pixels)")
            with open(out_path, 'wb') as f:
                f.write(xbt_data)
            print(f"Saved XBT:    {out_path}  ({len(xbt_data)} bytes)")
            return

        linear = dds_data[dds_info['data_offset']:]

        if orbis_tools:
            # Prefer libSceGpuAddress.dll tile (fast, no subprocess)
            dll_path = os.path.join(orbis_tools, 'libSceGpuAddress.dll')
            if os.path.isfile(dll_path):
                print("Swizzling for PS4 via libSceGpuAddress.dll ...")
                swizzled = ps4_tile(orbis_tools, linear, raw_desc)
            else:
                # Fallback: orbis-image2gnf.exe
                print("libSceGpuAddress.dll not found; using orbis-image2gnf.exe ...")
                swizzled = _import_via_orbis_exe(
                    dds_path, dxgi_fmt, orbis_tools)
        else:
            print("[warn] No --orbis-tools provided; PS4 swizzle will be incorrect.")
            swizzled = linear  # pass through unchanged as warning

        ps4_payload = raw_desc + swizzled
        xbt_data = build_xbt(meta, ps4_payload)
        print(f"PS4 payload:  {len(ps4_payload)} bytes  "
              f"({GNF_SURFACE_SIZE}B descriptor + {len(swizzled)}B pixels)")
    else:
        xbt_data = build_xbt(meta, dds_data)

    with open(out_path, 'wb') as f:
        f.write(xbt_data)
    print(f"Saved XBT:    {out_path}  ({len(xbt_data)} bytes)")


def _import_via_orbis_exe(dds_path: str, dxgi_fmt: int, orbis_tools: str) -> bytes:
    """Fallback: use orbis-image2gnf.exe DDS→GNF, extract swizzled pixels."""
    tmp_dir = tempfile.mkdtemp(prefix='xbt_orbis_')
    try:
        gnf_out = os.path.join(tmp_dir, 'swizzled.gnf')
        orbis_dds_to_gnf(orbis_tools, dds_path, gnf_out, dxgi_fmt)
        with open(gnf_out, 'rb') as f:
            gnf_data = f.read()
        swizzled = read_gnf_pixels(gnf_data)
        print(f"Swizzled pixels: {len(swizzled)} bytes")
        return swizzled
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='XBT texture extractor/importer with PS4 support'
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    # extract
    p_ext = sub.add_parser('extract', help='Extract XBT → DDS (+ optional PNG)')
    p_ext.add_argument('xbt',    help='Input .xbt file')
    p_ext.add_argument('output', help='Output directory')
    p_ext.add_argument('--png',  action='store_true',
                       help='Also export PNG (requires Pillow)')
    p_ext.add_argument('--dxgi', type=int, default=None, metavar='FMT',
                       help='Override DXGI format (e.g. 99=BC7_SRGB, 80=BC4)')
    p_ext.add_argument('--orbis-tools', metavar='DIR', dest='orbis_tools',
                       help='Directory with libSceGpuAddress.dll for correct PS4 unswizzle')

    # import
    p_imp = sub.add_parser('import', help='Import DDS → XBT')
    p_imp.add_argument('dds',    help='Input .dds file (linear/edited)')
    p_imp.add_argument('meta',   help='Metadata JSON produced by extract')
    p_imp.add_argument('output', help='Output .xbt file')
    p_imp.add_argument('--orbis-tools', metavar='DIR', dest='orbis_tools',
                       help='Directory with libSceGpuAddress.dll for correct PS4 swizzle')

    args = parser.parse_args()

    if args.cmd == 'extract':
        cmd_extract(args)
    elif args.cmd == 'import':
        cmd_import(args)


if __name__ == '__main__':
    main()
