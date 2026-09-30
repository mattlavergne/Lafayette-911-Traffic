"""A small map picture of a drawn commute route, for the route alert email.

Email clients can't run the Leaflet map, and most block remote images, so
the picture is rendered on the Pi and attached inline (``cid:``): OpenStreetMap
tiles stitched with Pillow, the drawn line on top, a green start dot, a
checkered-flag end dot, and a red dot for each located incident in the email.

Best-effort by design: no Pillow, no network, or any error → ``None`` and the
email goes out without a picture. Tiles are cached on disk (a route uses the
same ~6–12 tiles every day), requests carry an identifying User-Agent, and the
image carries the required "© OpenStreetMap contributors" attribution, per the
OSM tile usage policy.
"""

import io
import math
import os
import time
from typing import Dict, List, Optional, Tuple

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = ("Lafayette-911-Traffic route alerts "
              "(personal commute email; github.com/mattlavergne/Lafayette-911-Traffic)")
TILE_SIZE = 256
TILE_MAX_AGE_S = 30 * 86400
WIDTH, HEIGHT = 600, 320
_PAD_PX = 28
_MAX_ZOOM = 16


def _lnglat_to_px(lat: float, lng: float, z: int) -> Tuple[float, float]:
    """Web-Mercator world pixel coordinates at zoom z."""
    n = TILE_SIZE * (2 ** z)
    x = (lng + 180.0) / 360.0 * n
    s = math.sin(math.radians(max(-85.0, min(85.0, lat))))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def _pick_zoom(points: List[Tuple[float, float]]) -> int:
    for z in range(_MAX_ZOOM, 3, -1):
        xs, ys = zip(*(_lnglat_to_px(lat, lng, z) for lat, lng in points))
        if (max(xs) - min(xs) <= WIDTH - 2 * _PAD_PX
                and max(ys) - min(ys) <= HEIGHT - 2 * _PAD_PX):
            return z
    return 4


def _fetch_tile(session, cache_dir: str, z: int, x: int, y: int):
    """Tile as a PIL image (disk cache first), or None."""
    from PIL import Image

    path = os.path.join(cache_dir, "tiles", str(z), str(x), "%d.png" % y)
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < TILE_MAX_AGE_S:
            with open(path, "rb") as fh:
                return Image.open(io.BytesIO(fh.read())).convert("RGB")
    except Exception:
        pass
    if session is None:
        return None
    try:
        resp = session.get(TILE_URL.format(z=z, x=x, y=y), timeout=10,
                           headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200 or not resp.content:
            return None
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(resp.content)
        os.replace(tmp, path)
        return img
    except Exception:
        return None


def _checkered_dot(draw, cx: float, cy: float, r: int) -> None:
    draw.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), fill=(255, 255, 255))
    q = r / 2.0
    for i in range(-2, 2):
        for j in range(-2, 2):
            if (i + j) % 2 == 0:
                draw.rectangle((cx + i * q, cy + j * q, cx + (i + 1) * q, cy + (j + 1) * q),
                               fill=(23, 26, 33))


def render_route_png(path: List[Tuple[float, float]], incidents: Optional[List[Dict]] = None,
                     cache_dir: str = ".", session=None) -> Optional[bytes]:
    """PNG bytes of the route (plus located incident dots), or None."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    if not path or len(path) < 2:
        return None
    try:
        dots = [(i["lat"], i["lng"]) for i in (incidents or [])
                if i.get("lat") is not None and i.get("lng") is not None]
        z = _pick_zoom(list(path) + dots)
        xs, ys = zip(*(_lnglat_to_px(lat, lng, z) for lat, lng in path))
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        left, top = cx - WIDTH / 2.0, cy - HEIGHT / 2.0

        base = Image.new("RGB", (WIDTH, HEIGHT), (232, 234, 238))
        got_tiles = 0
        for tx in range(int(left // TILE_SIZE), int((left + WIDTH) // TILE_SIZE) + 1):
            for ty in range(int(top // TILE_SIZE), int((top + HEIGHT) // TILE_SIZE) + 1):
                tile = _fetch_tile(session, cache_dir, z, tx, ty)
                if tile is not None:
                    base.paste(tile, (int(round(tx * TILE_SIZE - left)), int(round(ty * TILE_SIZE - top))))
                    got_tiles += 1

        # Draw the overlay at 2x and scale down for smooth (anti-aliased) lines.
        ss = 2
        over = Image.new("RGBA", (WIDTH * ss, HEIGHT * ss), (0, 0, 0, 0))
        d = ImageDraw.Draw(over)

        def px(lat, lng):
            x, y = _lnglat_to_px(lat, lng, z)
            return ((x - left) * ss, (y - top) * ss)

        line = [px(lat, lng) for lat, lng in path]
        d.line(line, fill=(255, 255, 255, 235), width=11 * ss, joint="curve")
        d.line(line, fill=(47, 111, 237, 255), width=6 * ss, joint="curve")
        sx, sy = line[0]
        r = 7 * ss
        d.ellipse((sx - r - 2 * ss, sy - r - 2 * ss, sx + r + 2 * ss, sy + r + 2 * ss), fill=(255, 255, 255))
        d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(4, 120, 87))
        ex, ey = line[-1]
        _checkered_dot(d, ex, ey, r)
        for lat, lng in dots:
            ix, iy = px(lat, lng)
            ri = 8 * ss
            d.ellipse((ix - ri - 2 * ss, iy - ri - 2 * ss, ix + ri + 2 * ss, iy + ri + 2 * ss), fill=(255, 255, 255))
            d.ellipse((ix - ri, iy - ri, ix + ri, iy + ri), fill=(220, 38, 38))
        over = over.resize((WIDTH, HEIGHT), Image.LANCZOS)
        img = base.convert("RGBA")
        img.alpha_composite(over)

        label = ("© OpenStreetMap contributors" if got_tiles
                 else "Map tiles unavailable — route shape only")
        ld = ImageDraw.Draw(img)
        try:
            tw = int(ld.textlength(label))
        except Exception:
            tw = 6 * len(label)
        ld.rectangle((WIDTH - tw - 10, HEIGHT - 16, WIDTH, HEIGHT), fill=(255, 255, 255, 210))
        ld.text((WIDTH - tw - 5, HEIGHT - 14), label, fill=(60, 64, 72))

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None

