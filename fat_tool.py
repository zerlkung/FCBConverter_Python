#!/usr/bin/env python3
"""
fat_tool.py - FAT/DAT archive extractor/importer for Far Cry Primal

Confirmed format from actual game files:
  - FAT version 9 (Dunia2 V9 format) for BOTH PC and PS4
  - PC:  platform=0 (Any),     comp_ver=0  → compression scheme 1 = LZO1x
  - PS4: platform=4 (PS4),     comp_ver=2  → compression scheme 1 = LZ4
  - Both are little-endian

V9 entry layout (20 bytes, 5×uint32):
  a: hash high 32 bits
  b: hash low  32 bits
  c: uncompressed_size[31:2] | compression_scheme[1:0]
  d: offset[33:2]
  e: offset[1:0] | compressed_size[29:0]

Usage:
  # Auto-loads *.xml filelists placed next to this script (e.g. FCP.xml)
  python fat_tool.py extract DATA0.fat DATA0.dat ./output

  # Explicit overrides (merged with auto-loaded)
  python fat_tool.py extract DATA0.fat DATA0.dat ./output --names extra.xml
  python fat_tool.py extract DATA0.fat DATA0.dat ./output --filelist names.filelist

  python fat_tool.py import  DATA0.fat DATA0.dat ./input  DATA0_new.fat DATA0_new.dat
  python fat_tool.py hashlist installpkg.filelist [...] [-o hashlist.json]
  python fat_tool.py info    DATA0.fat

Game filelist format (XML, drop next to script):
  FCP.xml  → Far Cry Primal
  FC4.xml  → Far Cry 4
  etc.

  <filelist game="Far Cry Primal">
      <File name="path\\to\\file.ext" />
      ...
  </filelist>
"""

import os
import struct
import json
import argparse
import xml.etree.ElementTree as ET
import zlib

