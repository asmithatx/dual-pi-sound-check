#!/usr/bin/env python3
"""generate_icons.py — Creates PWA icons for SoundCheck. Run once."""
import struct, zlib, base64

def make_png(size, bg="#0f0f1a", wave="#3b82f6"):
    """Generate a minimal PNG with a soundwave icon via pure Python."""
    try:
        from PIL import Image, ImageDraw
        img  = Image.new("RGBA", (size, size), bg)
        draw = ImageDraw.Draw(img)
        # Simple soundwave bars
        cx, cy = size // 2, size // 2
        heights = [0.25, 0.45, 0.70, 0.90, 0.70, 0.45, 0.25]
        bar_w   = max(2, size // 20)
        spacing = size // (len(heights) + 1)
        for i, h in enumerate(heights):
            x  = spacing * (i + 1) - bar_w // 2
            bh = int(size * h)
            y1 = cy - bh // 2
            y2 = cy + bh // 2
            draw.rectangle([x, y1, x + bar_w, y2], fill=wave)
        img.save(f"static/icons/icon-{size}x{size}.png")
        print(f"  Created {size}x{size}.png via Pillow")
    except ImportError:
        # Pillow not available — write a 1x1 transparent PNG as placeholder
        placeholder = (
            b"\x89PNG\r\n\x1a\n"                    # PNG signature
            b"\x00\x00\x00\rIHDR"                    # IHDR chunk
            + struct.pack(">II", size, size)          # width, height
            + b"\x08\x02\x00\x00\x00"               # 8-bit RGB
            + struct.pack(">I", zlib.crc32(
                b"IHDR" + struct.pack(">II", size, size) + b"\x08\x02\x00\x00\x00"
              ) & 0xffffffff)
            + b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
            + b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        with open(f"static/icons/icon-{size}x{size}.png", "wb") as f:
            f.write(placeholder)
        print(f"  Created placeholder {size}x{size}.png (install Pillow for real icons)")

import os; os.makedirs("static/icons", exist_ok=True)
make_png(192)
make_png(512)
print("Done. Re-run after `pip install Pillow` for proper icons.")
