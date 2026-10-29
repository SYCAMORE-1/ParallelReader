"""Deterministic typesetting over localized source text; original bytes stay intact."""
import hashlib
import io
import json
import math
import re
from collections import Counter
from pathlib import Path

RENDER_VERSION = 'localized-text-png-v8-small-image-art'


def chinese_font():
    candidates = [Path(__file__).resolve().parents[1] / 'public' / 'fonts' / 'reader-sans.ttf',
                  Path('C:/Windows/Fonts/msyh.ttc'), Path('C:/Windows/Fonts/simhei.ttf'),
                  Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError('未找到用于生成中文译图的中文字体')


def background(image):
    pixels = list(image.resize((min(64, image.width), min(64, image.height))).getdata())
    bins = Counter(tuple(channel // 24 for channel in pixel[:3]) for pixel in pixels)
    best = bins.most_common(1)[0][0]
    colors = [pixel for pixel in pixels if tuple(channel // 24 for channel in pixel[:3]) == best]
    return tuple(round(sum(pixel[channel] for pixel in colors) / len(colors)) for channel in range(3))


def text_region(image, box, max_glyph_height=None, protected=None):
    """Complete glyphs cut by approximate model boxes, excluding long diagram lines."""
    from PIL import Image, ImageChops
    width, height = image.size
    left, top, right, bottom = box
    # Vision boxes can cut through a line's ascenders. Include nearby glyph
    # centres before estimating their height, so a lone colon/dot cannot seed
    # a tiny "text region" and exclude every full-size letter on that line.
    vertical_slack = max(3, min(18, (bottom - top) * .5))
    pad = max(4, min(64, round(min(width, height) * .045)), math.ceil(vertical_slack * 2))
    expanded = (max(0, left - pad), max(0, top - pad), min(width, right + pad), min(height, bottom + pad))
    crop = image.crop(expanded)
    color = background(image.crop(box))
    mask = bytearray(ImageChops.difference(crop, Image.new('RGB', crop.size, color)).convert('L').point(lambda p: 1 if p > 65 else 0).tobytes())
    cw, ch = crop.size
    components, nearby = [], []
    for seed in range(len(mask)):
        if not mask[seed]:
            continue
        mask[seed] = 0
        stack = [seed]
        x0 = x1 = seed % cw
        y0 = y1 = seed // cw
        area = 0
        positions = [] if protected is not None else None
        while stack:
            pos = stack.pop()
            if positions is not None:
                positions.append(pos)
            x, y = pos % cw, pos // cw
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            area += 1
            for neighbor in ([pos - 1] if x else []) + ([pos + 1] if x + 1 < cw else []) + ([pos - cw] if y else []) + ([pos + cw] if y + 1 < ch else []):
                if mask[neighbor]:
                    mask[neighbor] = 0
                    stack.append(neighbor)
        w, h = x1 - x0 + 1, y1 - y0 + 1
        cx, cy = expanded[0] + (x0 + x1) / 2, expanded[1] + (y0 + y1) / 2
        if max_glyph_height is not None and (h > max_glyph_height or w > max(12, h * 10)):
            if protected is not None:
                for pos in positions:
                    protected.putpixel((expanded[0] + pos % cw, expanded[1] + pos // cw), 255)
            continue  # Keep nearby art and rules out of the glyph selection.
        if area < 3 or w > max(12, h * (10 if max_glyph_height is not None else 2.5)) or h > max(60, w * 12) or area > cw * ch * .5 or area / (w * h) < .2:
            continue
        if w > cw * .7 and h > ch * .6:
            continue  # A surrounding rectangular frame is not a glyph.
        bounds = (x0 + expanded[0], y0 + expanded[1], x1 + expanded[0] + 1, y1 + expanded[1] + 1)
        if left - 2 <= cx <= right + 2 and top - vertical_slack <= cy <= bottom + vertical_slack:
            components.append(bounds)
        else:
            nearby.append(bounds)
    if components:
        heights = sorted(c[3] - c[1] for c in components)
        typical = heights[len(heights) // 2]
        nearby = [c for c in nearby if typical * .5 <= c[3] - c[1] <= typical * 1.5 and c[2] - c[0] <= typical * 1.8]
    for _ in range(3):
        additions = []
        for other in nearby:
            for selected in components:
                overlap_y = min(other[3], selected[3]) - max(other[1], selected[1])
                distance_x = max(other[0] - selected[2], selected[0] - other[2], 0)
                overlap_x = min(other[2], selected[2]) - max(other[0], selected[0])
                distance_y = max(other[1] - selected[3], selected[1] - other[3], 0)
                height_ratio = (other[3] - other[1]) / max(1, selected[3] - selected[1])
                same_line = overlap_y > min(other[3] - other[1], selected[3] - selected[1]) * .35 and distance_x < max(6, (other[3] - other[1]) * .7)
                next_line = overlap_x > 0 and .65 <= height_ratio <= 1.5 and distance_y < max(8, (other[3] - other[1]) * .85)
                if same_line or next_line:
                    additions.append(other)
                    break
        if not additions:
            break
        components.extend(additions)
        nearby = [item for item in nearby if item not in additions]
    if not components:
        return box
    margin = 1 if max_glyph_height is not None else 2
    refined = (max(0, min(c[0] for c in components) - margin), max(0, min(c[1] for c in components) - margin),
               min(width, max(c[2] for c in components) + margin), min(height, max(c[3] for c in components) + margin))
    # A few specks are insufficient evidence to shrink an entire model box.
    if (refined[2] - refined[0]) * (refined[3] - refined[1]) < (right - left) * (bottom - top) * .12:
        return box
    return refined


def wrap_text(text, font, width):
    lines = []
    for paragraph in text.splitlines() or ['']:
        line = ''
        for token in re.findall(r"[A-Za-z0-9]+(?:[’'-][A-Za-z0-9]+)*|\s+|[^\x00-\x7f]|.", paragraph):
            fragments = [token] if font.getlength(token) <= width else list(token)
            for fragment in fragments:
                if line and font.getlength(line + fragment) > width:
                    lines.append(line.rstrip())
                    line = fragment.lstrip()
                else:
                    line += fragment
        if line:
            lines.append(line.rstrip())
    return lines or ['']


def render_image(data, result, directory):
    from reader.image_regions import preserve_codes, qr_regions
    result = preserve_codes(data, result)
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except ImportError as exc:
        raise RuntimeError('生成中文译图需要 Pillow，请安装 requirements.txt 中的依赖') from exc
    digest = hashlib.sha256(data + json.dumps([RENDER_VERSION, result['blocks']], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    folder = Path(directory) / 'translated-images'
    folder.mkdir(exist_ok=True)
    filename = digest + '.png'
    target = folder / filename
    with Image.open(io.BytesIO(data)) as original:
        if original.width * original.height > 45_000_000:
            raise RuntimeError('图片尺寸超过译图排版上限')
        source = Image.new('RGB', original.size, 'white')
        if original.mode in {'RGBA', 'LA'} or original.info.get('transparency') is not None:
            rgba = original.convert('RGBA')
            source.paste(rgba, mask=rgba.getchannel('A'))
        else:
            source.paste(original.convert('RGB'))
    # Tiny imprints can contain legible Latin labels only a few pixels high.
    # Chinese needs more pixels per glyph. Render a higher-resolution variant
    # instead of shrinking below the readable font floor or dropping words.
    # Original EPUB bytes and normalized text coordinates remain unchanged.
    source_width, source_height = source.size
    native_source = source
    changed_text = any(block['source'].strip() != block['translation'].strip() for block in result['blocks'])
    scale = min(8, max(1, math.ceil(320 / min(source.size)))) if changed_text and max(source.size) < 320 else 1
    if scale > 1:
        source = source.resize((source.width * scale, source.height * scale), Image.Resampling.LANCZOS)
    dimensions = {'width': source.width, 'height': source.height,
                  'sourceWidth': source_width, 'sourceHeight': source_height, 'scale': scale}
    if target.is_file():
        return {'file': 'translated-images/' + filename, 'digest': digest, **dimensions}
    output = source.copy()
    protected = Image.new('L', (source_width, source_height), 0) if scale > 1 else None
    draw = ImageDraw.Draw(output)
    font_path = chinese_font() if result['blocks'] else None
    regions = []
    for index, block in enumerate(result['blocks']):
        if block['source'].strip() == block['translation'].strip():
            continue  # An explicitly retained label needs no erasing or retypesetting.
        bbox = block.get('bbox')
        if not isinstance(bbox, list) or len(bbox) != 4 or not 0 <= bbox[0] < bbox[2] <= 1000 or not 0 <= bbox[1] < bbox[3] <= 1000:
            raise RuntimeError(f'图片第 {index + 1} 处文字尚未准确定位，请重试图片校对')
        box = (round(bbox[0] * source_width / 1000), round(bbox[1] * source_height / 1000),
               round(bbox[2] * source_width / 1000), round(bbox[3] * source_height / 1000))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise RuntimeError('文字定位区域过小，请重新识别')
        lines_in_source = max(1, len([line for line in block['source'].splitlines() if line.strip()]))
        max_glyph_height = max(4, (box[3] - box[1]) / lines_in_source * 1.5) if scale > 1 else None
        box = text_region(native_source, box, max_glyph_height=max_glyph_height, protected=protected)
        box = tuple(coordinate * scale for coordinate in box)
        color = background(source.crop(box))
        available_w, available_h = box[2] - box[0], box[3] - box[1]
        chosen = None
        source_length = max(1, sum(char.isalnum() for char in block['source']))
        font_cap = min(52, max(12, round(math.sqrt(available_w * available_h / (source_length * .66)))))
        for size in range(min(font_cap, available_h), 5, -1):
            font = ImageFont.truetype(str(font_path), size)
            lines = wrap_text(block['translation'], font, available_w)
            line_height = max(1, round(size * 1.18))
            if line_height * len(lines) <= available_h and all(font.getlength(line) <= available_w for line in lines) and not (len(lines) > 1 and re.fullmatch(r'[。，！？；：、,.!?;:]+', lines[-1])):
                chosen = font, lines, line_height
                break
        if chosen is None:
            raise RuntimeError(f'图片第 {index + 1} 处文字区域无法容纳译文，已保留校对结果，请重试排版')
        font, lines, line_height = chosen
        draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=color)
        ink = (255, 255, 255) if sum(color) < 360 else (12, 12, 12)
        y = box[1] + max(0, (available_h - line_height * len(lines)) // 2)
        for line in lines:
            offset = font.getbbox(line)[1]
            draw.text((box[0], y - offset), line, font=font, fill=ink)
            y += line_height
        regions.append(list(box))
    if protected is not None and protected.getbbox():
        # Preserve the complete graphic, including its antialiased boundary,
        # when a tightly packed imprint puts a leg/flourish beside a label.
        mask = protected.filter(ImageFilter.MaxFilter(3)).resize(source.size, Image.Resampling.NEAREST)
        output.paste(source, (0, 0), mask)
    for left, top, right, bottom in qr_regions(data):
        box = (int(left * source.width / 1000), int(top * source.height / 1000),
               min(source.width, math.ceil(right * source.width / 1000)), min(source.height, math.ceil(bottom * source.height / 1000)))
        output.paste(source.crop(box), box[:2])
    temporary = target.with_suffix('.tmp')
    output.save(temporary, format='PNG', optimize=True)
    temporary.replace(target)
    return {'file': 'translated-images/' + filename, 'digest': digest, **dimensions, 'regions': regions}
