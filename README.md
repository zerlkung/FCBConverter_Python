# Far Cry Primal Tools (Python)

Python tools สำหรับ mod **Far Cry Primal** (PC & PS4) — แตกไฟล์/แพ็กไฟล์จาก archive และ texture บน Dunia engine

---

## เครื่องมือ

| ไฟล์ | หน้าที่ |
|------|---------|
| `fat_tool.py` | แตก / แพ็ก FAT/DAT archive (PC + PS4) |
| `xbt_tool.py` | แตก / แพ็ก XBT texture (PC + PS4) |
| `FCP.xml` | รายชื่อไฟล์ใน Far Cry Primal |

---

## ความต้องการ

```
Python 3.10+
lzo      (pip install python-lzo)   ← PC compression
lz4      (pip install lz4)          ← PS4 compression
Pillow   (pip install Pillow)       ← optional, export PNG จาก xbt_tool
```

### PS4 Texture Swizzle (xbt_tool เท่านั้น)

สำหรับ deswizzle / reswizzle texture PS4 อย่างถูกต้อง ต้องใช้ DLL สองตัวจาก **PS4 SDK (libSceGpuAddress)**:

```
libSceGpuAddress.dll
libSceGnm.dll
```

วางทั้งสองไฟล์ไว้ในโฟลเดอร์เดียวกัน (เช่น `ps4sdk/`) แล้วส่ง `--orbis-tools ps4sdk` ให้ `xbt_tool.py`
ต้องหา DLL เหล่านี้เองจาก PS4 SDK

---

## fat_tool.py — FAT/DAT Archive Tool

### รูปแบบไฟล์

Far Cry Primal ใช้คู่ `.fat` (index) + `.dat` (data)
Version 9 (Dunia2 V9), little-endian, 20 bytes ต่อ entry

| Platform | Compression |
|----------|------------|
| PC | LZO1x (scheme 1) |
| PS4 | LZ4 (scheme 1, comp_ver 2) |

### Filelist (XML)

วาง XML filelist ไว้ข้างๆ `fat_tool.py` แล้วจะโหลดอัตโนมัติ:

```xml
<filelist game="Far Cry Primal">
    <File name="ui\common\fonts\fire\din_next_w1g_default_1.xbt" />
    <File name="data\worlds\primal\world.fcb" />
</filelist>
```

`FCP.xml` มี filelist ของ Far Cry Primal — รวมมาให้แล้ว

### คำสั่ง

#### Extract (แตกไฟล์)

```bash
python fat_tool.py extract DATA0.fat DATA0.dat ./output
```

แตกไฟล์ทั้งหมดที่มีชื่ออยู่ใน filelist ชื่อไฟล์ที่ไม่รู้จะบันทึกเป็น `<hash_hex>`

```bash
python fat_tool.py extract DATA0.fat DATA0.dat ./output --names extra.xml
python fat_tool.py extract DATA0.fat DATA0.dat ./output --filelist names.filelist
```

เพิ่ม filelist เพิ่มเติม

#### Pack (สร้าง archive ใหม่จาก directory)

```bash
# PS4 (ค่าเริ่มต้น)
python fat_tool.py pack ./my_files patch2.fat patch2.dat

# ระบุ platform ชัดเจน
python fat_tool.py pack ./my_files patch2.fat patch2.dat --platform 4 --comp-ver 2

# PC
python fat_tool.py pack ./my_files patch2.fat patch2.dat --platform 1 --comp-ver 0
```

สร้าง FAT/DAT ใหม่จากไฟล์ทั้งหมดใน directory — **ไม่ต้องใช้ไฟล์ต้นฉบับ ไม่ต้องมี manifest**
คำนวณ CRC64 hash จาก relative path ของแต่ละไฟล์อัตโนมัติ

| `--platform` | ความหมาย |
|-------------|---------|
| 0 | Any |
| 1 | PC |
| 4 | PS4 (ค่าเริ่มต้น) |

| `--comp-ver` | ความหมาย |
|-------------|---------|
| 0 | PC (LZO) |
| 2 | PS4 / LZ4 (ค่าเริ่มต้น) |

> ถ้ามี `_manifest.json` อยู่ใน directory จะใช้ platform/hash จาก manifest แทน (ไม่ต้องใส่ flags)

#### Import (แทนที่ไฟล์ใน archive เดิม)

```bash
python fat_tool.py import DATA0.fat DATA0.dat ./input DATA0_new.fat DATA0_new.dat
```

แทนที่ไฟล์ที่แก้ไขกลับเข้า FAT/DAT ใหม่ — ไฟล์ที่ไม่ได้แก้จะคัดลอกจาก archive เดิม
ไฟล์ใน `./input` ต้องใช้ path เดียวกันกับที่ extract ออกมา

