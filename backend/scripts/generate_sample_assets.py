"""Generate the bundled sample schematics, their component annotations and a sample PDF datasheet.

Run from the backend directory:  python -m scripts.generate_sample_assets

Outputs (all deterministic):
  data/sample_circuits/<id>/schematic.png      – drawn schematic
  data/sample_circuits/<id>/annotations.json   – exact normalised component bounding boxes
  data/sample_circuits/led_basic/schematic.pdf – PDF version of the LED schematic
  data/sample_datasheets/generic_signal_diode_illustrative.pdf – illustrative PDF reference

The bounding boxes are known exactly because this script draws every symbol.
They are labelled ``sample_annotation`` in the API – not vision-model output.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "data" / "sample_circuits"
DATASHEETS = ROOT / "data" / "sample_datasheets"

INK = (24, 32, 48)
NET = (21, 101, 192)
MUTED = (110, 120, 135)
PAPER = (252, 252, 250)
GRID = (226, 230, 236)
LINE_W = 4


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


class Sheet:
    def __init__(self, width: int, height: int, title: str, sample_id: str):
        self.w, self.h = width, height
        self.img = Image.new("RGB", (width, height), PAPER)
        self.d = ImageDraw.Draw(self.img)
        self.boxes: dict[str, list[float]] = {}
        for x in range(0, width, 25):
            for y in range(0, height, 25):
                self.d.point((x, y), fill=GRID)
        self.d.rectangle((12, 12, width - 13, height - 13), outline=MUTED, width=2)
        # Title block
        tb_w, tb_h = 460, 86
        x0, y0 = width - 13 - tb_w, height - 13 - tb_h
        self.d.rectangle((x0, y0, width - 13, height - 13), outline=MUTED, width=2)
        self.d.text((x0 + 14, y0 + 10), title, font=font(24), fill=INK)
        self.d.text((x0 + 14, y0 + 44), f"CircuitSense sample · {sample_id} · reference drawing", font=font(16), fill=MUTED)

    def grow(self, ref: str, x0: float, y0: float, x1: float, y1: float) -> None:
        b = self.boxes.get(ref)
        if b is None:
            self.boxes[ref] = [x0, y0, x1, y1]
        else:
            b[0], b[1], b[2], b[3] = min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)

    def wire(self, *pts: tuple[int, int]) -> None:
        self.d.line(pts, fill=INK, width=LINE_W, joint="curve")

    def dot(self, x: int, y: int) -> None:
        self.d.ellipse((x - 7, y - 7, x + 7, y + 7), fill=INK)

    def text(self, xy: tuple[int, int], s: str, size: int = 26, fill=INK, anchor: str = "la", ref: str | None = None) -> None:
        f = font(size)
        self.d.text(xy, s, font=f, fill=fill, anchor=anchor)
        if ref:
            self.grow(ref, *self.d.textbbox(xy, s, font=f, anchor=anchor))

    def net_label(self, xy: tuple[int, int], name: str, anchor: str = "la") -> None:
        self.text(xy, name, size=22, fill=NET, anchor=anchor)

    def ground(self, x: int, y: int) -> None:
        self.wire((x, y), (x, y + 22))
        for i, half in enumerate((26, 17, 8)):
            yy = y + 22 + i * 9
            self.d.line((x - half, yy, x + half, yy), fill=INK, width=LINE_W)

    def resistor(self, ref: str, a: tuple[int, int], b: tuple[int, int]) -> None:
        (ax, ay), (bx, by) = a, b
        length = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / length, (by - ay) / length
        px, py = -uy, ux
        body = 100
        start = (length - body) / 2
        pts = [a, (ax + ux * start, ay + uy * start)]
        zig = 6
        for i in range(1, zig * 2 + 1):
            t = start + body * (i / (zig * 2 + 1))
            amp = 18 if i % 2 else -18
            pts.append((ax + ux * t + px * amp, ay + uy * t + py * amp))
        pts.append((ax + ux * (start + body), ay + uy * (start + body)))
        pts.append(b)
        self.d.line(pts, fill=INK, width=LINE_W, joint="curve")
        cx, cy = (ax + bx) / 2, (ay + by) / 2
        hx = abs(ux) * body / 2 + abs(px) * 24
        hy = abs(uy) * body / 2 + abs(py) * 24
        self.grow(ref, cx - hx, cy - hy, cx + hx, cy + hy)

    def led(self, ref: str, anode: tuple[int, int], cathode: tuple[int, int]) -> None:
        """Vertical LED, anode on top, current flowing downwards."""
        x, ya = anode
        _, yk = cathode
        cy = (ya + yk) / 2
        top, bar = cy - 28, cy + 22
        self.wire(anode, (x, top))
        self.wire((x, bar), cathode)
        self.d.polygon([(x - 32, top), (x + 32, top), (x, bar)], outline=INK, width=LINE_W)
        self.d.line((x - 32, bar, x + 32, bar), fill=INK, width=LINE_W)
        for off in (-8, 14):
            sx, sy = x + 30, cy + off
            ex, ey = sx + 30, sy - 26
            self.d.line((sx, sy, ex, ey), fill=INK, width=3)
            self.d.polygon([(ex, ey), (ex - 12, ey + 3), (ex - 4, ey + 11)], fill=INK)
        self.grow(ref, x - 40, top - 8, x + 66, bar + 8)

    def source(self, ref: str, center: tuple[int, int], plus_on_top: bool = True, radius: int = 50) -> tuple[tuple[int, int], tuple[int, int]]:
        x, y = center
        self.d.ellipse((x - radius, y - radius, x + radius, y + radius), outline=INK, width=LINE_W)
        top_sign, bottom_sign = ("+", "-") if plus_on_top else ("-", "+")
        self.d.text((x, y - 22), top_sign, font=font(30), fill=INK, anchor="mm")
        self.d.text((x, y + 22), bottom_sign, font=font(30), fill=INK, anchor="mm")
        self.grow(ref, x - radius - 6, y - radius - 6, x + radius + 6, y + radius + 6)
        return (x, y - radius), (x, y + radius)

    def opamp(self, ref: str, left: int, cy: int, width: int = 200, half_h: int = 110) -> dict[str, tuple[int, int]]:
        tip = left + width
        self.d.polygon([(left, cy - half_h), (left, cy + half_h), (tip, cy)], outline=INK, width=LINE_W)
        self.d.text((left + 22, cy - 50), "+", font=font(32), fill=INK, anchor="mm")
        self.d.text((left + 22, cy + 50), "-", font=font(32), fill=INK, anchor="mm")
        px = left + 90
        edge = half_h * (1 - 90 / width)
        self.grow(ref, left - 4, cy - half_h - 4, tip + 4, cy + half_h + 4)
        return {
            "IN+": (left, cy - 50),
            "IN-": (left, cy + 50),
            "OUT": (tip, cy),
            "V+": (px, int(cy - edge)),
            "V-": (px, int(cy + edge)),
        }

    def connector2(self, ref: str, x: int, y1: int, y2: int) -> None:
        self.d.rectangle((x, y1 - 30, x + 60, y2 + 30), outline=INK, width=LINE_W)
        for y in (y1, y2):
            self.d.ellipse((x + 18, y - 10, x + 38, y + 10), outline=INK, width=3)
        self.grow(ref, x - 4, y1 - 34, x + 64, y2 + 34)

    def pin_no(self, xy: tuple[int, int], n: str, anchor: str = "rb") -> None:
        self.text(xy, n, size=16, fill=MUTED, anchor=anchor)

    def annotations(self) -> dict:
        comps = {}
        for ref, (x0, y0, x1, y1) in sorted(self.boxes.items()):
            pad = 6
            x0, y0 = max(x0 - pad, 0), max(y0 - pad, 0)
            x1, y1 = min(x1 + pad, self.w), min(y1 + pad, self.h)
            comps[ref] = {
                "x": round(x0 / self.w, 4),
                "y": round(y0 / self.h, 4),
                "width": round((x1 - x0) / self.w, 4),
                "height": round((y1 - y0) / self.h, 4),
            }
        return {"image": "schematic.png", "width": self.w, "height": self.h, "components": comps}


def draw_led_basic() -> Sheet:
    s = Sheet(1200, 800, "LED indicator", "led_basic")
    top, bottom = s.source("V1", (250, 410))
    s.text((180, 385), "V1", anchor="ra", ref="V1")
    s.text((180, 420), "5 V", anchor="ra", ref="V1")
    s.wire(top, (250, 200), (420, 200))
    s.resistor("R1", (420, 200), (620, 200))
    s.text((520, 150), "R1", anchor="mb", ref="R1")
    s.text((520, 232), "330", anchor="mt", ref="R1")
    s.wire((620, 200), (850, 200), (850, 320))
    s.led("D1", (850, 320), (850, 480))
    s.text((935, 372), "D1", ref="D1")
    s.text((935, 406), "Red LED", ref="D1")
    s.wire((850, 480), (850, 620), (250, 620), bottom)
    s.ground(550, 620)
    s.dot(550, 620)
    s.net_label((265, 168), "VCC")
    s.net_label((660, 168), "LED_A")
    s.net_label((585, 652), "GND")
    s.pin_no((842, 316), "A", anchor="rb")
    s.pin_no((842, 500), "K", anchor="rt")
    return s


def draw_resistor_divider() -> Sheet:
    s = Sheet(1200, 800, "Resistor divider", "resistor_divider")
    top, bottom = s.source("V1", (250, 420))
    s.text((180, 395), "V1", anchor="ra", ref="V1")
    s.text((180, 430), "12 V", anchor="ra", ref="V1")
    s.wire(top, (250, 150), (600, 150), (600, 200))
    s.resistor("R1", (600, 200), (600, 360))
    s.text((560, 255), "R1", anchor="ra", ref="R1")
    s.text((560, 290), "10k", anchor="ra", ref="R1")
    s.wire((600, 360), (600, 440))
    s.dot(600, 400)
    s.wire((600, 400), (900, 400))
    s.resistor("R2", (600, 440), (600, 600))
    s.text((560, 495), "R2", anchor="ra", ref="R2")
    s.text((560, 530), "4.7k", anchor="ra", ref="R2")
    s.wire((600, 600), (600, 680), (250, 680), bottom)
    s.connector2("J1", 900, 400, 460)
    s.text((980, 400), "J1", ref="J1")
    s.text((980, 434), "Output", ref="J1")
    s.wire((900, 460), (820, 460), (820, 680), (600, 680))
    s.dot(600, 680)
    s.ground(420, 680)
    s.dot(420, 680)
    s.net_label((265, 118), "VIN")
    s.net_label((680, 368), "VOUT")
    s.net_label((455, 712), "GND")
    s.pin_no((896, 392), "1")
    s.pin_no((896, 452), "2")
    return s


def draw_noninverting_amp() -> Sheet:
    s = Sheet(1400, 900, "Non-inverting amplifier", "noninverting_amp")
    pins = s.opamp("U1", 700, 420)
    s.text((832, 296), "U1", ref="U1")
    s.text((832, 326), "Generic op-amp", size=20, ref="U1")
    s.pin_no((692, pins["IN+"][1] - 4), "3")
    s.pin_no((692, pins["IN-"][1] - 4), "2")
    s.pin_no((908, 412), "6", anchor="lb")
    # Supply pins
    vp, vn = pins["V+"], pins["V-"]
    s.wire(vp, (vp[0], 270))
    s.net_label((vp[0] + 10, 262), "VCC")
    s.pin_no((vp[0] - 6, vp[1] - 6), "7")
    s.wire(vn, (vn[0], 560))
    s.net_label((vn[0] + 10, 540), "VEE")
    s.pin_no((vn[0] - 6, vn[1] + 24), "4")
    # Input source
    top, bottom = s.source("V1", (300, 480))
    s.text((230, 455), "V1", anchor="ra", ref="V1")
    s.text((230, 490), "0.1 V", anchor="ra", ref="V1")
    s.wire(top, (300, pins["IN+"][1]), pins["IN+"])
    s.net_label((420, 338), "VIN")
    s.wire(bottom, (300, 600))
    s.ground(300, 600)
    # Feedback network
    fb = (640, pins["IN-"][1])
    s.wire(pins["IN-"], fb)
    s.dot(*fb)
    s.wire(fb, (620, fb[1]))
    s.resistor("R1", (620, fb[1]), (460, fb[1]))
    s.text((540, fb[1] - 34), "R1", anchor="mb", ref="R1")
    s.text((540, fb[1] + 30), "1k", anchor="mt", ref="R1")
    s.wire((460, fb[1]), (420, fb[1]), (420, 600))
    s.ground(420, 600)
    s.wire(fb, (640, 660), (760, 660))
    s.resistor("R2", (760, 660), (920, 660))
    s.text((840, 625), "R2", anchor="mb", ref="R2")
    s.text((840, 694), "10k", anchor="mt", ref="R2")
    s.wire((920, 660), (1000, 660), (1000, 420))
    s.net_label((650, 500), "FB")
    # Output and load
    s.wire(pins["OUT"], (1150, 420))
    s.dot(1000, 420)
    s.net_label((1030, 388), "VOUT")
    s.wire((1150, 420), (1150, 460))
    s.resistor("R3", (1150, 460), (1150, 620))
    s.text((1190, 515), "R3", ref="R3")
    s.text((1190, 550), "10k", ref="R3")
    s.wire((1150, 620), (1150, 680))
    s.ground(1150, 680)
    # Supply rails drawn as a separate block joined by net labels.
    s.text((120, 60), "Supplies", size=22, fill=MUTED)
    v2_top, v2_bottom = s.source("V2", (200, 190), radius=42)
    s.text((140, 172), "V2", anchor="ra", ref="V2")
    s.text((140, 204), "12 V", anchor="ra", ref="V2")
    s.wire(v2_top, (200, 110), (250, 110))
    s.net_label((258, 98), "VCC")
    s.wire(v2_bottom, (200, 250))
    s.ground(200, 250)
    v3_top, v3_bottom = s.source("V3", (420, 190), plus_on_top=False, radius=42)
    s.text((360, 172), "V3", anchor="ra", ref="V3")
    s.text((360, 204), "12 V", anchor="ra", ref="V3")
    s.wire(v3_top, (420, 110), (470, 110))
    s.net_label((478, 98), "VEE")
    s.wire(v3_bottom, (420, 250))
    s.ground(420, 250)
    return s


def write_led_pdf(sheet: Sheet, png_path: Path, pdf_path: Path) -> dict:
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)  # A4 landscape, points
    margin = 36
    avail_w, avail_h = 842 - 2 * margin, 595 - 2 * margin - 24
    scale = min(avail_w / sheet.w, avail_h / sheet.h)
    w, h = sheet.w * scale, sheet.h * scale
    x0 = (842 - w) / 2
    y0 = margin + 24
    rect = fitz.Rect(x0, y0, x0 + w, y0 + h)
    page.insert_text((margin, margin + 6), "CircuitSense sample schematic – LED indicator (PDF version)", fontsize=12)
    page.insert_image(rect, filename=str(png_path))
    doc.set_metadata({"title": "CircuitSense sample – LED indicator", "creator": "CircuitSense generate_sample_assets"})
    doc.save(str(pdf_path), deflate=True, garbage=4)
    doc.close()
    comps = {}
    for ref, b in sheet.annotations()["components"].items():
        comps[ref] = {
            "x": round((x0 + b["x"] * w) / 842, 4),
            "y": round((y0 + b["y"] * h) / 595, 4),
            "width": round(b["width"] * w / 842, 4),
            "height": round(b["height"] * h / 595, 4),
        }
    return {"file": "schematic.pdf", "page": 1, "components": comps}


DIODE_PAGES = [
    (
        "Generic small-signal silicon diode – ILLUSTRATIVE REFERENCE",
        [
            "ILLUSTRATIVE TEACHING DATA. This is not a manufacturer datasheet. Values are rounded, typical "
            "figures for a generic small-signal silicon switching diode, provided so CircuitSense can demonstrate "
            "PDF retrieval with page citations. Always use the datasheet of the exact part you have.",
            "1. Overview",
            "A small-signal silicon diode conducts when its anode is more positive than its cathode by roughly the "
            "forward voltage. It is used for signal rectification, reverse-polarity protection of low-current "
            "circuits, clamping and logic OR-ing.",
            "2. Pin identification",
            "The cathode is marked by a band on the body. In KiCad's Device:D symbol pin 1 is the cathode (K) "
            "and pin 2 is the anode (A).",
        ],
    ),
    (
        "Electrical characteristics (typical, illustrative)",
        [
            "3. Electrical characteristics",
            "Forward voltage: about 0.6 V to 0.7 V at 1 mA, rising to about 1 V at 100 mA (illustrative).",
            "Maximum continuous forward current: 150 mA (illustrative).",
            "Maximum repetitive reverse voltage: 75 V (illustrative).",
            "Reverse leakage current: a few nanoamps at 25 °C, increasing strongly with temperature.",
            "4. Troubleshooting",
            "Diode test on a multimeter (power removed): about 0.5 V to 0.7 V forward, open-circuit (OL) in "
            "reverse. A reading near 0 V in both directions indicates a shorted diode; OL in both directions "
            "indicates an open diode.",
        ],
    ),
]


def write_diode_pdf(path: Path) -> None:
    doc = fitz.open()
    for title, paragraphs in DIODE_PAGES:
        page = doc.new_page(width=595, height=842)
        page.insert_text((56, 72), title, fontsize=15)
        y = 104
        for para in paragraphs:
            is_heading = para[:2].rstrip(".").isdigit() and len(para) < 60
            rect = fitz.Rect(56, y, 540, y + 140)
            fontsize = 13 if is_heading else 10.5
            used = page.insert_textbox(rect, para, fontsize=fontsize)
            height = 140 - used if used >= 0 else 140
            y += height + (10 if is_heading else 14)
    doc.set_metadata({"title": "Generic small-signal diode (illustrative)", "creator": "CircuitSense generate_sample_assets"})
    doc.save(str(path), deflate=True, garbage=4)
    doc.close()


def main() -> None:
    for sample_id, fn in (
        ("led_basic", draw_led_basic),
        ("resistor_divider", draw_resistor_divider),
        ("noninverting_amp", draw_noninverting_amp),
    ):
        out_dir = SAMPLES / sample_id
        out_dir.mkdir(parents=True, exist_ok=True)
        sheet = fn()
        png = out_dir / "schematic.png"
        sheet.img.save(png, optimize=True)
        ann = sheet.annotations()
        if sample_id == "led_basic":
            ann["pdf"] = write_led_pdf(sheet, png, out_dir / "schematic.pdf")
        (out_dir / "annotations.json").write_text(json.dumps(ann, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {png.relative_to(ROOT)} ({len(ann['components'])} annotated components)")
    DATASHEETS.mkdir(parents=True, exist_ok=True)
    write_diode_pdf(DATASHEETS / "generic_signal_diode_illustrative.pdf")
    print("wrote data/sample_datasheets/generic_signal_diode_illustrative.pdf")


if __name__ == "__main__":
    main()
