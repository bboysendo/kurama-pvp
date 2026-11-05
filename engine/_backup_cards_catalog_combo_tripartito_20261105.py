"""
Generador del catálogo de cartas del PVP de Kurama Run.

Regla del creador (02/11/2026): UNICIDAD TOTAL. El juego tiene exactamente
16 cartas, una por cada combinación ATK/DEF de 0 a 3 — ataque 3/3, 3/2, 3/1,
3/0, 2/2, 2/1, 2/0, 1/1, 1/0, 0/0 y su inverso defensivo 2/3, 1/3, 0/3, 1/2,
0/2, 0/1 — sin ninguna carta repetida ni dos cartas con la misma
combinación, ni en el catálogo ni en un mazo ni en una mano.

Arte: solo los artes que hay DIRECTAMENTE en ../CARDS (nunca
../CARDS/CARDS_NO_USE), cada uno en el hueco de las stats que lleva
pintadas; los 6 huecos sin arte usan zorros NFT originales de ../GAMENFT.
- Raging Earthquake (ENEMIGOS1) viene ya repintado por el creador como 1/2;
  como el archivo trae fondo negro opaco y 1254x1254, se usa una copia
  normalizada (fondo transparente, 500x500 como el resto) en
  engine/assets/cards/tz_raging_earthquake_1x2.png. El original no se toca.
- El zorro NFT #404, que antes ocupaba 1/2, pasa al hueco libre 3/2.
- Las antiguas especiales con arte NFT (Robo de Vida / Combo Simultáneo, 1/0
  y 2/0) duplicaban combinaciones ya cubiertas y no quedaba ninguna libre,
  así que se retiran del catálogo (el motor conserva esas habilidades por si
  se asignan a alguna de las 16 cartas más adelante).

Las cartas con zorro NFT no traen marco ni números pintados: este módulo
genera su imagen (marco + ilustración NFT + ATK/DEF) en
engine/assets/cards/<id>.png.

Ejecutar directamente (`python cards_catalog.py`) regenera:
- engine/assets/cards/*.png (cartas con arte NFT + la copia normalizada de Raging Earthquake)
- cards_database.json
"""

from __future__ import annotations

import json
from pathlib import Path

from card_schema import (
    HEALER_HEAL_AMOUNT, TRIPARTITE_COMBO_BONUS, ZONE_HIT_DAMAGE, Ability, Card, CardCategory, Element, Faction,
)

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

# Copia normalizada del arte ENEMIGOS1 (1/2), ver normalize_card_art().
RAGING_EARTHQUAKE_SRC = "CARDS/ENEMIGOS1-removebg-preview.png"
RAGING_EARTHQUAKE_IMG = f"{GENERATED_REL}/tz_raging_earthquake_1x2.png"


def _nft_card(card_id: str, name: str, atk: int, defense: int, token_id: int, description: str,
              category: CardCategory = CardCategory.STANDARD, ability: Ability = Ability.NONE,
              element: Element = Element.ARCANE, heal_amount: int = 0, energy_bonus: int = 0) -> Card:
    return Card(
        id=card_id, name=name, faction=Faction.KURAMARUN, category=category,
        atk=atk, defense=defense, ability=ability, element=element,
        description=description, heal_amount=heal_amount, energy_bonus=energy_bonus,
        image_path=f"{GENERATED_REL}/{card_id}.png", nft_token_id=token_id,
    )