#### Info

```bash
python fat_tool.py info DATA0.fat
```

แสดงข้อมูล archive (จำนวน entry, compression, platform)

#### Hashlist

```bash
python fat_tool.py hashlist installpkg.filelist -o hashlist.json
```

สร้าง JSON hash→path จากไฟล์ `.filelist`

---

## xbt_tool.py — XBT Texture Tool

### รูปแบบไฟล์

XBT คือ container texture ของ Dunia engine

| Platform | Payload |
|----------|---------|
| PC | ไฟล์ DDS โดยตรง |
| PS4 | 44-byte GnfSurface descriptor + pixel ที่ swizzle ด้วย GPU |

PC กับ PS4 **ตรวจจับอัตโนมัติ** — ไม่ต้องใส่ flag

### รูปแบบ DDS ที่รองรับ

`BC1 / BC2 / BC3 / BC4 / BC5 / BC7 / R8G8B8A8`

### คำสั่ง

#### Extract (แตก texture)

```bash
# PC — ไม่ต้องใช้ SDK
python xbt_tool.py extract texture.xbt ./out

# PS4 — deswizzle ถูกต้อง
python xbt_tool.py extract texture.xbt ./out --orbis-tools ps4sdk/

# บันทึก PNG ด้วย (ต้องติดตั้ง Pillow)
python xbt_tool.py extract texture.xbt ./out --orbis-tools ps4sdk/ --png

# กำหนด DXGI format เองถ้า auto-detect ผิด
python xbt_tool.py extract texture.xbt ./out --dxgi 99
```

สร้างไฟล์ `texture.dds` และ `texture.xbt.meta.json` ใน output directory
**ไฟล์ `.meta.json` จำเป็นต้องใช้ตอน import**

#### Import (แพ็ก texture กลับ)

```bash
# PC
python xbt_tool.py import texture_new.dds texture.xbt.meta.json output.xbt

# PS4 — reswizzle ถูกต้อง
python xbt_tool.py import texture_new.dds texture.xbt.meta.json output.xbt --orbis-tools ps4sdk/
```

รองรับไฟล์ input ทั้ง DDS และ GNF:

```bash
# GNF input — ใช้ pixel ที่ tiled แล้วโดยตรง ไม่ต้อง reswizzle
python xbt_tool.py import texture.gnf texture.xbt.meta.json output.xbt
```

---

## ขั้นตอน Mod Texture (PS4)

```
1. แตก archive
   python fat_tool.py extract DATA0.fat DATA0.dat ./unpacked

2. แตก texture (deswizzle)
   python xbt_tool.py extract unpacked/ui/common/fonts/fire/din_next_w1g_default_1.xbt ./out --orbis-tools ps4sdk/
   # ได้: out/din_next_w1g_default_1.dds
   #      out/din_next_w1g_default_1.xbt.meta.json

3. แก้ไข .dds ด้วย Photoshop / GIMP / etc.

4. แพ็ก texture กลับ (reswizzle)
   python xbt_tool.py import out/din_next_w1g_default_1.dds out/din_next_w1g_default_1.xbt.meta.json out/new.xbt --orbis-tools ps4sdk/

5. คัดลอก new.xbt กลับไปที่ path เดิมใน unpacked/

6. แพ็ก archive ใหม่ (สองแบบ)
   # แบบ 1: แทนที่ใน archive เดิม
   python fat_tool.py import DATA0.fat DATA0.dat ./unpacked DATA0_new.fat DATA0_new.dat

   # แบบ 2: สร้าง archive ใหม่จาก directory (ไม่ต้องใช้ต้นฉบับ)
   python fat_tool.py pack ./unpacked DATA0_new.fat DATA0_new.dat
```

## ขั้นตอน Mod Texture (PC)

ไม่ต้องใช้ SDK

```
1. แตก
   python xbt_tool.py extract texture.xbt ./out

2. แก้ไข texture.dds

3. แพ็กกลับ
   python xbt_tool.py import out/texture.dds out/texture.xbt.meta.json out/texture_new.xbt
```

---

## หมายเหตุ

- **Round-trip accuracy**: PS4 swizzle/deswizzle ผ่าน `libSceGpuAddress.dll` ถูกต้อง bit-perfect (ทดสอบแล้ว 0 byte ต่าง)
- **Font texture** ใน Far Cry Primal ใช้ MSDF (Multi-channel Signed Distance Field) — DDS ที่ extract ออกมาจะดูเหมือน noise สีๆ นั่นเป็นเรื่องปกติ ใช้ไฟล์ `.ffd` คู่กันสำหรับตำแหน่ง glyph
- PS4 fat archive ใช้ compression **LZ4** (ไม่ใช่ Zlib)