# ---------------------------------------------------------------------------
# CRC64 hash – Gibbed.Dunia Hashing/CRC64.cs  (used for all file name hashing)
# ---------------------------------------------------------------------------
_CRC64_TABLE = [
    0x0000000000000000, 0x01B0000000000000, 0x0360000000000000, 0x02D0000000000000,
    0x06C0000000000000, 0x0770000000000000, 0x05A0000000000000, 0x0410000000000000,
    0x0D80000000000000, 0x0C30000000000000, 0x0EE0000000000000, 0x0F50000000000000,
    0x0B40000000000000, 0x0AF0000000000000, 0x0820000000000000, 0x0990000000000000,
    0x1B00000000000000, 0x1AB0000000000000, 0x1860000000000000, 0x19D0000000000000,
    0x1DC0000000000000, 0x1C70000000000000, 0x1EA0000000000000, 0x1F10000000000000,
    0x1680000000000000, 0x1730000000000000, 0x15E0000000000000, 0x1450000000000000,
    0x1040000000000000, 0x11F0000000000000, 0x1320000000000000, 0x1290000000000000,
    0x3600000000000000, 0x37B0000000000000, 0x3560000000000000, 0x34D0000000000000,
    0x30C0000000000000, 0x3170000000000000, 0x33A0000000000000, 0x3210000000000000,
    0x3B80000000000000, 0x3A30000000000000, 0x38E0000000000000, 0x3950000000000000,
    0x3D40000000000000, 0x3CF0000000000000, 0x3E20000000000000, 0x3F90000000000000,
    0x2D00000000000000, 0x2CB0000000000000, 0x2E60000000000000, 0x2FD0000000000000,
    0x2BC0000000000000, 0x2A70000000000000, 0x28A0000000000000, 0x2910000000000000,
    0x2080000000000000, 0x2130000000000000, 0x23E0000000000000, 0x2250000000000000,
    0x2640000000000000, 0x27F0000000000000, 0x2520000000000000, 0x2490000000000000,
    0x6C00000000000000, 0x6DB0000000000000, 0x6F60000000000000, 0x6ED0000000000000,
    0x6AC0000000000000, 0x6B70000000000000, 0x69A0000000000000, 0x6810000000000000,
    0x6180000000000000, 0x6030000000000000, 0x62E0000000000000, 0x6350000000000000,
    0x6740000000000000, 0x66F0000000000000, 0x6420000000000000, 0x6590000000000000,
    0x7700000000000000, 0x76B0000000000000, 0x7460000000000000, 0x75D0000000000000,
    0x71C0000000000000, 0x7070000000000000, 0x72A0000000000000, 0x7310000000000000,
    0x7A80000000000000, 0x7B30000000000000, 0x79E0000000000000, 0x7850000000000000,
    0x7C40000000000000, 0x7DF0000000000000, 0x7F20000000000000, 0x7E90000000000000,
    0x5A00000000000000, 0x5BB0000000000000, 0x5960000000000000, 0x58D0000000000000,
    0x5CC0000000000000, 0x5D70000000000000, 0x5FA0000000000000, 0x5E10000000000000,
    0x5780000000000000, 0x5630000000000000, 0x54E0000000000000, 0x5550000000000000,
    0x5140000000000000, 0x50F0000000000000, 0x5220000000000000, 0x5390000000000000,
    0x4100000000000000, 0x40B0000000000000, 0x4260000000000000, 0x43D0000000000000,
    0x47C0000000000000, 0x4670000000000000, 0x44A0000000000000, 0x4510000000000000,
    0x4C80000000000000, 0x4D30000000000000, 0x4FE0000000000000, 0x4E50000000000000,
    0x4A40000000000000, 0x4BF0000000000000, 0x4920000000000000, 0x4890000000000000,
    0xD800000000000000, 0xD9B0000000000000, 0xDB60000000000000, 0xDAD0000000000000,
    0xDEC0000000000000, 0xDF70000000000000, 0xDDA0000000000000, 0xDC10000000000000,
    0xD580000000000000, 0xD430000000000000, 0xD6E0000000000000, 0xD750000000000000,
    0xD340000000000000, 0xD2F0000000000000, 0xD020000000000000, 0xD190000000000000,
    0xC300000000000000, 0xC2B0000000000000, 0xC060000000000000, 0xC1D0000000000000,
    0xC5C0000000000000, 0xC470000000000000, 0xC6A0000000000000, 0xC710000000000000,
    0xCE80000000000000, 0xCF30000000000000, 0xCDE0000000000000, 0xCC50000000000000,
    0xC840000000000000, 0xC9F0000000000000, 0xCB20000000000000, 0xCA90000000000000,
    0xEE00000000000000, 0xEFB0000000000000, 0xED60000000000000, 0xECD0000000000000,
    0xE8C0000000000000, 0xE970000000000000, 0xEBA0000000000000, 0xEA10000000000000,
    0xE380000000000000, 0xE230000000000000, 0xE0E0000000000000, 0xE150000000000000,
    0xE540000000000000, 0xE4F0000000000000, 0xE620000000000000, 0xE790000000000000,
    0xF500000000000000, 0xF4B0000000000000, 0xF660000000000000, 0xF7D0000000000000,
    0xF3C0000000000000, 0xF270000000000000, 0xF0A0000000000000, 0xF110000000000000,
    0xF880000000000000, 0xF930000000000000, 0xFBE0000000000000, 0xFA50000000000000,
    0xFE40000000000000, 0xFFF0000000000000, 0xFD20000000000000, 0xFC90000000000000,
    0xB400000000000000, 0xB5B0000000000000, 0xB760000000000000, 0xB6D0000000000000,
    0xB2C0000000000000, 0xB370000000000000, 0xB1A0000000000000, 0xB010000000000000,
    0xB980000000000000, 0xB830000000000000, 0xBAE0000000000000, 0xBB50000000000000,
    0xBF40000000000000, 0xBEF0000000000000, 0xBC20000000000000, 0xBD90000000000000,
    0xAF00000000000000, 0xAEB0000000000000, 0xAC60000000000000, 0xADD0000000000000,
    0xA9C0000000000000, 0xA870000000000000, 0xAAA0000000000000, 0xAB10000000000000,
    0xA280000000000000, 0xA330000000000000, 0xA1E0000000000000, 0xA050000000000000,
    0xA440000000000000, 0xA5F0000000000000, 0xA720000000000000, 0xA690000000000000,
    0x8200000000000000, 0x83B0000000000000, 0x8160000000000000, 0x80D0000000000000,
    0x84C0000000000000, 0x8570000000000000, 0x87A0000000000000, 0x8610000000000000,
    0x8F80000000000000, 0x8E30000000000000, 0x8CE0000000000000, 0x8D50000000000000,
    0x8940000000000000, 0x88F0000000000000, 0x8A20000000000000, 0x8B90000000000000,
    0x9900000000000000, 0x98B0000000000000, 0x9A60000000000000, 0x9BD0000000000000,
    0x9FC0000000000000, 0x9E70000000000000, 0x9CA0000000000000, 0x9D10000000000000,
    0x9480000000000000, 0x9530000000000000, 0x97E0000000000000, 0x9650000000000000,
    0x9240000000000000, 0x93F0000000000000, 0x9120000000000000, 0x9090000000000000,
]