# --------------------------------------------------------------------------- #
# Las 16 cartas del juego (una por combinación ATK/DEF)
# --------------------------------------------------------------------------- #
BASE_CARDS: list[Card] = [
    # --- Ataque (ATK >= DEF) ------------------------------------------------
    Card(
        id="kr_celestial_operative_3x3", name="Celestial Kitsune Operative",
        faction=Faction.KURAMARUN, category=CardCategory.STANDARD, atk=3, defense=3,
        element=Element.ARCANE, description="Potencia simétrica máxima: espada y escudo a la vez.",
        image_path="CARDS/KURAMARUN2-removebg-preview.png",
    ),
    _nft_card("nft_fox_404_3x2", "Fox #404 · Ribbon Blade", 3, 2, 404,
              "Ataque pesado con guardia — zorro NFT #404, golpea fuerte sin descuidarse."),
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
    Card(
        id="tz_raging_earthquake_1x2", name="Raging Earthquake",
        faction=Faction.TALON_ZHEN, category=CardCategory.STANDARD, atk=1, defense=2,
        element=Element.EARTH, subtitle="(Talon Zhen)",
        description="Híbrida defensiva — erizo de tierra furioso: se cubre con púas y pincha.",
        image_path=RAGING_EARTHQUAKE_IMG,
    ),
    Card(
        id="kr_cyber_guardian_0x2", name="Cyber-Kitsune Operative (Guardian)",
        faction=Faction.KURAMARUN, category=CardCategory.STANDARD, atk=0, defense=2,
        element=Element.ARCANE, description="Defensa Ligera — variante de guardia con escudo de energía.",
        image_path="CARDS/KURAMARUN3estrecha.png",
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
# Las 7 cartas ESPECIALES (coste 2) — revisión del creador del 03/11/2026,
# efectos definitivos de Sendo del 05/11/2026 (ver pvp_engine.py)
# --------------------------------------------------------------------------- #
# Stats fijadas por el creador. Ataque: 3/0, 2/0, 2/2, 1/0. Curación (femeninas): 0/3, 0/2, 1/1.
SPECIAL_ATTACK_COMBOS = [(3, 0), (2, 0), (2, 2), (1, 0)]
SPECIAL_GUARD_COMBOS = [(0, 3), (0, 2), (1, 1)]


def _tri_txt(atk: int) -> str:
    total = atk * ZONE_HIT_DAMAGE + TRIPARTITE_COMBO_BONUS
    zonas = "la zona" if atk == 1 else f"las {atk} zonas"
    return (f"Combo Tripartito: si impacta en {zonas} sin que le bloqueen ningún ataque, hace "
            f"{ZONE_HIT_DAMAGE} por zona + {TRIPARTITE_COMBO_BONUS} = {total} de daño.")


def _heal_txt(defense: int) -> str:
    zonas = "la zona" if defense == 1 else f"las {defense} zonas"
    return f"Curación: si defiendes {zonas} que permite, recibes +{HEALER_HEAL_AMOUNT} de vida."


SPECIAL_CARDS: list[Card] = [
    # --- Ataque con Combo Tripartito ---------------------------------------
    _nft_card("sp_fox_1468_3x0", "Fox #1468 · Ronin Executioner", 3, 0, 1468,
              "Especial de ataque — samurái ronin que remata sin piedad. " + _tri_txt(3),
              category=CardCategory.SPECIAL, ability=Ability.TRIPARTITE_STRIKE, element=Element.FIRE),
    _nft_card("sp_fox_87_2x0", "Fox #87 · Crimson Goggles", 2, 0, 87,
              "Especial de ataque — piloto carmesí que busca el hueco en la guardia. " + _tri_txt(2),
              category=CardCategory.SPECIAL, ability=Ability.TRIPARTITE_STRIKE, element=Element.FIRE),
    _nft_card("sp_fox_1597_2x2", "Fox #1597 · Shadow Hat Duelist", 2, 2, 1597,
              "Especial de ataque — duelista de las sombras, golpea y se cubre. " + _tri_txt(2),
              category=CardCategory.SPECIAL, ability=Ability.TRIPARTITE_STRIKE, element=Element.SHADOW),
    _nft_card("sp_fox_487_1x0", "Fox #487 · Neon Pounce", 1, 0, 487,
              "Especial de ataque — salto de neón tan rápido que nadie lo ve venir. " + _tri_txt(1),
              category=CardCategory.SPECIAL, ability=Ability.TRIPARTITE_STRIKE, element=Element.AIR),
    # --- Femeninas de Curación ---------------------------------------------
    _nft_card("sp_fox_1281_0x3", "Fox #1281 · Moonshrine Guardian", 0, 3, 1281,
              "Especial de curación — guardiana del santuario lunar. " + _heal_txt(3),
              category=CardCategory.SPECIAL, ability=Ability.HEALER, element=Element.WATER,
              heal_amount=HEALER_HEAL_AMOUNT),
    _nft_card("sp_fox_963_0x2", "Fox #963 · Blossom Ward", 0, 2, 963,
              "Especial de curación — protectora de los cerezos. " + _heal_txt(2),
              category=CardCategory.SPECIAL, ability=Ability.HEALER, element=Element.NATURE,
              heal_amount=HEALER_HEAL_AMOUNT),
    _nft_card("sp_fox_444_1x1", "Fox #444 · Sakura Medic", 1, 1, 444,
              "Especial de curación — sanadora sakura que pica y protege. " + _heal_txt(1),
              category=CardCategory.SPECIAL, ability=Ability.HEALER, element=Element.NATURE,
              heal_amount=HEALER_HEAL_AMOUNT),
]


def build_catalog() -> list[Card]:
    return list(BASE_CARDS) + list(SPECIAL_CARDS)


def validate_catalog(cards: list[Card], check_files: bool = True) -> None:
    """Unicidad total — falla en voz alta si alguien rompe el catálogo: 16 base (coste 1, una por
    combinación ATK/DEF) + 7 especiales (coste 2, con sus stats y habilidades fijadas)."""
    specials = [c for c in cards if c.category == CardCategory.SPECIAL]
    cards = [c for c in cards if c.category != CardCategory.SPECIAL]
    assert sorted((c.atk, c.defense) for c in specials if c.ability == Ability.TRIPARTITE_STRIKE) == sorted(
        SPECIAL_ATTACK_COMBOS), "Especiales de ataque incorrectas"
    assert sorted((c.atk, c.defense) for c in specials if c.ability == Ability.HEALER) == sorted(
        SPECIAL_GUARD_COMBOS), "Especiales de curación incorrectas"
    assert len(specials) == len(SPECIAL_ATTACK_COMBOS) + len(SPECIAL_GUARD_COMBOS)
    for c in specials:
        assert c.cost_energy == 2, f"{c.id}: las especiales cuestan 2"
    for c in cards:
        assert c.cost_energy == 1, f"{c.id}: las cartas base cuestan 1"
    all_cards = cards + specials
    assert len({c.id for c in all_cards}) == len(all_cards), "Ids de carta repetidos (base/especial)"
    assert len({c.image_path for c in all_cards}) == len(all_cards), "Dos cartas comparten imagen"
    assert len({c.nft_token_id for c in all_cards if c.nft_token_id is not None}) == len(
        [c for c in all_cards if c.nft_token_id is not None]), "Dos cartas usan el mismo zorro NFT"
    for c in specials:
        assert c.image_path and (not check_files or (PVP_ROOT / c.image_path).exists()), f"{c.id}: sin imagen"
    combos = [(c.atk, c.defense) for c in cards]
    assert len(combos) == len(set(combos)), f"Combinaciones ATK/DEF repetidas: {sorted(combos)}"
    assert sorted(combos) == sorted(REQUIRED_BASE_COMBOS), (
        f"Combinaciones incorrectas: faltan {set(REQUIRED_BASE_COMBOS) - set(combos)}, "
        f"sobran {set(combos) - set(REQUIRED_BASE_COMBOS)}"
    )
    ids = [c.id for c in cards]
    assert len(ids) == len(set(ids)), "Ids de carta repetidos"
    images = [c.image_path for c in cards]
    assert len(images) == len(set(images)), "Dos cartas comparten imagen"
    nft_ids = [c.nft_token_id for c in cards if c.nft_token_id is not None]
    assert len(nft_ids) == len(set(nft_ids)), "Dos cartas usan el mismo zorro NFT"
    for c in cards:
        assert c.category == CardCategory.STANDARD and c.ability == Ability.NONE, f"{c.id}: sin especiales"
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
    Ability.TRIPARTITE_STRIKE: ((90, 10, 10), (255, 90, 40)),
    Ability.HEALER: ((90, 20, 80), (255, 130, 210)),
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
        label = {
            Ability.LIFESTEAL: "ROBO DE VIDA", Ability.COMBO_STRIKE: "COMBO SIMULTÁNEO",
            Ability.TRIPARTITE_STRIKE: (
                f"COMBO TRIPARTITO {card.atk * ZONE_HIT_DAMAGE + TRIPARTITE_COMBO_BONUS}"
            ),
            Ability.HEALER: f"CURACIÓN +{HEALER_HEAL_AMOUNT} VIDA",
        }.get(card.ability, "ESPECIAL")
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
    # Coste de energía (especiales: 2) en una gema azul arriba a la izquierda.
    if card.category == CardCategory.SPECIAL:
        d.ellipse([x0 + 6, 66, x0 + 62, 122], fill=(20, 60, 160), outline=gold, width=4)
        _text_center(d, (x0 + 34, 94), str(card.cost_energy), 30, fill=(220, 240, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def normalize_card_art(src: Path, out_path: Path, size: int = 500) -> None:
    """Copia de un arte de CARDS con el fondo negro opaco convertido en
    transparente (relleno desde las 4 esquinas, así no se tocan los negros
    de dentro del dibujo) y escalada a size x size como el resto de artes."""
    from PIL import Image, ImageDraw

    img = Image.open(src).convert("RGBA")
    w, h = img.size
    if img.getpixel((0, 0))[3] != 0:  # fondo opaco: vaciarlo desde las esquinas
        marker = (255, 0, 255, 0)
        for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            if img.getpixel(corner)[3] != 0 and sum(img.getpixel(corner)[:3]) < 90:
                ImageDraw.floodfill(img, corner, marker, thresh=60)
        px = img.load()
        for y in range(h):
            for x in range(w):
                if px[x, y] == marker:
                    px[x, y] = (0, 0, 0, 0)
    img = img.resize((size, size), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def generate_images(cards: list[Card]) -> None:
    for card in cards:
        if card.nft_token_id is not None:
            generate_nft_card_image(card, GENERATED_DIR / f"{card.id}.png")
    normalize_card_art(PVP_ROOT / RAGING_EARTHQUAKE_SRC, PVP_ROOT / RAGING_EARTHQUAKE_IMG)


def main() -> None:
    cards = build_catalog()
    validate_catalog(cards, check_files=False)
    generate_images(cards)
    validate_catalog(cards, check_files=True)

    print(f"Cartas: {len(cards)} (esperado {len(REQUIRED_BASE_COMBOS)} base + {len(SPECIAL_CARDS)} especiales)")

    with open(DATABASE_PATH, "w", encoding="utf-8") as f:
        json.dump([c.to_dict() for c in cards], f, indent=2, ensure_ascii=False)
    print(f"Catálogo guardado en {DATABASE_PATH} ({len(cards)} cartas totales)")


if __name__ == "__main__":
    main()
