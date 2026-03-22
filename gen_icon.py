#!/usr/bin/env python3
"""生成漂亮的 Ping Monitor 图标"""
import math, json, os, subprocess, shutil
from PIL import Image, ImageDraw, ImageFilter

OUT = "/Users/mac/Desktop/未命名文件夹/icon.icns"
TMP = "/Users/mac/Desktop/未命名文件夹/icon.iconset"
SIZES = [16, 32, 64, 128, 256, 512, 1024]

os.makedirs(TMP, exist_ok=True)


def make_gradient(w: int, h: int, color_top, color_bot) -> Image.Image:
    """从上到下线性渐变"""
    base = Image.new("RGB", (w, h), color_top)
    grad = Image.new("RGB", (w, h), color_bot)
    # 用 blend 实现渐变
    result = Image.new("RGB", (w, h))
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(color_top[0] * (1 - t) + color_bot[0] * t)
        g = int(color_top[1] * (1 - t) + color_bot[1] * t)
        b = int(color_top[2] * (1 - t) + color_bot[2] * t)
        for x in range(w):
            # radial distance from center
            dist = math.hypot(x - w / 2, y - h / 2)
            max_d = math.hypot(w / 2, h / 2)
            radial = min(1.0, dist / max_d)
            # mix top/bot with radial factor
            tr = int(color_top[0] * (1 - radial) + color_bot[0] * radial)
            tg = int(color_top[1] * (1 - radial) + color_bot[1] * radial)
            tb = int(color_top[2] * (1 - radial) + color_bot[2] * radial)
            result.putpixel((x, y), (r, g, b))
    return result


def draw_icon(sz: int) -> Image.Image:
    # —— 圆角画布 + 深色渐变背景 ——

    # 背景：径向渐变，深蓝 -> 暗青
    bg = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    cx, cy = sz // 2, sz // 2
    max_r = sz * 0.707  # 角到中心距离
    # 分辨率超过 32 时用快速采样
    step = max(1, sz // 64)
    for y in range(0, sz, step):
        for x in range(0, sz, step):
            dist = math.hypot(x - cx, y - cy)
            t = min(1.0, dist / max_r)
            # 中心 #0d2840 往外 #040c18
            rr = int(13 - t * 9)
            gg = int(40 - t * 28)
            bb = int(64 - t * 46)
            for dx in range(step):
                for dy in range(step):
                    if 0 <= x + dx < sz and 0 <= y + dy < sz:
                        bg.putpixel((x + dx, y + dy), (rr, gg, bb, 255))

    # —— 圆角裁切遮罩 ——

    def round_clip(src: Image.Image, radius_frac=0.22) -> Image.Image:
        radius = int(sz * radius_frac)
        mask = Image.new("L", (sz, sz), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, sz - 1, sz - 1], radius=radius, fill=255
        )
        mask = mask.filter(ImageFilter.SMOOTH)
        out = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        out.paste(src, mask=mask)
        return out

    bg = round_clip(bg)

    # 叠加层（带透明度以便抗锯齿边缘可见）
    overlay = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # —— 三层 Ping 信号弧（青色，底部发射向上扩散）——
    ox, oy = sz // 2, int(sz * 0.60)   # 发射源在偏下位置

    # 亮边
    bright = [(0, 255, 220, 255), (0, 200, 255, 255), (0, 160, 255, 200)]
    # 阴影（稍暗一点）
    shadow = [(0, 140, 180, 255), (0, 110, 160, 220), (0, 80, 130, 180)]

    radii_frac = [0.40, 0.29, 0.19]   # 相对 canvas
    thick_frac = [0.09, 0.07, 0.055]
    span = 130  # 弧度跨度（度）
    start_base = 205

    for i, (rf, tf) in enumerate(zip(radii_frac, thick_frac)):
        r = int(sz * rf)
        thick = max(1, int(sz * tf))
        # 阴影（往内一点）
        draw.arc([ox - r, oy - r, ox + r, oy + r],
                 start=start_base + i * 5, end=start_base + span - i * 5,
                 fill=shadow[i], width=thick)
        # 亮边
        draw.arc([ox - r, oy - r, ox + r, oy + r],
                 start=start_base + i * 5, end=start_base + span - i * 5,
                 fill=bright[i], width=max(1, thick // 2))

    # —— 中心发射点（发光效果）——
    dot = int(sz * 0.06)
    # 外发光
    glow = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_r = int(sz * 0.12)
    glow_draw.ellipse([ox - glow_r, oy - glow_r, ox + glow_r, oy + glow_r],
                      fill=(0, 255, 200, 60))
    glow_draw.ellipse([ox - glow_r // 2, oy - glow_r // 2,
                        ox + glow_r // 2, oy + glow_r // 2],
                      fill=(0, 255, 200, 120))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=sz // 25))
    bg = Image.alpha_composite(bg, glow)

    # 实心圆点
    draw.ellipse([ox - dot, oy - dot, ox + dot, oy + dot],
                  fill=(0, 255, 210, 255))

    # 叠加弧线
    bg = Image.alpha_composite(bg, overlay)

    # —— "PING" 文字（大尺寸才显示）——
    if sz >= 64:
        try:
            from PIL import ImageFont
            # 找系统字体
            font_paths = [
                "/System/Library/Fonts/SFProDisplay-Bold.otf",
                "/System/Library/Fonts/Helvetica.ttc",
                "/Library/Fonts/Arial Bold.ttf",
                "/Library/Fonts/Arial.ttf",
            ]
            font = None
            for fp in font_paths:
                if os.path.exists(fp):
                    font = ImageFont.truetype(fp, max(8, sz // 7))
                    break
            if font is None:
                font = ImageFont.load_default()

            text = "PING"
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = (sz - tw) // 2
            ty = int(sz * 0.77)

            # 阴影
            draw.text((tx + 1, ty + 1), text, font=font, fill=(0, 0, 0, 100))
            # 主文字
            draw.text((tx, ty), text, font=font, fill=(0, 230, 255, 255))
        except Exception:
            pass

    return bg


# 生成所有尺寸（含 @2x Retina）
icons = []
for sz in SIZES:
    img = draw_icon(sz)
    p1 = f"{TMP}/icon_{sz}x{sz}.png"
    img.save(p1, "PNG")
    icons.append({"filename": f"icon_{sz}x{sz}.png", "size": f"{sz}x{sz}"})

    img2 = draw_icon(sz * 2)
    p2 = f"{TMP}/icon_{sz}x{sz}@2x.png"
    img2.save(p2, "PNG")
    icons.append({"filename": f"icon_{sz}x{sz}@2x.png", "size": f"{sz}x{sz}@2x"})

# Contents.json
with open(f"{TMP}/Contents.json", "w") as f:
    json.dump({"images": icons, "info": {"version": 1, "author": "xcode"}}, f, indent=2)

print(f"生成 {len(icons)} 张图片...")
subprocess.run(["iconutil", "-c", "icns", "-o", OUT, TMP], check=True)
print(f"✓ 完成  {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB)")
shutil.rmtree(TMP)
