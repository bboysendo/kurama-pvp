"""
Generador del catálogo de cartas del PVP de Kurama Run.

Revisión del creador (02/11/2026):

1. Arte: solo se usan los artes que hay DIRECTAMENTE en ../CARDS (nunca
   ../CARDS/CARDS_NO_USE) y, para los huecos sin arte, los zorros NFT
   originales de ../GAMENFT. Cada arte de CARDS lleva pintados nombre y
   ATK/DEF, así que cada uno ocupa el hueco cuyas stats coinciden con lo
   pintado. Excepción: había dos artes 0/2 (KURAMARUN3 y ENEMIGOS1) — se
   queda Cyber-Kitsune (KURAMARUN3) como 0/2 y Raging Earthquake (ENEMIGOS1)
   pasa al hueco libre 3/2; para que el arte no contradiga a la carta, se
   genera una copia con los números repintados en
   engine/assets/cards/tz_raging_earthquake_3x2.png (el original de CARDS no
   se toca).

2. Pool base: 16 cartas ÚNICAS = todas las combinaciones de ataque
   3/3, 3/2, 3/1, 3/0, 2/2, 2/1, 2/0, 1/1, 1/0, 0/0 (ATK/DEF) y su inverso
   defensivo 2/3, 1/3, 0/3, 1/2, 0/2, 0/1 (las simétricas 3/3, 2/2, 1/1, 0/0
   son su propio inverso, no se duplican). Ninguna se repite en un mazo.

3. Especiales (las únicas que pueden repetirse en un mazo), con arte NFT:
   - Robo de Vida (LIFESTEAL): curan al atacante según el daño que hacen.
   - Combo Simultáneo (COMBO_STRIKE): +daño si ese turno se dañan 2 o 3
     zonas del rival a la vez.

Las cartas cuyo arte es un zorro NFT no traen marco ni números pintados, así
que este módulo genera su imagen de carta (marco + ilustración NFT + ATK/DEF)
en engine/assets/cards/<id>.png.

Ejecutar directamente (`python cards_catalog.py`) regenera:
- engine/assets/cards/*.png (cartas con arte NFT + la 3/2 repintada)
- cards_database.json
"""

from __future__ import annotations

import json
from pathlib import Path

from card_schema import Ability, Card, CardCategory, Element, Faction

ENGINE_DIR = Path(__file__).resolve().parent
PVP_ROOT = ENGINE_DIR.parent
CARDS_DIR = PVP_ROOT / "CARDS"
GAMENFT_DIR = PVP_ROOT / "GAMENFT"
GENERATED_DIR = ENGINE_DIR / "assets" / "cards"
GENERATED_REL = "engine/assets/cards"
DATABASE_PATH = ENGINE_DIR / "cards_database.json"

# Combinaciones exactas pedidas por el creador (ATK, DEF).
ATTACK_COMBOS = [(3, 3), (3, 2), (3, 1), (3, 0), (2, 2), (2, 1), (2, 0), (1, 1), (1, 0), (0, 0)]
DEFENSE_COMBOS = [(d, a) for a, d in ATTACK_COMBOS if a != d]  # inverso, sin duplicar simétricas
REQUIRED_BASE_COMBOS = ATTACK_COMBOS + DEFENSE_COMBOS  # 16 únicas

# Copia repintada del arte ENEMIGOS1 (0/2 pintado -> 3/2), ver repaint_raging_earthquake().
RAGING_EARTHQUAKE_SRC = "CARDS/ENEMIGOS1-removebg-preview.png"
RAGING_EARTHQUAKE_IMG = f"{GENERATED_REL}/tz_raging_earthquake_3x2.png"


def _nft_card(card_id: str, name: str, atk: int, defense: int, token_id: int, description: str,
              category: CardCategory = CardCategory.STANDARD, ability: Ability = Ability.NONE,
              element: Element = Element.ARCANE, heal_amount: int = 0) -> Card:
    return Card(
        id=card_id, name=name, faction=Faction.KURAMARUN, category=category,
        atk=atk, defense=defense, ability=ability, element=element,
        description=description, heal_amount=heal_amount,
        image_path=f"{GENERATED_REL}/{card_id}.png", nft_token_id=token_id,
    )


