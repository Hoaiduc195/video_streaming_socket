from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

OUTFILE = 'movie_sample.mjpeg'
FRAMES = 30
WIDTH = 320
HEIGHT = 240
QUALITY = 80

# Create simple frames with frame number
frames = []
for i in range(FRAMES):
    img = Image.new('RGB', (WIDTH, HEIGHT), (int(255 * (i % 3 == 0)), int(255 * (i % 3 == 1)), int(255 * (i % 3 == 2))))
    draw = ImageDraw.Draw(img)
    text = f"Frame {i+1}"
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except:
        font = ImageFont.load_default()
    # Compute text size robustly across Pillow versions
    try:
        w, h = draw.textsize(text, font=font)
    except Exception:
        try:
            w, h = font.getsize(text)
        except Exception:
            bbox = draw.textbbox((0, 0), text, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
    draw.text(((WIDTH-w)//2, (HEIGHT-h)//2), text, fill=(255,255,255), font=font)
    buf = BytesIO()
    img.save(buf, format='JPEG', quality=QUALITY)
    frames.append(buf.getvalue())

# Write MJPEG file with 5-byte header per frame (matches VideoStream.nextFrame)
with open(OUTFILE, 'wb') as out:
    for data in frames:
        header = f"{len(data):5d}".encode('utf-8')  # 5-byte ASCII width, spaces OK
        out.write(header)
        out.write(data)

print(f"Wrote {OUTFILE} with {FRAMES} frames")
