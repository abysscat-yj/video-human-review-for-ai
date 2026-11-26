# Renders demo/sample.mp4, the 8-second clip bundled with Frame Review (sunset, hills, a bird, a title).
# Needs numpy, pillow and ffmpeg; the font path is macOS-specific.
#   uv run --no-project --with numpy --with pillow python demo/make_sample.py demo/sample.mp4
import math, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
W, H, FPS, SEC = 1280, 720, 30, 8
out = sys.argv[1]
font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 64)
small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 28)
ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                       "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", "-preset", "slow", "-movflags", "+faststart", out],
                      stdin=subprocess.PIPE)
yy = np.linspace(0, 1, H)[:, None]
def lerp(a, b, k): return a + (b - a) * k
def hills(seed, base, amp, color, shift):
    xs = np.arange(W + 1)
    y = base + amp * (np.sin(xs / 170 + seed) * 0.6 + np.sin(xs / 61 + seed * 2.3) * 0.25 + np.sin(xs / 23 + seed) * 0.08)
    pts = [(x - shift % 1, float(y[x])) for x in xs] + [(W, H), (0, H)]
    return pts, color
for i in range(FPS * SEC):
    k = i / (FPS * SEC)
    top = np.array(lerp(np.array([38, 52, 110]), np.array([24, 20, 60]), k))
    mid = np.array(lerp(np.array([240, 150, 90]), np.array([200, 80, 90]), k))
    low = np.array(lerp(np.array([255, 214, 150]), np.array([250, 140, 100]), k))
    sky = np.where(yy < 0.55, top + (mid - top) * (yy / 0.55), mid + (low - mid) * ((yy - 0.55) / 0.45))
    img = np.broadcast_to(sky[:, None, :], (H, W, 3)).astype(np.uint8)
    im = Image.fromarray(np.ascontiguousarray(img))
    d = ImageDraw.Draw(im, "RGBA")
    sy = lerp(330, 470, k)  # the sun sets
    glow = Image.new("RGBA", (W, H)); gd = ImageDraw.Draw(glow)
    gd.ellipse([640 - 160, sy - 160, 640 + 160, sy + 160], fill=(255, 220, 160, 90)); glow = glow.filter(ImageFilter.GaussianBlur(40))
    im.paste(glow, (0, 0), glow); d = ImageDraw.Draw(im, "RGBA")
    d.ellipse([640 - 70, sy - 70, 640 + 70, sy + 70], fill=(255, 236, 200, 255))
    for seed, base, amp, col, spd in [(1.0, 470, 40, (120, 70, 90, 255), 20), (2.7, 530, 35, (80, 45, 70, 255), 45), (4.1, 600, 30, (40, 25, 45, 255), 90)]:
        pts, c = hills(seed, base, amp, col, spd * k)
        d.polygon(pts, fill=c)
    bx, by = lerp(-60, W + 60, k), 230 + 30 * math.sin(k * 12)  # a bird crosses
    wing = 18 * math.sin(i * 0.6)
    d.line([(bx - 26, by - wing), (bx, by), (bx + 26, by - wing)], fill=(30, 20, 40, 255), width=5)
    a = int(255 * min(1, max(0, (k - 0.15) / 0.15)))
    d.text((W / 2, 120), "Frame Review", font=font, fill=(255, 255, 255, a), anchor="mm")
    d.text((W / 2, 180), "sample clip · 8 s · 30 fps", font=small, fill=(255, 255, 255, int(a * 0.8)), anchor="mm")
    ff.stdin.write(im.convert("RGB").tobytes())
ff.stdin.close(); ff.wait()