# --------------------------------------------------------------------------- #
# 1. Pool base: 16 cartas únicas
# --------------------------------------------------------------------------- #
BASE_CARDS: list[Card] = [
    # --- Ataque (ATK >= DEF) ------------------------------------------------
    Card(
        id="kr_celestial_operative_3x3", name="Celestial Kitsune Operative",
        faction=Faction.KURAMARUN, category=CardCategory.STANDARD, atk=3, defense=3,
        element=Element.ARCANE, description="Potencia simétrica máxima: espada y escudo a la vez.",
        image_path="CARDS/KURAMARUN2-removebg-preview.png",
    ),
    Card(
        id="tz_raging_earthquake_3x2", name="Raging Earthquake",
        faction=Faction.TALON_ZHEN, category=CardCategory.STANDARD, atk=3, defense=2,
        element=Element.EARTH, subtitle="(Talon Zhen)",
        description="Erizo de tierra furioso: embiste con púas y aguanta el golpe.",
        image_path=RAGING_EARTHQUAKE_IMG,
    ),
    Card(
        id="kr_pyre_cosmos_3x1", name="Pyre & Cosmos: Kitsune Celestial",
        faction=Faction.KURAMARUN, category=CardCategory.STANDARD, atk=3, defense=1,
        element=Element.FIRE, description="Invocadora de fuego y cosmos: ataque pesado con algo de guardia.",
        image_path="CARDS/KURAMARUN6-removebg-preview.png",
    ),
    Card(
        id="tz_shadow_claw_3x0", name="Shadow Claw: Aerial Predator",
        faction=Faction.TALON_ZHEN, category=CardCategory.STANDARD, atk=3, defense=0,
        element=Element.AIR, subtitle="(Talon Zhen)",
        description="Ataque Puro — depredador aéreo de las sombras.",
        image_path="CARDS/ENEMIGOS2-removebg-preview.png",
    ),
    Card(
        id="kr_celestial_duelist_2x2", name="Celestial Kitsune Operative (Duelist)",
        faction=Faction.KURAMARUN, category=CardCategory.STANDARD, atk=2, defense=2,
        element=Element.ARCANE, description="Duelista equilibrada: ataca y defiende por igual.",
        image_path="CARDS/KURAMARUN5-removebg-preview.png",
    ),
    _nft_card("nft_fox_11_2x1", "Fox #11 · Goggle Raider", 2, 1, 11,
              "Híbrida ofensiva — zorro NFT #11, golpe firme con algo de cobertura."),
    Card(
        id="kr_cyber_operative_2x0", name="Cyber-Kitsune Operative",
        faction=Faction.KURAMARUN, category=CardCategory.STANDARD, atk=2, defense=0,
        element=Element.ARCANE, description="Ataque Moderado — operativa kitsune cibernética.",
        image_path="CARDS/KURAMARUN1-removebg-preview.png",
    ),
    _nft_card("nft_fox_150_1x1", "Fox #150 · Headset Scout", 1, 1, 150,
              "Híbrida ligera — zorro NFT #150, pica y cubre a la vez."),
    _nft_card("nft_fox_666_1x0", "Fox #666 · Swift Jab", 1, 0, 666,
              "Ataque Ligero — zorro NFT #666, golpe rápido de bajo coste."),
    _nft_card("nft_fox_27_0x0", "Fox #27 · Feint", 0, 0, 27,
              "Finta — zorro NFT #27: no ataca ni defiende, solo despista al rival."),
    # --- Defensa (inverso: DEF > ATK) ---------------------------------------
    _nft_card("nft_fox_233_2x3", "Fox #233 · Samurai Wall", 2, 3, 233,
              "Híbrida defensiva pesada — zorro NFT #233, muro samurái que devuelve el golpe."),
    Card(
        id="kr_celestial_flux_1x3", name="Celestial Kitsune Operative (Flux)",
        faction=Faction.KURAMARUN, category=CardCategory.STANDARD, atk=1, defense=3,
        element=Element.ARCANE, description="Canalizadora de flujo: gran escudo y un toque de ataque.",
        image_path="CARDS/KURAMARUN4-removebg-preview.png",
    ),
    Card(
        id="tz_lava_rage_0x3", name="Lava Rage: Volcanic Striker",
        faction=Faction.TALON_ZHEN, category=CardCategory.STANDARD, atk=0, defense=3,
        element=Element.FIRE, subtitle="(Talon Zhen)",
        description="Defensa Pura / Escudo — coraza volcánica.",
        image_path="CARDS/ENEMIGOS4-removebg-preview.png",
    ),
    _nft_card("nft_fox_404_1x2", "Fox #404 · Ribbon Guard", 1, 2, 404,
              "Híbrida defensiva — zorro NFT #404, guardia con contragolpe ligero."),
    Card(
        id="kr_cyber_guardian_0x2", name="Cyber-Kitsune Operative (Guardian)",
        faction=Faction.KURAMARUN, category=CardCategory.STANDARD, atk=0, defense=2,
        element=Element.ARCANE, description="Defensa Ligera — variante de guardia con escudo de energía.",
        image_path="CARDS/KURAMARUN3-removebg-preview.png",
    ),
    Card(
        id="tz_flora_fury_0x1", name="Flora-Fury: Terrasprout",
        faction=Faction.TALON_ZHEN, category=CardCategory.STANDARD, atk=0, defense=1,
        element=Element.NATURE, subtitle="(Talon Zhen)",
        description="Defensa Pura Mínima — brote furioso recién germinado.",
        image_path="CARDS/ENEMIGOS3-removebg-preview.png",
    ),
]

