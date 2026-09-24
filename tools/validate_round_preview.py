"""Independent landmarks for the 466px native round design fixtures."""
from math import cos, radians, sin
from pathlib import Path
from PIL import Image, ImageChops


def validate(directory):
    images = {}
    for path in Path(directory).glob('round-*.png'):
        with Image.open(path) as image:
            images[path.stem.removeprefix('round-')] = image.convert('RGB')
    for name, image in images.items():
        assert image.size == (466, 466), name
        # Every visible pixel must survive the physical circular mask.
        for y in range(466):
            for x in range(466):
                if (x - 232.5)**2 + (y - 232.5)**2 > 232.5**2:
                    assert image.getpixel((x, y)) == (0, 0, 0), (name, x, y)
        if name == 'diagnostic' or name.startswith(('settings-', 'wifi-')):
            continue
        # Detect vanished/clipped captions independently of object geometry.
        for box in [(170, 75, 295, 115), (75, 295, 205, 325), (208, 295, 400, 325)]:
            assert sum(max(p) > 70 for p in image.crop(box).getdata()) > 100, (name, box)
        # Clear gap between hero including its percent unit and the two stats.
        assert image.crop((85, 270, 390, 293)).getbbox() is None, name
    live, stale = images['codex-live'], images['codex-stale']
    assert ImageChops.difference(live.crop((0, 0, 466, 370)), stale.crop((0, 0, 466, 370))).getbbox() is None
    assert ImageChops.difference(live, stale).getbbox() is not None

    def ring(name, angle):
        return images[name].getpixel((round(233 + 214*cos(radians(angle-90))),
                                      round(233 + 214*sin(radians(angle-90)))))
    accent, muted, track = (111, 120, 255), (69, 75, 138), (48, 50, 56)
    assert ring('codex-live', 100) == muted
    assert ring('codex-live', 145) == accent  # 35% earlier + 8% today
    assert ring('codex-live', 200) == track
    for angle in (45, 100, 145, 200, 300):
        assert ring('codex-full', angle) == accent
        for name in ('codex-zero', 'codex-missing', 'codex-today-invalid'):
            assert ring(name, angle) == track, name
    assert ring('codex-today-missing', 100) == accent
    assert images['diagnostic'].getpixel((160, 173)) == (255, 0, 0)
    assert images['diagnostic'].getpixel((194, 173)) == (0, 255, 0)
    assert images['diagnostic'].getpixel((228, 173)) == (0, 0, 255)

    # Fixed independent content bands prevent blank surfaces passing the mask.
    for name in ('settings-menu', 'settings-labs', 'settings-labs-providers',
                 'settings-labs-providers-pending'):
        for top in (112, 178, 244, 310):
            assert sum(max(p) > 100 for p in images[name].crop((100, top, 365, top+48)).getdata()) > 100, (name, top)
    qr = images['wifi-qr']
    assert qr.getpixel((135, 120)) == (255, 255, 255)
    assert qr.getpixel((330, 315)) == (255, 255, 255)
    assert sum(max(p) > 180 for p in qr.crop((135, 120, 331, 316)).getdata()) > 15000
    for name in ('wifi-manual', 'wifi-searching', 'wifi-failed'):
        assert sum(max(p) > 100 for p in images[name].crop((45, 145, 420, 240)).getdata()) > 200, name
