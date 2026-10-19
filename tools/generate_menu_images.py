"""サンプル商品のイラスト画像（SVG）を static/images/products/ に作る。

    py tools/generate_menu_images.py

写真の代わりの仮画像。実際の写真ができたら同じ商品IDの png / jpg / webp を置き、CSV で image_path を差し替える。
"""

from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "images" / "products"


def frame(background, body, accent="#ffffff"):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300" role="img">
<defs>
  <radialGradient id="bg" cx="50%" cy="40%" r="75%">
    <stop offset="0" stop-color="{accent}" stop-opacity="0.9"/>
    <stop offset="1" stop-color="{background}"/>
  </radialGradient>
</defs>
<rect width="400" height="300" fill="url(#bg)"/>
<ellipse cx="200" cy="262" rx="150" ry="18" fill="#000" opacity="0.10"/>
{body}
</svg>
"""


# ---------- 料理 ----------

def burger(cheese=False, sauce=None, double=False, patty="#7a4320", bun="#e0a04a", fish=False):
    layers = []
    y = 200
    layers.append(f'<path d="M100 {y} h200 a20 20 0 0 1 -20 28 h-160 a20 20 0 0 1 -20 -28z" fill="{bun}"/>')
    pattys = 2 if double else 1
    for _ in range(pattys):
        y -= 26
        if fish:
            layers.append(f'<rect x="96" y="{y}" width="208" height="26" rx="8" fill="#e8b14c"/>')
            layers.append(f'<rect x="96" y="{y}" width="208" height="8" rx="4" fill="#c98a2c"/>')
        else:
            layers.append(f'<rect x="92" y="{y}" width="216" height="26" rx="13" fill="{patty}"/>')
        if cheese:
            layers.append(f'<path d="M98 {y + 2} h204 l-18 22 l-16 -14 l-20 18 l-22 -16 l-20 16 l-24 -18 l-22 16 l-20 -14 z" fill="#ffc933"/>')
    if sauce:
        layers.append(f'<path d="M100 {y - 2} q25 14 50 0 t50 0 t50 0 t50 0 v6 h-200z" fill="{sauce}"/>')
    layers.append(f'<path d="M92 {y - 8} q20 -14 40 0 q20 -14 40 0 q20 -14 40 0 q20 -14 40 0 q20 -14 56 0 v10 h-216z" fill="#5fb548"/>')
    layers.append(f'<rect x="104" y="{y - 22}" width="192" height="12" rx="6" fill="#e2483d"/>')
    top = y - 22
    layers.append(f'<path d="M96 {top} q0 -86 104 -86 q104 0 104 86z" fill="{bun}"/>')
    for sx, sy in [(150, top - 50), (190, top - 62), (230, top - 48), (175, top - 30), (215, top - 28), (250, top - 30)]:
        layers.append(f'<ellipse cx="{sx}" cy="{sy}" rx="5" ry="3" fill="#fff4d6" transform="rotate(-20 {sx} {sy})"/>')
    return "\n".join(layers)


def plate(content, rim="#ffffff"):
    return f'<ellipse cx="200" cy="200" rx="160" ry="62" fill="{rim}" stroke="#e6dccf" stroke-width="4"/>' \
           f'<ellipse cx="200" cy="196" rx="120" ry="42" fill="#f7f2ea"/>{content}'


def salad():
    leaves = "".join(
        f'<ellipse cx="{x}" cy="{y}" rx="34" ry="18" fill="{c}" transform="rotate({r} {x} {y})"/>'
        for x, y, r, c in [(160, 186, -20, "#6cc04a"), (200, 176, 10, "#4fa83a"), (240, 188, 25, "#7bd15a"),
                           (180, 200, 5, "#58b043"), (222, 202, -15, "#6cc04a")]
    )
    toppings = '<circle cx="172" cy="178" r="12" fill="#e2483d"/><circle cx="236" cy="174" r="12" fill="#e2483d"/>' \
               '<rect x="186" y="160" width="46" height="18" rx="6" fill="#d9a066"/><rect x="200" y="190" width="40" height="16" rx="6" fill="#c98a4a"/>'
    return plate(leaves + toppings)


def kids_plate():
    return plate(
        '<ellipse cx="170" cy="190" rx="44" ry="26" fill="#7a4320"/><path d="M134 184 q36 -20 72 0" stroke="#b5332a" stroke-width="8" fill="none"/>'
        + "".join(f'<rect x="{230 + i * 10}" y="{160 + (i % 2) * 8}" width="9" height="46" rx="3" fill="#f2c14e" transform="rotate({-15 + i * 8} {234 + i * 10} 190)"/>' for i in range(5))
        + '<path d="M160 150 l0 -30 l26 10 z" fill="#2f7fd1"/><line x1="160" y1="150" x2="160" y2="174" stroke="#8a6a4a" stroke-width="3"/>'
    )


def curry():
    return plate('<path d="M90 196 q110 -70 220 0 q-110 46 -220 0z" fill="#ffffff"/>'
                 '<path d="M200 160 q90 6 108 36 q-50 30 -108 30z" fill="#b8661c"/>'
                 '<circle cx="240" cy="190" r="12" fill="#e08a2c"/><circle cx="270" cy="200" r="10" fill="#f0b04a"/><circle cx="226" cy="206" r="9" fill="#7a3d12"/>')


def napolitan():
    noodles = "".join(f'<path d="M{120 + i * 14} 200 q30 -40 60 0 t60 0" stroke="#e2763a" stroke-width="7" fill="none"/>' for i in range(6))
    return plate(noodles + '<circle cx="180" cy="180" r="9" fill="#5fb548"/><circle cx="236" cy="186" r="9" fill="#e2483d"/><rect x="206" y="166" width="24" height="10" rx="4" fill="#f5c6b8"/>')


def hotdog():
    return '<path d="M80 190 q120 -40 240 0 q-10 40 -120 44 q-110 -4 -120 -44z" fill="#e0a04a"/>' \
           '<rect x="72" y="166" width="256" height="30" rx="15" fill="#b8452c"/>' \
           '<path d="M90 172 q20 -10 40 0 t40 0 t40 0 t40 0 t40 0 t30 0" stroke="#ffd23f" stroke-width="6" fill="none"/>' \
           '<path d="M84 196 q116 30 232 0 v10 q-116 34 -232 0z" fill="#d18c38"/>'


# ---------- サイド ----------

def fries(spicy=False):
    sticks = "".join(
        f'<rect x="{130 + i * 18}" y="{70 + (i % 3) * 14}" width="14" height="110" rx="4" fill="#f5c542" transform="rotate({-12 + i * 3} {137 + i * 18} 160)"/>'
        for i in range(8)
    )
    specks = "".join(f'<circle cx="{140 + (i * 37) % 130}" cy="{90 + (i * 23) % 60}" r="3" fill="#c2331f"/>' for i in range(14)) if spicy else ""
    return sticks + specks + '<path d="M120 140 h160 l-18 110 h-124z" fill="#d93a2b"/><path d="M150 180 q50 20 100 0" stroke="#ffd23f" stroke-width="8" fill="none"/>'


def nuggets():
    pieces = "".join(f'<ellipse cx="{x}" cy="{y}" rx="34" ry="24" fill="#d99a3c" transform="rotate({r} {x} {y})"/>'
                     f'<ellipse cx="{x - 8}" cy="{y - 6}" rx="12" ry="6" fill="#f0c06a"/>'
                     for x, y, r in [(150, 190, -10), (210, 178, 8), (262, 196, -5), (180, 214, 12), (238, 220, -8)])
    return plate(pieces) + '<rect x="290" y="150" width="50" height="34" rx="8" fill="#ffffff" stroke="#e6dccf" stroke-width="3"/><ellipse cx="315" cy="156" rx="20" ry="7" fill="#e2483d"/>'


def onion_rings():
    rings = "".join(f'<ellipse cx="{x}" cy="{y}" rx="40" ry="26" fill="none" stroke="#d99a3c" stroke-width="16"/>'
                    for x, y in [(160, 196), (232, 186), (200, 216), (262, 214)])
    return plate(rings)


def fried_chicken():
    pieces = "".join(f'<path d="M{x - 40} {y} q-6 -44 40 -44 q46 0 40 44 q-10 26 -40 26 q-30 0 -40 -26z" fill="#c9792a"/>'
                     f'<path d="M{x - 26} {y - 20} q14 -10 30 -4" stroke="#e8a858" stroke-width="6" fill="none"/>'
                     for x, y in [(160, 210), (240, 206), (200, 182)])
    return plate(pieces + '<path d="M286 196 l26 -14 l-6 26z" fill="#ffe066"/>')


def soup(color, garnish="#5fb548"):
    return '<path d="M90 160 h220 q-6 90 -110 90 q-104 0 -110 -90z" fill="#ffffff" stroke="#e6dccf" stroke-width="4"/>' \
           f'<ellipse cx="200" cy="162" rx="108" ry="22" fill="{color}"/>' \
           f'<circle cx="180" cy="160" r="5" fill="{garnish}"/><circle cx="214" cy="164" r="4" fill="{garnish}"/><circle cx="196" cy="168" r="3" fill="{garnish}"/>' \
           '<path d="M170 120 q-10 -20 0 -40 M200 116 q-10 -20 0 -40 M230 120 q-10 -20 0 -40" stroke="#ffffff" stroke-width="5" fill="none" opacity="0.7"/>'


def sauce_bottle():
    return '<rect x="160" y="90" width="80" height="160" rx="26" fill="#d62f24"/><rect x="180" y="58" width="40" height="40" rx="8" fill="#f2f2f2"/>' \
           '<rect x="192" y="40" width="16" height="22" rx="4" fill="#f2f2f2"/><rect x="170" y="150" width="60" height="44" rx="8" fill="#ffffff"/>' \
           '<path d="M182 178 q8 -18 18 -4 q10 -14 18 4" stroke="#5fb548" stroke-width="5" fill="none"/>'


# ---------- 飲み物 ----------

def glass(liquid, foam=None, ice=True, straw="#ffffff", garnish="", top_y=110):
    ice_cubes = "".join(f'<rect x="{x}" y="{y}" width="34" height="30" rx="6" fill="#ffffff" opacity="0.55" transform="rotate({r} {x + 17} {y + 15})"/>'
                        for x, y, r in [(156, top_y + 12, 12), (200, top_y + 6, -10), (176, top_y + 44, 6)]) if ice else ""
    foam_part = f'<rect x="138" y="{top_y - 18}" width="124" height="24" rx="10" fill="{foam}"/>' if foam else ""
    straw_part = f'<rect x="222" y="{top_y - 80}" width="10" height="120" rx="5" fill="{straw}" transform="rotate(12 227 {top_y})"/>' if straw else ""
    return straw_part + \
        f'<path d="M130 {top_y - 30} h140 l-14 170 h-112z" fill="#eaf4fb" opacity="0.6" stroke="#cfe0ea" stroke-width="3"/>' + \
        f'<path d="M134 {top_y} h132 l-11 136 h-110z" fill="{liquid}"/>' + foam_part + ice_cubes + garnish


def cup(liquid, steam=True, art=False, color="#ffffff"):
    steam_part = '<path d="M180 92 q-12 -20 0 -40 M206 88 q-12 -20 0 -40 M232 92 q-12 -20 0 -40" stroke="#ffffff" stroke-width="6" fill="none" opacity="0.8"/>' if steam else ""
    art_part = '<path d="M196 128 q10 -16 20 0 q-10 16 -20 0z M206 120 v24" stroke="#f5e6d0" stroke-width="5" fill="none"/>' if art else ""
    return '<ellipse cx="200" cy="236" rx="110" ry="20" fill="#ffffff" stroke="#e6dccf" stroke-width="3"/>' \
           f'<path d="M126 120 h148 q0 110 -74 110 q-74 0 -74 -110z" fill="{color}" stroke="#e6dccf" stroke-width="3"/>' \
           '<path d="M272 140 q40 0 34 30 q-6 26 -40 26" stroke="#e6dccf" stroke-width="10" fill="none"/>' \
           f'<ellipse cx="200" cy="124" rx="72" ry="14" fill="{liquid}"/>' + art_part + steam_part


def beer():
    return '<path d="M126 90 h132 v150 q0 14 -14 14 h-104 q-14 0 -14 -14z" fill="#f2a81d"/>' \
           '<path d="M258 120 h26 q16 0 16 16 v60 q0 16 -16 16 h-26" stroke="#f2e6c8" stroke-width="12" fill="none"/>' \
           '<path d="M120 96 q14 -30 40 -18 q16 -24 42 -8 q22 -22 44 0 q24 -6 20 24 v14 h-146z" fill="#fffaf0"/>' \
           + "".join(f'<circle cx="{150 + (i * 29) % 90}" cy="{150 + (i * 37) % 80}" r="4" fill="#ffe08a"/>' for i in range(8))


def shake():
    return glass("#f5e1c8", foam=None, ice=False, straw="#e2483d", top_y=120,
                 garnish='<path d="M136 120 q16 -40 64 -40 q48 0 64 40z" fill="#ffffff"/><circle cx="200" cy="74" r="12" fill="#d0202f"/>')


def melon_soda():
    return glass("#3fbf5a", ice=True, straw="#ffffff", top_y=124,
                 garnish='<path d="M146 124 q14 -46 54 -46 q40 0 54 46z" fill="#fffaf0"/><circle cx="214" cy="72" r="12" fill="#d0202f"/>'
                         '<path d="M214 60 q6 -20 20 -24" stroke="#4a7a2a" stroke-width="3" fill="none"/>')


def sour():
    return glass("#f6f0c8", ice=True, straw="", top_y=110,
                 garnish='<circle cx="242" cy="96" r="28" fill="#ffe24d" stroke="#f0c419" stroke-width="5"/>'
                         '<path d="M242 70 v52 M216 96 h52 M224 78 l36 36 M260 78 l-36 36" stroke="#f0c419" stroke-width="3"/>')


# ---------- デザート ----------

def pudding():
    return '<ellipse cx="200" cy="236" rx="120" ry="22" fill="#ffffff" stroke="#e6dccf" stroke-width="3"/>' \
           '<path d="M140 230 l14 -110 h92 l14 110z" fill="#f7d26b"/>' \
           '<path d="M154 120 h92 l-3 22 q-43 14 -86 0z" fill="#8a4513"/>' \
           '<path d="M178 104 q22 -20 44 0 q-22 10 -44 0z" fill="#ffffff"/><circle cx="200" cy="92" r="10" fill="#d0202f"/>'


def parfait(chocolate=True):
    layers = ("#5b3214", "#f6efe6", "#8a5226", "#f6efe6") if chocolate else ("#e2483d", "#f6efe6", "#f2a81d", "#f6efe6")
    body = '<path d="M150 90 h100 l-16 120 h-68z" fill="#eaf4fb" stroke="#cfe0ea" stroke-width="3"/>'
    for i, color in enumerate(layers):
        body += f'<rect x="{156 + i * 3}" y="{110 + i * 24}" width="{88 - i * 6}" height="24" fill="{color}"/>'
    top = '<path d="M146 92 q20 -50 54 -40 q34 -10 54 40z" fill="#ffffff"/>'
    if chocolate:
        top += '<path d="M160 74 q40 20 80 0" stroke="#5b3214" stroke-width="6" fill="none"/><rect x="220" y="30" width="10" height="50" rx="4" fill="#8a5226" transform="rotate(20 225 55)"/>'
    else:
        top += '<circle cx="182" cy="62" r="12" fill="#e2483d"/><path d="M206 50 l20 -6 l-4 22z" fill="#f2a81d"/><circle cx="226" cy="70" r="10" fill="#7b4fbf"/>'
    return body + top + '<rect x="186" y="208" width="28" height="30" fill="#cfe0ea"/><ellipse cx="200" cy="242" rx="44" ry="10" fill="#cfe0ea"/>'


def ice_cream():
    return '<path d="M160 140 l40 110 l40 -110z" fill="#d99a4a"/>' \
           '<path d="M168 150 l64 0 M176 172 l48 0 M184 194 l32 0" stroke="#b5762a" stroke-width="4"/>' \
           '<circle cx="200" cy="120" r="48" fill="#fff6e0"/><circle cx="172" cy="128" r="22" fill="#fff6e0"/><circle cx="228" cy="128" r="22" fill="#fff6e0"/>'


def pancake():
    stack = "".join(f'<ellipse cx="200" cy="{214 - i * 22}" rx="110" ry="22" fill="#d9903a"/><ellipse cx="200" cy="{208 - i * 22}" rx="104" ry="18" fill="#f0c070"/>' for i in range(3))
    return plate("") + stack + '<rect x="176" y="136" width="48" height="22" rx="4" fill="#fff3b0"/>' \
                                '<path d="M150 150 q20 30 40 10 q20 30 50 0 q20 20 30 0" stroke="#b5651d" stroke-width="8" fill="none" opacity="0.8"/>'


def cheesecake():
    return plate('<path d="M130 210 l70 -70 l80 40 v30 l-80 40z" fill="#f7e2a8"/><path d="M200 140 l80 40 l-80 40 l-70 -10z" fill="#fff1c4"/>'
                 '<path d="M130 210 l70 30 l80 -30 v12 l-80 32 l-70 -32z" fill="#c98a4a"/><circle cx="214" cy="170" r="12" fill="#c2185b"/>')


# ---------- 商品と絵の対応 ----------

FOOD_BG, SIDE_BG, DRINK_BG, DESSERT_BG, OTHER_BG = "#f6dcc0", "#f7e3b6", "#cfe7f5", "#f8d7e3", "#e6e0d6"

PRODUCTS = {
    "P001": (FOOD_BG, burger()),
    "P002": (FOOD_BG, burger(cheese=True)),
    "P003": (FOOD_BG, burger(sauce="#d0202f")),
    "P004": (FOOD_BG, salad()),
    "P005": (FOOD_BG, kids_plate()),
    "P006": (SIDE_BG, fries()),
    "P007": (SIDE_BG, nuggets()),
    "P008": (SIDE_BG, fries(spicy=True)),
    "P009": (SIDE_BG, soup("#f6d35c")),
    "P010": (SIDE_BG, soup("#f0902c")),
    "P011": (DRINK_BG, glass("#4a2412")),
    "P012": (DRINK_BG, glass("#f7a21b")),
    "P013": (DRINK_BG, glass("#3a2210", straw="#2f7fd1")),
    "P014": (DRINK_BG, cup("#b5651d", color="#ffffff")),
    "P015": (DESSERT_BG, pudding()),
    "P016": (DESSERT_BG, parfait(chocolate=True)),
    "P017": (DESSERT_BG, ice_cream()),
    "P018": (OTHER_BG, sauce_bottle()),
    "P019": (FOOD_BG, burger(sauce="#4a2410", patty="#5a2e14")),
    "P020": (FOOD_BG, burger(cheese=True, double=True)),
    "P021": (FOOD_BG, burger(fish=True, cheese=True)),
    "P022": (FOOD_BG, hotdog()),
    "P023": (FOOD_BG, curry()),
    "P024": (FOOD_BG, napolitan()),
    "P025": (SIDE_BG, onion_rings()),
    "P026": (SIDE_BG, fried_chicken()),
    "P027": (DRINK_BG, melon_soda()),
    "P028": (DRINK_BG, glass("#f3d27a")),
    "P029": (DRINK_BG, glass("#a8641e", straw="")),
    "P030": (DRINK_BG, cup("#d8b38a", steam=True, art=True)),
    "P031": (DRINK_BG, shake()),
    "P032": (DRINK_BG, beer()),
    "P033": (DRINK_BG, sour()),
    "P034": (DESSERT_BG, pancake()),
    "P035": (DESSERT_BG, cheesecake()),
    "P036": (DESSERT_BG, parfait(chocolate=False)),
}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for product_id, (background, body) in PRODUCTS.items():
        (OUT_DIR / f"{product_id}.svg").write_text(frame(background, body), encoding="utf-8")
    print(f"{len(PRODUCTS)} 個の画像を作成しました: {OUT_DIR}")


if __name__ == "__main__":
    main()