# --------------------------------------------------------------------------- #
# 2. Especiales (pueden repetirse en un mazo) — arte NFT original
# --------------------------------------------------------------------------- #
SPECIAL_CARDS: list[Card] = [
    _nft_card("sp_lifesteal_fox_512", "Fox #512 · Vampiric Fang", 1, 0, 512,
              "Robo de Vida: recupera tantos HP como daño haga en su zona (máx. 2).",
              category=CardCategory.SPECIAL, ability=Ability.LIFESTEAL, element=Element.SHADOW,
              heal_amount=2),
    _nft_card("sp_lifesteal_fox_888", "Fox #888 · Blood Moon", 2, 0, 888,
              "Robo de Vida: recupera tantos HP como daño haga en su zona (máx. 3).",
              category=CardCategory.SPECIAL, ability=Ability.LIFESTEAL, element=Element.SHADOW,
              heal_amount=3),
    _nft_card("sp_combo_fox_999", "Fox #999 · Twin Storm", 1, 0, 999,
              "Combo Simultáneo: si este turno dañas 2 zonas a la vez, +2 de daño; si dañas las 3, +4.",
              category=CardCategory.SPECIAL, ability=Ability.COMBO_STRIKE, element=Element.NATURE),
    _nft_card("sp_combo_fox_1234", "Fox #1234 · Triple Tempest", 2, 0, 1234,
              "Combo Simultáneo: si este turno dañas 2 zonas a la vez, +2 de daño; si dañas las 3, +4.",
              category=CardCategory.SPECIAL, ability=Ability.COMBO_STRIKE, element=Element.NATURE),
]


def build_catalog() -> list[Card]:
    return list(BASE_CARDS) + list(SPECIAL_CARDS)


def validate_catalog(cards: list[Card], check_files: bool = True) -> None:
    """Reglas del creador — falla en voz alta si alguien rompe el catálogo."""
    base = [c for c in cards if c.category == CardCategory.STANDARD]
    specials = [c for c in cards if c.category == CardCategory.SPECIAL]
    combos = [(c.atk, c.defense) for c in base]
    assert len(combos) == len(set(combos)), f"Cartas base repetidas: {combos}"
    assert sorted(combos) == sorted(REQUIRED_BASE_COMBOS), (
        f"Combinaciones base incorrectas: faltan {set(REQUIRED_BASE_COMBOS) - set(combos)}, "
        f"sobran {set(combos) - set(REQUIRED_BASE_COMBOS)}"
    )
    assert all(c.ability == Ability.NONE for c in base), "Las cartas base no llevan habilidad"
    assert specials and all(c.ability in (Ability.LIFESTEAL, Ability.COMBO_STRIKE) for c in specials)
    assert all(c.nft_token_id is not None for c in specials), "Las especiales usan arte NFT"
    ids = [c.id for c in cards]
    assert len(ids) == len(set(ids)), "Ids de carta repetidos"
    for c in cards:
        assert c.image_path, f"{c.id} sin imagen"
        assert "CARDS_NO_USE" not in c.image_path, f"{c.id} usa arte descartado"
        if c.image_path.startswith("CARDS/"):
            assert c.image_path.count("/") == 1, f"{c.id}: solo arte de la raíz de CARDS/"
        if check_files:
            assert (PVP_ROOT / c.image_path).exists(), f"{c.id}: no existe {c.image_path}"