def crc64(s: str) -> int:
    """CRC64 of lowercased string — matches Gibbed.Dunia NameHasher64."""
    h = 0
    for c in s.lower():
        h = _CRC64_TABLE[(h ^ ord(c)) & 0xFF] ^ (h >> 8)
    return h & 0xFFFFFFFFFFFFFFFF


# ---------------------------------------------------------------------------
# Platform / compression helpers
# ---------------------------------------------------------------------------

PLATFORM_NAMES = {0: 'Any', 1: 'PC', 2: 'X360', 3: 'PS3', 4: 'PS4'}

# Platforms that use little-endian: Any(0), PC(1), PS4(4)
# Big-endian: X360(2), PS3(3)
def _is_little_endian(platform: int) -> bool:
    return platform not in (2, 3)

# Compression scheme byte → name, keyed by comp_ver
# comp_ver=0 (PC):  1=LZO1x
# comp_ver=2 (PS4): 1=LZ4
COMP_NONE  = 0
COMP_LZO1X = 1   # PC (comp_ver=0)
COMP_LZ4   = 1   # PS4 (comp_ver=2)  — same byte value, meaning differs by comp_ver


def scheme_name(scheme: int, comp_ver: int) -> str:
    if scheme == 0:
        return 'None'
    if scheme == 1:
        return 'LZ4' if comp_ver >= 2 else 'LZO1x'
    if scheme == 2:
        return 'LZ4' if comp_ver < 2 else 'Zlib'
    return f'Unknown({scheme})'


# ---------------------------------------------------------------------------
# V9 entry parse / serialize  (Gibbed.Dunia2 EntrySerializerV9.cs)
#
#  a: hash[63:32]
#  b: hash[31:0]
#  c: uncompressed[31:2] | scheme[1:0]
#  d: offset[33:2]
#  e: offset[1:0] | compressed[29:0]
# ---------------------------------------------------------------------------
ENTRY_SIZE = 20


def parse_entry_v9(data: bytes, off: int, big: bool = False) -> dict:
    fmt = '>IIIII' if big else '<IIIII'
    a, b, c, d, e = struct.unpack_from(fmt, data, off)
    return {
        'hash':         ((a << 32) | b) & 0xFFFFFFFFFFFFFFFF,
        'uncompressed': (c >> 2) & 0x3FFFFFFF,
        'scheme':       c & 0x3,
        'offset':       ((d << 2) | ((e >> 30) & 0x3)),
        'compressed':   e & 0x3FFFFFFF,
    }


