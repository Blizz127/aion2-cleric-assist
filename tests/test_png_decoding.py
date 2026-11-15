"""Tiny synthetic PNG fixtures for the stdlib decoder; no owner screenshots."""
import struct
import unittest
import zlib

from aion2_assist.png import read_png_rows


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png(raw, *, width=1, height=1, color=2, depth=8, compression=0, filtering=0, interlace=0,
        palette=None, compressed=None):
    header = struct.pack(">IIBBBBB", width, height, depth, color, compression, filtering, interlace)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
    if palette is not None:
        data += chunk(b"PLTE", palette)
    return data + chunk(b"IDAT", zlib.compress(raw) if compressed is None else compressed) + chunk(b"IEND", b"")


class PngDecoding(unittest.TestCase):
    def test_unsupported_scanline_filter_is_rejected(self):
        for filter_type in (5, 255):
            with self.subTest(filter_type=filter_type):
                with self.assertRaisesRegex(ValueError, "filter"):
                    read_png_rows(png(bytes([filter_type, 16, 32, 48])))

    def test_decoded_scanlines_must_match_declared_dimensions_exactly(self):
        for raw in (b"", b"\0", b"\0\x10\x20", b"\0\x10\x20\x30\0", bytes(8)):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "scanline"):
                    read_png_rows(png(raw))

    def test_compressed_stream_must_be_complete_without_trailing_data(self):
        encoded = zlib.compress(b"\0\x10\x20\x30")
        for bad in (b"invalid zlib", encoded[:-1], encoded + b"extra", encoded + encoded):
            with self.subTest(compressed=bad):
                with self.assertRaisesRegex(ValueError, "compressed"):
                    read_png_rows(png(b"", compressed=bad))

    def test_extra_compressed_pixels_are_refused(self):
        with self.assertRaisesRegex(ValueError, "scanline"):
            read_png_rows(png(bytes(1024 * 1024)))  # dimensions allow only four decoded bytes

    def test_supported_color_modes_preserve_rgb_values(self):
        for color, samples, palette, expected in (
            (0, b"\x2a", None, b"\x2a" * 3),
            (2, b"\x10\x20\x30", None, b"\x10\x20\x30"),
            (3, b"\x01", b"\0\0\0\x10\x20\x30", b"\x10\x20\x30"),
            (4, b"\x2a\x7f", None, b"\x2a" * 3),
            (6, b"\x10\x20\x30\x7f", None, b"\x10\x20\x30"),
        ):
            with self.subTest(color=color):
                self.assertEqual(read_png_rows(png(b"\0" + samples, color=color, palette=palette)),
                                 (1, 1, [expected]))

    def test_invalid_pixel_format_or_dimensions_are_rejected(self):
        for fields in ({"width": 0}, {"height": 0}, {"color": 1}, {"color": 7}, {"depth": 4},
                       {"compression": 1}, {"filtering": 1}, {"interlace": 1}):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    read_png_rows(png(bytes(4), **fields))

    def test_indexed_pixels_require_a_complete_palette_and_valid_index(self):
        for palette, index in ((None, 0), (b"", 0), (b"\x01", 0), (bytes(771), 0), (bytes(6), 2)):
            with self.subTest(palette_bytes=None if palette is None else len(palette), index=index):
                with self.assertRaisesRegex(ValueError, "palette"):
                    read_png_rows(png(bytes([0, index]), color=3, palette=palette))


if __name__ == "__main__":
    unittest.main()
