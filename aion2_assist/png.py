"""PNG loading: PIL if importable, else a minimal stdlib reader (8-bit, non-interlaced)."""
import struct
import zlib

from .frame import Frame


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def unfilter(ftype: int, line: bytearray, prev: bytearray, bpp: int) -> bytearray:
    for i in range(len(line)):
        a = line[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if ftype == 1:
            line[i] = (line[i] + a) & 255
        elif ftype == 2:
            line[i] = (line[i] + b) & 255
        elif ftype == 3:
            line[i] = (line[i] + ((a + b) >> 1)) & 255
        elif ftype == 4:
            line[i] = (line[i] + _paeth(a, b, c)) & 255
    return line


def read_png_rows(data: bytes):
    """Return (width, height, rows of RGB bytes)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, plte = 8, b"", None
    while pos < len(data):
        ln, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if ctype == b"IHDR":
            w, h, depth, ctp, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif ctype == b"PLTE":
            plte = body
        elif ctype == b"IDAT":
            idat += body
    if depth != 8 or interlace != 0:
        raise ValueError("only 8-bit non-interlaced PNG supported without PIL")
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctp]
    stride = w * chans
    raw = zlib.decompress(idat)
    rows, prev = [], bytearray(stride)
    for y in range(h):
        off = y * (stride + 1)
        line = unfilter(raw[off], bytearray(raw[off + 1:off + 1 + stride]), prev, chans)
        prev = line
        out = bytearray(3 * w)
        for x in range(w):
            px = line[x * chans:(x + 1) * chans]
            if ctp == 2 or ctp == 6:
                rgb = px[:3]
            elif ctp == 0 or ctp == 4:
                rgb = bytes((px[0],) * 3)
            else:
                rgb = plte[3 * px[0]:3 * px[0] + 3]
            out[3 * x:3 * x + 3] = rgb
        rows.append(bytes(out))
    return w, h, rows


def write_png(path: str, frame: Frame, filters=(0, 1, 2, 3, 4)) -> None:
    """Minimal RGB writer (used by tests/synth); cycles through filter types."""
    bpp, raw, prev = 3, bytearray(), bytes(3 * frame.width)
    for y, row in enumerate(frame.rows):
        ft = filters[y % len(filters)]
        raw.append(ft)
        for i in range(len(row)):
            a = row[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            pred = (0, a, b, (a + b) >> 1, _paeth(a, b, c))[ft]
            raw.append((row[i] - pred) & 255)
        prev = row

    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", frame.width, frame.height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def load_frame(path: str, ts: float = 0.0, force_stdlib: bool = False) -> Frame:
    if not force_stdlib:
        try:
            from PIL import Image  # type: ignore
            im = Image.open(path).convert("RGB")
            w, h = im.size
            b = im.tobytes()
            return Frame(w, h, [b[y * 3 * w:(y + 1) * 3 * w] for y in range(h)], ts)
        except ImportError:
            pass
    with open(path, "rb") as f:
        w, h, rows = read_png_rows(f.read())
    return Frame(w, h, rows, ts)