def serialize_entry_v9(e: dict, big: bool = False) -> bytes:
    fmt = '>IIIII' if big else '<IIIII'
    h    = e['hash'] & 0xFFFFFFFFFFFFFFFF
    a    = (h >> 32) & 0xFFFFFFFF
    b    = h & 0xFFFFFFFF
    c    = ((e['uncompressed'] & 0x3FFFFFFF) << 2) | (e['scheme'] & 0x3)
    off  = e['offset']
    d    = (off >> 2) & 0xFFFFFFFF
    ee   = ((off & 0x3) << 30) | (e['compressed'] & 0x3FFFFFFF)
    return struct.pack(fmt, a, b, c, d, ee)


# ---------------------------------------------------------------------------
# FAT header parse
# ---------------------------------------------------------------------------
FAT_MAGIC = 0x46415432  # 'FAT2'


def parse_fat(fat_path: str) -> dict:
    with open(fat_path, 'rb') as f:
        data = f.read()

    off = 0
    magic = struct.unpack_from('<I', data, off)[0]; off += 4
    if magic != FAT_MAGIC:
        raise ValueError(f'Bad magic 0x{magic:08X} — expected FAT2 (0x{FAT_MAGIC:08X})')

    ver_raw  = struct.unpack_from('<I', data, off)[0]; off += 4
    version  = ver_raw & 0x7FFFFFFF
    encrypted = bool(ver_raw & 0x80000000)

    if version != 9:
        raise ValueError(
            f'Unsupported FAT version {version}. '
            f'This tool supports version 9 (Far Cry Primal PC/PS4).'
        )

    flags    = struct.unpack_from('<I', data, off)[0]; off += 4
    platform = flags & 0xFF
    comp_ver = (flags >> 8) & 0xFF

    big = not _is_little_endian(platform)
    efmt = '>i' if big else '<i'

    # V9 subfat counts
    subfat_total = struct.unpack_from('<i', data, off)[0]; off += 4
    subfat_count = struct.unpack_from('<i', data, off)[0]; off += 4

    # Main entries
    entry_count = struct.unpack_from('<i', data, off)[0]; off += 4
    entries = []
    for _ in range(entry_count):
        entries.append(parse_entry_v9(data, off, big))
        off += ENTRY_SIZE

    # unknown1 count (always 0 per BigFile.cs)
    unk1_count = struct.unpack_from('<I', data, off)[0]; off += 4

    # unknown2 count (V7+)
    unk2_count = struct.unpack_from('<I', data, off)[0]; off += 4
    off += unk2_count * 16

    # SubFATs
    subfats = []
    for _ in range(subfat_count):
        sf_count = struct.unpack_from('<I', data, off)[0]; off += 4
        sf_entries = []
        for _ in range(sf_count):
            sf_entries.append(parse_entry_v9(data, off, big))
            off += ENTRY_SIZE
        subfats.append(sf_entries)

    return {
        'version':      version,
        'platform':     platform,
        'comp_ver':     comp_ver,
        'encrypted':    encrypted,
        'big_endian':   big,
        'entries':      entries,
        'subfats':      subfats,
        '_header_raw':  data[:off],   # preserve exact header for re-serialisation
    }


def _collect_all_entries(fat: dict) -> list:
    """Flatten main + subfat entries into one list."""
    all_entries = list(fat['entries'])
    for sf in fat['subfats']:
        all_entries.extend(sf)
    return all_entries


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

def decompress_entry(entry: dict, raw: bytes, comp_ver: int) -> bytes:
    scheme = entry['scheme']
    if scheme == 0:
        return raw
    if scheme == 1:
        if comp_ver >= 2:
            return _lz4_decompress(raw, entry['uncompressed'])
        else:
            return _lzo_decompress(raw, entry['uncompressed'])
    if scheme == 2:
        if comp_ver >= 2:
            return _zlib_decompress(raw, entry['uncompressed'])
        else:
            return _lz4_decompress(raw, entry['uncompressed'])
    raise ValueError(f"Unknown compression scheme {scheme}")


def compress_for_scheme(data: bytes, comp_ver: int):
    """Returns (compressed_bytes, scheme_id). Falls back to uncompressed."""
    if comp_ver >= 2:
        # Try LZ4 first (scheme 1), fall back to Zlib (scheme 2), then none
        result = _lz4_compress(data)
        if result is not None and len(result) < len(data):
            return result, 1
        result = _zlib_compress(data)
        if result is not None and len(result) < len(data):
            return result, 2
    else:
        result = _lzo_compress(data)
        if result is not None and len(result) < len(data):
            return result, 1
    return data, 0


