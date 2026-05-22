from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict


def _get_text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    """Return (w, h) for drawable text in a Pillow-version-robust way."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        try:
            # older Pillow may expose getmask
            mask = font.getmask(text)
            return mask.size
        except Exception:
            # last resort heuristic
            return (len(text) * 7, int(font.size if hasattr(font, 'size') else 14))


def render_dashboard_image(reminders: List[Dict], username: str) -> BytesIO:
    # Canvas
    width, height = 720, 380
    bg_color = (24, 26, 27)  # dark background
    accent_color = (86, 194, 121)  # green accent
    text_color = (235, 235, 235)
    secondary_text = (150, 150, 150)
    box_color = (36, 38, 39)
    button_color = (42, 44, 45)
    border_radius = 16

    # Create image
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Accent bar
    draw.rectangle([0, 0, 8, height], fill=accent_color)

    # Fonts (adjust paths if needed)
    try:
        font_title = ImageFont.truetype("arialbd.ttf", 28)
        font_sub = ImageFont.truetype("arial.ttf", 18)
        font_text = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        # fallback to DejaVu if Arial isn't available, then the default bitmap
        try:
            font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
            font_sub = ImageFont.truetype("DejaVuSans.ttf", 18)
            font_text = ImageFont.truetype("DejaVuSans.ttf", 16)
        except Exception:
            font_title = ImageFont.load_default()
            font_sub = ImageFont.load_default()
            font_text = ImageFont.load_default()

    # Title
    draw.text((28, 24), "🕒 REMINDER DASHBOARD", fill=text_color, font=font_title)
    draw.text((32, 62), f"started by @{username}", fill=secondary_text, font=font_sub)

    # Timezone dropdown (mock inside image)
    dropdown_x, dropdown_y = 28, 110
    dropdown_w, dropdown_h = 250, 36
    draw.rounded_rectangle(
        [dropdown_x, dropdown_y, dropdown_x + dropdown_w, dropdown_y + dropdown_h],
        radius=10,
        fill=box_color,
    )
    draw.text((dropdown_x + 12, dropdown_y + 8), "Select Timezone ▼", fill=secondary_text, font=font_text)

    # Buttons (vertical pills at left) — visual only
    buttons = ["Set Timezone", "List Reminder", "Delete Reminder"]
    btn_w, btn_h = 180, 40
    btn_gap = 14
    btn_x = 28
    btn_y = 180

    for i, label in enumerate(buttons):
        y = btn_y + i * (btn_h + btn_gap)
        draw.rounded_rectangle(
            [btn_x, y, btn_x + btn_w, y + btn_h],
            radius=10,
            fill=button_color,
        )
        w, h = _get_text_size(draw, label, font_text)
        draw.text(
            (btn_x + (btn_w - w) / 2, y + (btn_h - h) / 2),
            label,
            fill=text_color,
            font=font_text,
        )

    # Optional: list sample reminders (display area)
    reminders_x = 320
    reminders_y = 110
    draw.text((reminders_x, 80), "Upcoming Reminders", fill=text_color, font=font_sub)
    for i, r in enumerate(reminders[:5]):
        text = f"• {r.get('message', '')} — {r.get('time_display', '')}"
        draw.text((reminders_x, reminders_y + i * 28), text, fill=secondary_text, font=font_text)

    # Rounded corner mask (apply rounded corners to the whole image)
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, width, height], border_radius, fill=255)
    rounded = Image.new("RGB", (width, height), (0, 0, 0))
    rounded.paste(img, mask=mask)

    # Save to bytes
    img_io = BytesIO()
    rounded.save(img_io, "PNG")
    img_io.seek(0)
    return img_io
from PIL import Image, ImageDraw, ImageFont
import io
from typing import List, Dict


def _load_font(size: int, bold: bool = False):
    # Try common fonts; fall back to default
    candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "Arial.ttf",
        "arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_dashboard_image(reminders: List[Dict], username: str) -> io.BytesIO:
    """Render a dashboard-style card as PNG and return BytesIO.

    reminders: list of dicts with keys: id, message, time_display
    username: display name string
    """
    # Layout config
    max_items = 8
    items = reminders[:max_items]
    width = 1000
    header_h = 96
    item_h = 72
    padding = 20
    gap = 12
    height = header_h + padding * 2 + len(items) * item_h + max(0, (len(items) - 1)) * gap
    radius = 18

    # Colors
    bg_color = (15, 15, 15)
    card_color = (28, 28, 29)
    accent_color = (46, 204, 113)
    title_color = (235, 235, 235)
    sub_color = (165, 165, 165)
    box_color = (34, 34, 35)
    separator_color = (55, 55, 55)

    img = Image.new("RGBA", (width, max(220, height)), bg_color)
    draw = ImageDraw.Draw(img)

    # Card rectangle
    card_margin = 16
    card_box = [card_margin, card_margin, width - card_margin, height - card_margin]
    draw.rounded_rectangle(card_box, radius=radius, fill=card_color)

    # Accent stripe
    stripe_w = 10
    stripe_box = [card_box[0] + 12, card_box[1] + 12, card_box[0] + 12 + stripe_w, card_box[3] - 12]
    draw.rounded_rectangle(stripe_box, radius=6, fill=accent_color)

    # Fonts
    title_font = _load_font(34, bold=True)
    header_font = _load_font(18, bold=True)
    item_font = _load_font(16)
    small_font = _load_font(13)

    # Title
    title_x = stripe_box[2] + 20
    title_y = card_box[1] + 18
    draw.text((title_x, title_y), "🎛️  Reminder Dashboard", font=title_font, fill=title_color)

    # Username right-aligned
    uname_txt = f"{username}"
    bbox_uname = draw.textbbox((0, 0), uname_txt, font=small_font)
    w_uname = bbox_uname[2] - bbox_uname[0]
    h_uname = bbox_uname[3] - bbox_uname[1]
    draw.text((card_box[2] - 22 - w_uname, title_y + 10), uname_txt, font=small_font, fill=sub_color)

    # Separator under header
    # Dropdown mock (top area) - shows current timezone selection UI inside the image
    dd_x = title_x
    dd_y = title_y + 36
    dd_w = 360
    dd_h = 44
    dd_box = [dd_x, dd_y, dd_x + dd_w, dd_y + dd_h]
    draw.rounded_rectangle(dd_box, radius=8, fill=box_color, outline=separator_color, width=1)
    dd_label = "Select timezone"
    try:
        bbox_dd = draw.textbbox((0, 0), dd_label, font=item_font)
        dd_tw = bbox_dd[2] - bbox_dd[0]
        dd_th = bbox_dd[3] - bbox_dd[1]
    except Exception:
        dd_tw = len(dd_label) * 7
        dd_th = 16
    draw.text((dd_x + 12, dd_y + (dd_h - dd_th) / 2), dd_label, font=item_font, fill=title_color)
    # small chevron on right
    try:
        draw.text((dd_x + dd_w - 28, dd_y + (dd_h - dd_th) / 2), "▾", font=header_font, fill=sub_color)
    except Exception:
        draw.text((dd_x + dd_w - 28, dd_y + (dd_h - dd_th) / 2), "v", font=item_font, fill=sub_color)

    # Separator under header area
    qa_y = dd_y + dd_h + 12
    draw.line((title_x, qa_y, card_box[2] - 22, qa_y), fill=separator_color, width=1)

    # Quick-action pill buttons inside image (visual only)
    btns = [
        ("📝 List", (86, 110, 255)),
        ("🗑️ Delete", (75, 75, 75)),
        ("🌐 Timezone", (46, 204, 113)),
    ]
    btn_h = 40
    btn_spacing = 12
    # start x aligned with title
    btn_x = title_x
    btn_y = qa_y + 16
    for label, color in btns:
        try:
            bbox = draw.textbbox((0, 0), label, font=item_font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except Exception:
            text_w = len(label) * 8
            text_h = 16
        pad_x = 18
        btn_w = text_w + pad_x * 2
        btn_box = [btn_x, btn_y, btn_x + btn_w, btn_y + btn_h]
        # button fill and border
        draw.rounded_rectangle(btn_box, radius=10, fill=color, outline=separator_color, width=1)
        # text (white for colored buttons, dark for grey)
        text_color = title_color if color != (75, 75, 75) else (230, 230, 230)
        draw.text((btn_box[0] + pad_x, btn_box[1] + (btn_h - text_h) / 2), label, font=item_font, fill=text_color)
        btn_x += btn_w + btn_spacing

    # Items (reminder rows) start below the visual buttons
    items_start_y = btn_y + btn_h + 20
    x_left = title_x
    for idx, item in enumerate(items):
        top = items_start_y + idx * (item_h + gap)
        left = x_left
        right = card_box[2] - 30
        bottom = top + item_h

        # item bg
        draw.rounded_rectangle([left, top, right, bottom], radius=12, fill=box_color)

        # numeric badge
        badge_w = 56
        badge_box = [left + 12, top + 12, left + 12 + badge_w, bottom - 12]
        draw.rounded_rectangle(badge_box, radius=8, outline=separator_color, width=1)
        idx_txt = f"{idx+1:02d}"
        bbox_idx = draw.textbbox((0, 0), idx_txt, font=header_font)
        w_idx = bbox_idx[2] - bbox_idx[0]
        h_idx = bbox_idx[3] - bbox_idx[1]
        draw.text((badge_box[0] + (badge_w - w_idx) / 2, top + (item_h - h_idx) / 2), idx_txt, font=header_font, fill=title_color)

        # message and time
        msg = item.get("message", "")[:60]
        time_display = item.get("time_display", "")
        draw.text((left + 86, top + 14), msg, font=item_font, fill=title_color)
        draw.text((left + 86, top + 40), time_display, font=small_font, fill=sub_color)

    # Empty hint
    if not items:
        hint = "No active reminders. Use /reminder to create one."
        draw.text((title_x, items_start_y + 8), hint, font=item_font, fill=sub_color)

    out = io.BytesIO()
    img = img.convert("RGB")
    img.save(out, format="PNG")
    out.seek(0)
    return out
