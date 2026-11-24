"""Génération de sous-titres ASS (styles personnalisables, mode karaoké mot à mot)."""
import re

STYLES = {
    # nom: (police, taille/1080, primaire (BGR ASS), secondaire, contour, épaisseur contour, gras, mots/ligne, marge verticale %)
    "bold":    ("Arial", 0.062, "&H00FFFFFF", "&H00FFFFFF", "&H00000000", 5, 1, 3, 0.18),
    "yellow":  ("Arial", 0.066, "&H0000E5FF", "&H0000E5FF", "&H00000000", 6, 1, 3, 0.18),
    "karaoke": ("Arial", 0.062, "&H0000E5FF", "&H00FFFFFF", "&H00000000", 5, 1, 4, 0.18),
    "minimal": ("Arial", 0.045, "&H00FFFFFF", "&H00FFFFFF", "&H80000000", 2, 0, 6, 0.08),
}

def _t(sec):
    sec = max(sec, 0); h = int(sec // 3600); m = int(sec % 3600 // 60); s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def _clean(w): return re.sub(r"[{}\\]", "", w)

def chunk(words, per_line):
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= per_line or re.search(r"[.!?…]$", w["w"]) :
            out.append(cur); cur = []
    if cur: out.append(cur)
    return out

def build_ass(words, width, height, style="bold", offset=0.0, overrides=None):
    """words: [{'w','s','e'}] en temps absolu ; offset = début du clip (secondes)."""
    name = style if style in STYLES else "bold"
    font, size, prim, sec, outl, ow, bold, per, margin = STYLES[name]
    o = overrides or {}
    font = o.get("font", font); prim = o.get("color", prim)
    px = int(min(width, height) * size * 1.2)
    px = int(o.get("size", px))
    upper = o.get("uppercase", name != "minimal")
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
            "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Default,{font},{px},{prim},{sec},{outl},&H64000000,{-1 if bold else 0},0,0,0,100,100,0,0,1,"
            f"{ow * height / 1080:.1f},0,2,{int(width * 0.06)},{int(width * 0.06)},{int(height * margin)},1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines = []
    for grp in chunk([w for w in words if w["w"].strip()], per):
        start = grp[0]["s"] - offset; end = grp[-1]["e"] - offset
        if name == "karaoke":
            parts = []
            for i, w in enumerate(grp):
                nxt = grp[i + 1]["s"] if i + 1 < len(grp) else w["e"]
                parts.append(f"{{\\k{max(int((nxt - w['s']) * 100), 1)}}}{_clean(w['w'])}")
            text = " ".join(parts)
        else:
            text = " ".join(_clean(w["w"]) for w in grp)
        if upper and name != "karaoke": text = text.upper()
        elif upper: text = re.sub(r"(\\k\d+\})(.*?)( |$)", lambda m: m.group(1) + m.group(2).upper() + m.group(3), text)
        lines.append(f"Dialogue: 0,{_t(start)},{_t(end + 0.15)},Default,,0,0,0,,{text}")
    return head + "\n".join(lines) + "\n"