def _lz4_decompress(data: bytes, size: int) -> bytes:
    try:
        import lz4.block
        return bytes(lz4.block.decompress(data, uncompressed_size=size))
    except ImportError:
        raise RuntimeError("Install lz4:  pip install lz4")


def _lz4_compress(data: bytes):
    try:
        import lz4.block
        return lz4.block.compress(data, store_size=False)
    except ImportError:
        return None


def _zlib_decompress(data: bytes, size: int) -> bytes:
    # Try standard zlib header first, then raw deflate
    try:
        return zlib.decompress(data)
    except zlib.error:
        return zlib.decompress(data, -15)   # raw deflate (no header)


def _zlib_compress(data: bytes) -> bytes:
    return zlib.compress(data, level=6)


def _lzo_decompress(data: bytes, size: int) -> bytes:
    try:
        import lzo
        return lzo.decompress(data, False, size)
    except ImportError:
        raise RuntimeError("Install python-lzo:  pip install python-lzo")


def _lzo_compress(data: bytes):
    try:
        import lzo
        return lzo.compress(data)
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Filelist / XML name-map loading  (hash → filepath mapping)
# ---------------------------------------------------------------------------

# Directory where this script lives — used for auto-discovery of *.xml filelists
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_names_xml(path: str) -> dict:
    """Load hash→name mapping from an XML filelist (any element with a 'name' attr)."""
    mapping: dict = {}
    if not os.path.isfile(path):
        print(f'  [warn] XML filelist not found: {path}')
        return mapping
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        for elem in root.iter():
            name = elem.get('name')
            if name:
                mapping[crc64(name)] = name
        print(f'  Loaded {path}  ({len(mapping)} names)')
    except ET.ParseError as exc:
        print(f'  [warn] XML parse error in {path}: {exc}')
    return mapping


def load_filelists(*paths: str) -> dict:
    """Load hash→name mapping from plain-text filelists (one path per line)."""
    mapping: dict = {}
    for p in paths:
        if not os.path.isfile(p):
            print(f'  [warn] filelist not found: {p}')
            continue
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.rstrip('\r\n')
                if not line or line.startswith(';'):
                    continue
                mapping[crc64(line)] = line
        print(f'  Loaded {p}  ({len(mapping)} hashes so far)')
    return mapping


def load_game_xml(game: str) -> dict:
    """Load <GAME>.xml from the script's directory, e.g. --game FCP → FCP.xml."""
    path = os.path.join(_SCRIPT_DIR, f'{game.upper()}.xml')
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f'Game filelist not found: {path}\n'
            f'Create {game.upper()}.xml next to fat_tool.py with the filenames you need.'
        )
    return load_names_xml(path)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_info(args):
    fat = parse_fat(args.fat)
    platform_str = PLATFORM_NAMES.get(fat['platform'], f"Unknown({fat['platform']})")
    all_entries  = _collect_all_entries(fat)
    schemes      = {}
    for e in all_entries:
        n = scheme_name(e['scheme'], fat['comp_ver'])
        schemes[n] = schemes.get(n, 0) + 1

    print(f"FAT version:  {fat['version']}")
    print(f"Platform:     {fat['platform']} ({platform_str})")
    print(f"Comp version: {fat['comp_ver']}")
    print(f"Encrypted:    {fat['encrypted']}")
    print(f"Endian:       {'big' if fat['big_endian'] else 'little'}")
    print(f"Main entries: {len(fat['entries'])}")
    print(f"SubFATs:      {len(fat['subfats'])} (entries: {sum(len(s) for s in fat['subfats'])})")
    print(f"Total:        {len(all_entries)}")
    print(f"Compression:  {schemes}")