# --------------------------------------------------------------------------- #
# Generación de imágenes (requiere Pillow)
# --------------------------------------------------------------------------- #
_FONT_CANDIDATES = [
    "DejaVuSerif-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "georgiab.ttf", "C:/Windows/Fonts/georgiab.ttf", "timesbd.ttf", "arialbd.ttf",
]


def _font(size: int):
    from PIL import ImageFont

    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_center(draw, xy, text, size, fill=(255, 236, 190), stroke=(40, 22, 8), stroke_w=3):
    draw.text(xy, text, font=_font(size), fill=fill, anchor="mm", stroke_width=stroke_w, stroke_fill=stroke)


def _fit_text(draw, xy, text, max_w, size, **kw):
    while size > 10:
        bbox = draw.textbbox((0, 0), text, font=_font(size), stroke_width=2)
        if bbox[2] - bbox[0] <= max_w:
            break
        size -= 1
    _text_center(draw, xy, text, size, stroke_w=2, **kw)


# Paleta del marco según el rol de la carta.
_PALETTES = {
    "attack": ((120, 20, 24), (230, 70, 40)),
    "defense": ((16, 70, 60), (40, 190, 150)),
    "balanced": ((40, 30, 100), (120, 100, 230)),
    Ability.LIFESTEAL: ((80, 0, 30), (220, 30, 80)),
    Ability.COMBO_STRIKE: ((70, 40, 0), (255, 170, 30)),
}


def _palette_for(card: Card):
    if card.ability in _PALETTES:
        return _PALETTES[card.ability]
    if card.atk > card.defense:
        return _PALETTES["attack"]
    if card.defense > card.atk:
        return _PALETTES["defense"]
    return _PALETTES["balanced"]


def _hexagon(cx, cy, r):
    import math

    return [(cx + r * math.cos(math.radians(60 * i + 30)), cy + r * math.sin(math.radians(60 * i + 30)))
            for i in range(6)]


