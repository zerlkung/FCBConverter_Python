# Far Cry Primal Tools (Python)

Python tools for modding **Far Cry Primal** (PC & PS4) — archive extraction/reimport and texture extraction/reimport built on the Dunia engine file formats.

---

## Tools

| File | Purpose |
|------|---------|
| `fat_tool.py` | Extract / repack FAT/DAT archives (PC + PS4) |
| `xbt_tool.py` | Extract / repack XBT textures (PC + PS4) |
| `FCP.xml` | Filename list for Far Cry Primal |

---

## Requirements

```
Python 3.10+
lzo      (pip install python-lzo)   ← PC compression
lz4      (pip install lz4)          ← PS4 compression
Pillow   (pip install Pillow)       ← optional, PNG export from xbt_tool
```

### PS4 Texture Swizzle (xbt_tool only)

To correctly deswizzle / reswizzle PS4 textures you need two DLLs from the **PS4 SDK (libSceGpuAddress)**:

```
libSceGpuAddress.dll
libSceGnm.dll
```

Place both in a folder (e.g. `ps4sdk/`) and pass `--orbis-tools ps4sdk` to `xbt_tool.py`.
You must obtain these DLLs yourself from the official or leaked PS4 SDK.

---

## fat_tool.py — FAT/DAT Archive Tool

### File Format

Far Cry Primal uses `.fat` (index) + `.dat` (data) pairs.
Version 9 (Dunia2 V9), little-endian. 20 bytes per entry.

| Platform | Compression |
|----------|------------|
| PC | LZO1x (scheme 1) |
| PS4 | LZ4 (scheme 1, comp_ver 2) |

### Filelist (XML)

Place an XML filelist next to `fat_tool.py` and it will be loaded automatically:

```xml
<filelist game="Far Cry Primal">
    <File name="ui\common\fonts\fire\din_next_w1g_default_1.xbt" />
    <File name="data\worlds\primal\world.fcb" />
    ...
</filelist>
```

`FCP.xml` contains the Far Cry Primal filelist — already included.

### Commands

#### Extract

```bash
python fat_tool.py extract DATA0.fat DATA0.dat ./output
```

Extract all files. Unknown filenames will be saved as `<hash_hex>`.

```bash
python fat_tool.py extract DATA0.fat DATA0.dat ./output --names extra.xml
python fat_tool.py extract DATA0.fat DATA0.dat ./output --filelist names.filelist
```

Merge additional filelists.

#### Import (Repack)

```bash
python fat_tool.py import DATA0.fat DATA0.dat ./input DATA0_new.fat DATA0_new.dat
```

Repack modified files back into a new FAT/DAT pair.
Files in `./input` must use the same relative paths as extracted.

#### Info

```bash
python fat_tool.py info DATA0.fat
```

Print archive statistics (entry count, compression, platform).

#### Hashlist

```bash
python fat_tool.py hashlist installpkg.filelist -o hashlist.json
```

Generate a JSON hash→path lookup from a `.filelist` file.

---

## xbt_tool.py — XBT Texture Tool

### File Format

XBT is the Dunia engine texture container.

| Platform | Payload |
|----------|---------|
| PC | DDS file (raw) |
| PS4 | 44-byte GnfSurface descriptor + GPU-swizzled pixels |

PC vs PS4 is **auto-detected** — no flag needed.

### Supported DDS Formats

`BC1 / BC2 / BC3 / BC4 / BC5 / BC7 / R8G8B8A8`

### Commands

#### Extract

```bash
python xbt_tool.py extract texture.xbt ./out
```

PC: saves `texture.dds` directly.
PS4 (no SDK): saves raw swizzled DDS with a warning.

```bash
python xbt_tool.py extract texture.xbt ./out --orbis-tools ps4sdk/
```

PS4 with SDK: properly deswizzles pixels — gives a correct viewable DDS.

```bash
python xbt_tool.py extract texture.xbt ./out --orbis-tools ps4sdk/ --png
```

Also saves a `.png` alongside (requires Pillow).

```bash
python xbt_tool.py extract texture.xbt ./out --dxgi 99
```

Override DXGI format number if auto-detection is wrong.

#### Import

```bash
python xbt_tool.py import texture_new.dds texture.xbt.meta.json output.xbt
```

PC: wraps the DDS directly.
PS4 (no SDK): passes pixel data through with a warning.

```bash
python xbt_tool.py import texture_new.dds texture.xbt.meta.json output.xbt --orbis-tools ps4sdk/
```

PS4 with SDK: re-swizzles the DDS pixels before packing — gives a correct PS4 XBT.

The `.meta.json` file is created automatically by `extract`. It stores the original XBT header and PS4 GnfSurface descriptor needed to rebuild the file.

**GNF input is also accepted:**

```bash
python xbt_tool.py import texture.gnf texture.xbt.meta.json output.xbt
```

If the input file has GNF format (magic `GNF `) the pre-tiled pixel data is used directly — no re-swizzle step.

---

## Typical Workflow — PS4 Texture Mod

```
1. Extract archive
   python fat_tool.py extract DATA0.fat DATA0.dat ./unpacked

2. Extract texture (deswizzle)
   python xbt_tool.py extract unpacked/ui/common/fonts/fire/din_next_w1g_default_1.xbt ./out --orbis-tools ps4sdk/
   # produces: out/din_next_w1g_default_1.dds
   #           out/din_next_w1g_default_1.xbt.meta.json

3. Edit the DDS in Photoshop / GIMP / etc.

4. Import texture (reswizzle)
   python xbt_tool.py import out/din_next_w1g_default_1.dds out/din_next_w1g_default_1.xbt.meta.json out/new.xbt --orbis-tools ps4sdk/

5. Copy new.xbt back to unpacked/ path

6. Repack archive
   python fat_tool.py import DATA0.fat DATA0.dat ./unpacked DATA0_new.fat DATA0_new.dat
```

---

## Typical Workflow — PC Texture Mod

No SDK needed for PC.

```
1. Extract
   python xbt_tool.py extract texture.xbt ./out
   # produces: out/texture.dds + out/texture.xbt.meta.json

2. Edit texture.dds

3. Import
   python xbt_tool.py import out/texture.dds out/texture.xbt.meta.json out/texture_new.xbt
```

---

## Notes

- **Round-trip accuracy**: PS4 swizzle/deswizzle via `libSceGpuAddress.dll` is bit-perfect (0 byte differences verified).
- **Font textures** in Far Cry Primal use MSDF (Multi-channel Signed Distance Field) encoding — the extracted DDS will look like colorful noise; this is normal. Use the accompanying `.ffd` file for glyph layout.
- PS4 fat archives use **LZ4** compression (not Zlib). scheme=2 entries in language fat files are a different compression not yet identified.