def cmd_extract(args):
    fat_path = args.fat
    dat_path = args.dat
    out_dir  = args.output

    print(f'Parsing {fat_path} ...')
    fat = parse_fat(fat_path)
    all_entries = _collect_all_entries(fat)
    platform_str = PLATFORM_NAMES.get(fat['platform'], f"Unknown({fat['platform']})")
    print(f'  Version={fat["version"]}  Platform={fat["platform"]} ({platform_str})'
          f'  comp_ver={fat["comp_ver"]}  entries={len(all_entries)}')

    # Build name map from explicit sources only (no auto-loading)
    name_map: dict = {}

    if args.game:
        name_map.update(load_game_xml(args.game))

    if args.names:
        for p in args.names:
            name_map.update(load_names_xml(p))

    if args.filelist:
        name_map.update(load_filelists(*args.filelist))

    print(f'  Name map: {len(name_map)} entries')

    if not name_map:
        print('  [error] No names loaded — add filenames to FCP.xml (or use --names / --filelist)')
        return

    os.makedirs(out_dir, exist_ok=True)

    manifest_entries = []
    extracted = 0

    with open(dat_path, 'rb') as dat_f:
        for entry in all_entries:
            h    = entry['hash']
            path = name_map.get(h)
            if not path:
                continue   # skip entries not in the filelist

            rel      = path.replace('\\', os.sep).replace('/', os.sep).lstrip(os.sep)
            out_path = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)

            read_size = entry['compressed'] if entry['compressed'] else entry['uncompressed']
            if read_size > 0:
                dat_f.seek(entry['offset'])
                raw = dat_f.read(read_size)
                try:
                    decompressed = decompress_entry(entry, raw, fat['comp_ver'])
                except Exception as ex:
                    print(f'  [warn] decompress {path}: {ex}')
                    decompressed = raw
            else:
                decompressed = b''

            with open(out_path, 'wb') as out_f:
                out_f.write(decompressed)

            print(f'  extracted: {path}')
            extracted += 1

            manifest_entries.append({
                'hash':         f'{h:016X}',
                'path':         path,
                'offset':       entry['offset'],
                'compressed':   entry['compressed'],
                'uncompressed': entry['uncompressed'],
                'scheme':       entry['scheme'],
            })

    # Report any names in the filelist that were NOT found in this archive
    found_hashes = {int(e['hash'], 16) for e in manifest_entries}
    for h, path in name_map.items():
        if h not in found_hashes:
            print(f'  [not found] {path}')

    manifest = {
        'fat_version': fat['version'],
        'platform':    fat['platform'],
        'comp_ver':    fat['comp_ver'],
        'entries':     manifest_entries,
    }
    manifest_path = os.path.join(out_dir, '_manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)

    print(f'\n  Extracted: {extracted}/{len(name_map)} listed files')
    print(f'  Output:    {out_dir}')
    print(f'  Manifest:  {manifest_path}')


def cmd_import(args):
    fat_path     = args.fat
    dat_path     = args.dat
    in_dir       = args.input
    out_fat_path = args.out_fat
    out_dat_path = args.out_dat

    manifest_path = os.path.join(in_dir, '_manifest.json')
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f'Manifest not found: {manifest_path}\nRun extract first.')

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    comp_ver = manifest.get('comp_ver', 0)
    platform = manifest.get('platform', 0)
    big      = not _is_little_endian(platform)

    print(f'Parsing original {fat_path} ...')
    fat = parse_fat(fat_path)
    all_orig = _collect_all_entries(fat)

    man_by_hash = {int(e['hash'], 16): e for e in manifest['entries']}

    print(f'Building new DAT ...')
    new_dat     = bytearray()
    new_entries = []  # parallel to all_orig

    with open(dat_path, 'rb') as dat_f:
        for orig in all_orig:
            h           = orig['hash']
            man         = man_by_hash.get(h)
            replacement = None

            if man:
                rel         = man['path'].replace('/', os.sep).replace('\\', os.sep)
                candidate   = os.path.join(in_dir, rel)
                if os.path.isfile(candidate):
                    replacement = candidate

            if replacement:
                with open(replacement, 'rb') as rf:
                    raw_data = rf.read()
                comp_data, scheme = compress_for_scheme(raw_data, comp_ver)
                uncompressed = len(raw_data) if scheme != 0 else 0
            else:
                read_size = orig['compressed'] if orig['compressed'] else orig['uncompressed']
                dat_f.seek(orig['offset'])
                comp_data    = dat_f.read(read_size)
                scheme       = orig['scheme']
                uncompressed = orig['uncompressed']

            # 4-byte alignment (V9 offset is << 2, so must be multiple of 4)
            pad = (4 - (len(new_dat) % 4)) % 4
            new_dat += b'\x00' * pad

            offset = len(new_dat)
            new_dat += comp_data

            new_entry = dict(orig)
            new_entry.update({
                'offset':       offset,
                'compressed':   len(comp_data),
                'uncompressed': uncompressed,
                'scheme':       scheme,
            })
            new_entries.append(new_entry)

    print(f'Writing {out_dat_path} ...')
    with open(out_dat_path, 'wb') as f:
        f.write(new_dat)

    # Rebuild FAT: header (unchanged) + new entry bytes + tail
    # Header layout for V9:
    #   magic(4) + ver_raw(4) + flags(4) + subfat_total(4) + subfat_count(4)
    #   + entry_count(4)  = 24 bytes
    print(f'Writing {out_fat_path} ...')
    with open(fat_path, 'rb') as f:
        orig_fat = f.read()

    HDR_SIZE = 24  # fixed for V9
    hdr = bytearray(orig_fat[:HDR_SIZE])

    # Split new_entries back into main / subfat groups
    main_count = len(fat['entries'])
    new_main   = new_entries[:main_count]
    new_subfat = new_entries[main_count:]

    struct.pack_into('<i', hdr, 20, len(new_main))   # patch main entry count

    entry_bytes = bytearray()
    for e in new_main:
        entry_bytes += serialize_entry_v9(e, big)

    # unk1_count=0, unk2_count=0  (2×4 bytes)
    entry_bytes += struct.pack('<II', 0, 0)

    # SubFATs (rewrite with updated entries)
    sf_offset = main_count
    for sf_orig in fat['subfats']:
        sf_new = new_entries[sf_offset: sf_offset + len(sf_orig)]
        sf_offset += len(sf_orig)
        entry_bytes += struct.pack('<I', len(sf_new))
        for e in sf_new:
            entry_bytes += serialize_entry_v9(e, big)

    with open(out_fat_path, 'wb') as f:
        f.write(hdr)
        f.write(entry_bytes)

    print(f'Done. {len(new_entries)} entries written.')


