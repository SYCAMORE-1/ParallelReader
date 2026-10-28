"""Locate QR finder patterns so translation cannot overwrite machine-readable art."""
import io
import re
from functools import lru_cache
from itertools import combinations


def _pattern(lengths):
    module = sum(lengths) / 7
    return module if module >= 1 and all(abs(length - module * ratio) <= module * .7
                                        for length, ratio in zip(lengths, [1, 1, 3, 1, 1])) else None


@lru_cache(maxsize=16)
def qr_regions(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as original:
        image = original.convert('L')
        if min(image.size) < 35:
            return ()
        image.thumbnail((1400, 1400))
        width, height = image.size
        pixels = image.point(lambda p: 1 if p < 128 else 0).tobytes()
    candidates = []
    for y in range(0, height, max(1, height // 1000)):
        row = pixels[y * width:(y + 1) * width]
        runs, start = [], 0
        for x in range(1, width + 1):
            if x == width or row[x] != row[start]:
                runs.append((row[start], start, x - start)); start = x
        for index in range(4, len(runs)):
            group = runs[index - 4:index + 1]
            if [r[0] for r in group] != [1, 0, 1, 0, 1]:
                continue
            module = _pattern([r[2] for r in group])
            if not module:
                continue
            x = round(group[2][1] + (group[2][2] - 1) / 2)
            top, bottom = y, y
            while top > 0 and pixels[(top - 1) * width + x]: top -= 1
            while bottom + 1 < height and pixels[(bottom + 1) * width + x]: bottom += 1
            up, down = [], []
            for direction, edge, lengths in [(-1, top - 1, up), (1, bottom + 1, down)]:
                for color in [0, 1]:
                    count = 0
                    while 0 <= edge < height and pixels[edge * width + x] == color:
                        count += 1; edge += direction
                    lengths.append(count)
            vertical = _pattern([up[1], up[0], bottom - top + 1, down[0], down[1]])
            if not vertical or not .65 <= vertical / module <= 1.5:
                continue
            cy = (top + bottom) / 2
            if abs(cy - y) > module * 1.6:
                continue
            cluster = next((c for c in candidates if abs(c[0] - x) < module * 2 and abs(c[1] - cy) < module * 2), None)
            if cluster:
                n = cluster[3]; cluster[:] = [(cluster[0] * n + x) / (n + 1), (cluster[1] * n + cy) / (n + 1), (cluster[2] * n + module) / (n + 1), n + 1]
            else:
                candidates.append([x, cy, module, 1])
    finders = [c for c in candidates if c[3] >= 2]
    boxes = []
    for corner in finders:
        for first, second in combinations((c for c in finders if c is not corner), 2):
            horizontal, vertical = sorted([first, second], key=lambda c: abs(c[1] - corner[1]))
            module = (corner[2] + horizontal[2] + vertical[2]) / 3
            dx, dy = abs(horizontal[0] - corner[0]), abs(vertical[1] - corner[1])
            if (abs(horizontal[1] - corner[1]) > module * 2 or abs(vertical[0] - corner[0]) > module * 2
                    or min(dx, dy) < module * 10 or not .65 <= dx / max(1, dy) <= 1.55
                    or max(c[2] for c in [corner, horizontal, vertical]) > min(c[2] for c in [corner, horizontal, vertical]) * 1.5):
                continue
            points = [corner, horizontal, vertical]
            box = (max(0, round((min(c[0] for c in points) - module * 4) / width * 1000)),
                   max(0, round((min(c[1] for c in points) - module * 4) / height * 1000)),
                   min(1000, round((max(c[0] for c in points) + module * 4) / width * 1000)),
                   min(1000, round((max(c[1] for c in points) + module * 4) / height * 1000)))
            if box not in boxes: boxes.append(box)
    return tuple(boxes)


def preserve_codes(data, result):
    regions = qr_regions(data)
    if not regions:
        return result
    blocks = []
    for block in result['blocks']:
        box = block.get('bbox')
        logo = re.sub(r'[^A-Za-z]', '', block['source']).upper() in {'OUTWIT', 'OUTLIVE', 'OUTFIGHT'}
        if logo or box and any(left - 12 <= (box[0] + box[2]) / 2 <= right + 12 and top - 8 <= (box[1] + box[3]) / 2 <= bottom + 8
                       for left, top, right, bottom in regions):
            continue
        blocks.append(block)
    return dict(result, blocks=blocks, has_text=bool(blocks), preservedRegions=[list(r) for r in regions])
