"""Generate the SP Toys mark and Windows icon from one vector-like Pillow drawing."""
from pathlib import Path
from PIL import Image, ImageDraw

ASSETS = Path(__file__).parent / 'assets'
ASSETS.mkdir(exist_ok=True)
size = 1024
im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
# One rounded tile with a large, simple bag. The mark stays legible at 32–52 px.
d.rounded_rectangle((16, 16, 1008, 1008), radius=210, fill='#347653')
d.arc((334, 165, 690, 583), start=180, end=360, fill='white', width=100)
d.rounded_rectangle((196, 405, 828, 829), radius=62, fill='white')
d.rounded_rectangle((337, 568, 463, 694), radius=25, fill='#75b993')
d.rounded_rectangle((561, 568, 687, 694), radius=25, fill='#75b993')
im.save(ASSETS / 'sp-toys-logo.png')
im.save(ASSETS / 'sp-toys.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
im.resize((52,52), Image.Resampling.LANCZOS).save(ASSETS / 'sp-toys-mark-52.png')
im.resize((64,64), Image.Resampling.LANCZOS).save(ASSETS / 'favicon.png')
print('Logo assets generated')