def cmd_pack(args):
    """
    Create a brand-new FAT/DAT containing ONLY the files in the input directory.
    The original FAT is used only to copy the header flags (platform, comp_ver,
    version) so the new archive matches the target platform (PC or PS4).

    Entry order in the new FAT is sorted by hash (same convention as Dunia).
    Entries not found on disk are silently skipped.
    """
    in_dir       = args.input
    out_fat_path = args.out_fat
    out_dat_path = args.out_dat

    manifest_path = os.path.join(in_dir, '_manifest.json')
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f'Manifest not found: {manifest_path}\nRun extract first.')

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    platform = manifest.get('platform', 0)
    comp_ver  = manifest.get('comp_ver', 0)
    big       = not _is_little_endian(platform)
    platform_str = PLATFORM_NAMES.get(platform, f'Unknown({platform})')

    print(f'Packing  platform={platform} ({platform_str})  comp_ver={comp_ver}')

    new_dat     = bytearray()
    new_entries = []

    # Sort entries by hash for consistency (Dunia uses sorted order)
    sorted_entries = sorted(manifest['entries'], key=lambda e: int(e['hash'], 16))

    for man in sorted_entries:
        rel       = man['path'].replace('/', os.sep).replace('\\', os.sep)
        file_path = os.path.join(in_dir, rel)

        if not os.path.isfile(file_path):
            print(f'  [skip] not on disk: {man["path"]}')
            continue

        with open(file_path, 'rb') as rf:
            raw_data = rf.read()

        comp_data, scheme = compress_for_scheme(raw_data, comp_ver)
        uncompressed = len(raw_data) if scheme != 0 else 0

        # 4-byte alignment
        pad = (4 - (len(new_dat) % 4)) % 4
        new_dat += b'\x00' * pad

        offset = len(new_dat)
        new_dat += comp_data

        new_entries.append({
            'hash':         int(man['hash'], 16),
            'offset':       offset,
            'compressed':   len(comp_data),
            'uncompressed': uncompressed,
            'scheme':       scheme,
        })
        print(f'  packed: {man["path"]}  ({len(raw_data):,} bytes)')

    print(f'Writing {out_dat_path}  ({len(new_dat):,} bytes) ...')
    with open(out_dat_path, 'wb') as f:
        f.write(new_dat)

    # Build FAT header from scratch (V9, no subfats)
    #   magic(4) + ver_raw(4) + flags(4)
    #   + subfat_total(4) + subfat_count(4) + entry_count(4)
    flags   = (platform & 0xFF) | ((comp_ver & 0xFF) << 8)
    hdr     = struct.pack('<III iii',
                          FAT_MAGIC,
                          9,          # version (no encrypt flag)
                          flags,
                          0,          # subfat_total
                          0,          # subfat_count
                          len(new_entries))

    entry_bytes = bytearray()
    for e in new_entries:
        entry_bytes += serialize_entry_v9(e, big)

    # unk1_count=0, unk2_count=0
    entry_bytes += struct.pack('<II', 0, 0)

    print(f'Writing {out_fat_path}  ({len(new_entries)} entries) ...')
    with open(out_fat_path, 'wb') as f:
        f.write(hdr)
        f.write(entry_bytes)

    print(f'Done. New archive has {len(new_entries)} file(s), DAT is {len(new_dat):,} bytes.')