def generate_nft_card_image(card: Card, out_path: Path) -> None:
    """Carta con ilustración NFT: marco dorado, título arriba, zorro NFT al
    centro y ATK (izq.) · ATK/DEF (centro) · DEF (dcha.) abajo — mismo
    lenguaje visual y mismo lienzo 500x500 transparente que los artes de CARDS."""
    from PIL import Image, ImageDraw, ImageFilter

    W = H = 500
    x0, y0, x1, y1 = 57, 4, 443, 494  # mismo encuadre que los artes de CARDS
    dark, light = _palette_for(card)
    gold, gold_dark = (232, 190, 96), (120, 80, 20)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grad = Image.new("RGBA", (x1 - x0, y1 - y0))
    gd = ImageDraw.Draw(grad)
    for y in range(y1 - y0):
        t = y / (y1 - y0)
        col = tuple(int(light[i] * (1 - t) * 0.55 + dark[i] * (0.45 + 0.55 * t)) for i in range(3))
        gd.line([(0, y), (x1 - x0, y)], fill=col + (255,))
    mask = Image.new("L", grad.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, grad.size[0] - 1, grad.size[1] - 1], radius=26, fill=255)
    img.paste(grad, (x0, y0), mask)

    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse([120, 90, 380, 350], fill=light + (150,))
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(40)))

    fox = Image.open(GAMENFT_DIR / f"fox_{card.nft_token_id}.png").convert("RGBA")
    fox.thumbnail((330, 330), Image.LANCZOS)
    img.alpha_composite(fox, ((W - fox.width) // 2, 62 + (330 - fox.height) // 2))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x0, y0, x1, y1], radius=26, outline=gold_dark, width=10)
    d.rounded_rectangle([x0 + 4, y0 + 4, x1 - 4, y1 - 4], radius=23, outline=gold, width=4)
    d.rounded_rectangle([x0 + 14, y0 + 14, x1 - 14, y1 - 14], radius=18, outline=light + (255,), width=2)

    d.rounded_rectangle([x0 + 24, 16, x1 - 24, 60], radius=12, fill=(40, 24, 10, 235), outline=gold, width=3)
    _fit_text(d, (W // 2, 38), card.name.upper(), (x1 - x0) - 70, 22)

    if card.ability != Ability.NONE:
        label = {Ability.LIFESTEAL: "ROBO DE VIDA", Ability.COMBO_STRIKE: "COMBO SIMULTÁNEO"}.get(
            card.ability, "ESPECIAL"
        )
        d.rounded_rectangle([x0 + 70, 352, x1 - 70, 380], radius=10, fill=dark + (235,), outline=gold, width=2)
        _text_center(d, (W // 2, 366), label, 16, stroke_w=2)

    for cx, val, lab in ((118, card.atk, "ATK"), (382, card.defense, "DEF")):
        d.polygon(_hexagon(cx, 420, 46), fill=gold_dark)
        d.polygon(_hexagon(cx, 420, 40), fill=dark)
        d.polygon(_hexagon(cx, 420, 34), fill=light)
        _text_center(d, (cx, 412), str(val), 40)
        _text_center(d, (cx, 446), lab, 13, stroke_w=2)
    d.ellipse([196, 392, 304, 448], fill=(214, 182, 120), outline=gold_dark, width=4)
    _text_center(d, (250, 420), f"{card.atk}/{card.defense}", 34, fill=(250, 238, 205))

    d.rounded_rectangle([160, 458, 340, 484], radius=8, fill=(150, 110, 60), outline=gold_dark, width=2)
    _text_center(d, (250, 471), "KURAMARUN PVP", 14, stroke_w=2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def repaint_raging_earthquake(out_path: Path) -> None:
    """Copia de ENEMIGOS1 con el ATK pintado cambiado de 0 a 3 (hexágono
    izquierdo y óvalo central "0/2" -> "3/2"). Se tapa solo el dígito con el
    color de su propia placa y se vuelve a escribir encima."""
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.open(PVP_ROOT / RAGING_EARTHQUAKE_SRC).convert("RGBA")

    def patch(box, sample_xy, text, center, size, fill, stroke):
        color = img.getpixel(sample_xy)[:3]
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse(box, fill=color + (255,))
        img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(2)))
        ImageDraw.Draw(img).text(center, text, font=_font(size), fill=fill, anchor="mm",
                                 stroke_width=3, stroke_fill=stroke)

    patch(*RAGING_EARTHQUAKE_PATCHES[0])
    patch(*RAGING_EARTHQUAKE_PATCHES[1])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


# (caja a tapar, píxel de muestra del fondo, texto, centro, tamaño, color, contorno)
RAGING_EARTHQUAKE_PATCHES = [
    ((98, 384, 138, 428), (104, 392), "3", (118, 406), 40, (255, 236, 170), (30, 50, 20)),  # hexágono ATK
    ((207, 390, 238, 431), (285, 400), "3", (222, 410), 38, (255, 238, 190), (40, 26, 10)),  # óvalo "0/2"
]


def generate_images(cards: list[Card]) -> None:
    for card in cards:
        if card.nft_token_id is not None:
            generate_nft_card_image(card, GENERATED_DIR / f"{card.id}.png")
    repaint_raging_earthquake(PVP_ROOT / RAGING_EARTHQUAKE_IMG)


def main() -> None:
    cards = build_catalog()
    validate_catalog(cards, check_files=False)
    generate_images(cards)
    validate_catalog(cards, check_files=True)

    base = [c for c in cards if c.category == CardCategory.STANDARD]
    specials = [c for c in cards if c.category == CardCategory.SPECIAL]
    print(f"Base únicas: {len(base)} (esperado {len(REQUIRED_BASE_COMBOS)})")
    print(f"Especiales (repetibles): {len(specials)}")

    with open(DATABASE_PATH, "w", encoding="utf-8") as f:
        json.dump([c.to_dict() for c in cards], f, indent=2, ensure_ascii=False)
    print(f"Catálogo guardado en {DATABASE_PATH} ({len(cards)} cartas totales)")


if __name__ == "__main__":
    main()
