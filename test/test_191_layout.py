"""Regression for compact native output; run with the preview directory argument."""
import sys
from pathlib import Path
from PIL import Image
root=Path(sys.argv[1])
frames=list(root.glob('*.png'))
assert len(frames)>100
for p in frames:
 with Image.open(p) as im:
  assert im.size==(536,240), (p,im.size)
# A genuine empty strip between tracker values and pager prevents the observed collision.
with Image.open(root/'vibepulse-tracker-codex-full.png') as im:
 assert im.convert('RGB').crop((50,219,486,223)).getbbox() is None, 'tracker values intrude on pager clearance'
# Setup QR remains inside the native panel and retains black/white modules.
with Image.open(root/'wifi-setup-qr.png') as im:
 colors={color for count,color in im.convert('RGB').crop((56,42,220,206)).getcolors(164*164)}
 assert (0,0,0) in colors and (255,255,255) in colors
print(f'PASS: {len(frames)} native 536x240 frames, tracker clearance and QR raster')