def cmd_hashlist(args):
    mapping = load_filelists(*args.filelists)
    out = args.output or 'hashlist.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({f'{h:016X}': p for h, p in mapping.items()}, f, indent=2)
    print(f'Saved {len(mapping)} hashes → {out}')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='FAT/DAT tool for Far Cry Primal (V9, PC & PS4)'
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('info', help='Print FAT header info')
    p.add_argument('fat')

    p = sub.add_parser('extract', help='Extract files from FAT/DAT')
    p.add_argument('fat')
    p.add_argument('dat')
    p.add_argument('output', help='Output directory')
    p.add_argument('--game', metavar='NAME', default=None,
                   help='Load <NAME>.xml from script dir  (e.g. --game FCP)')
    p.add_argument('--names', nargs='+', metavar='XML', default=None,
                   help='Explicit XML filelist path(s)')
    p.add_argument('--filelist', nargs='+', metavar='FL', default=None,
                   help='Plain-text filelist path(s)')

    p = sub.add_parser('import', help='Repack modified files into FAT/DAT')
    p.add_argument('fat',     help='Original .fat')
    p.add_argument('dat',     help='Original .dat')
    p.add_argument('input',   help='Input dir with _manifest.json')
    p.add_argument('out_fat', help='Output .fat')
    p.add_argument('out_dat', help='Output .dat')

    p = sub.add_parser('pack', help='Create new FAT/DAT with only the extracted files (no original needed)')
    p.add_argument('input',   help='Input dir with _manifest.json and extracted files')
    p.add_argument('out_fat', help='Output .fat')
    p.add_argument('out_dat', help='Output .dat')

    p = sub.add_parser('hashlist', help='Build hash→path JSON from filelist(s)')
    p.add_argument('filelists', nargs='+')
    p.add_argument('-o', '--output')

    args = parser.parse_args()
    {'info': cmd_info, 'extract': cmd_extract, 'pack': cmd_pack,
     'import': cmd_import, 'hashlist': cmd_hashlist}[args.cmd](args)


if __name__ == '__main__':
    main()
