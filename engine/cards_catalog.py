"""
Generador del catálogo de cartas: construye las 27 cartas del pool
competitivo (sección 5.2 de PVP_Rules) + 2 cartas "Legendary" fuera de pool,
mezclando los 18 artes ya terminados en ../CARDS (recortados a PNG
transparente) con placeholders generados para los huecos que todavía no
tienen arte final.

IMPORTANTE: la carpeta ../CARDS/CARDS_NO_USE guarda versiones antiguas /
descartadas de estos mismos artes — este módulo nunca debe referenciar nada
de ahí dentro, solo archivos directamente en la raíz de ../CARDS.

Inventario de arte real encontrado en la carpeta PVP (11/10/2026):
- 6 cartas "KURAMARUN*-removebg-preview" (bando del jugador, Cyber/Celestial
  Kitsune Operative).
- 6 cartas "ENEMIGOS*-removebg-preview" (bando Talon Zhen: Raging Earthquake,
  Shadow Claw, Flora-Fury, Lava Rage, Frost Rage, Kappa).
- 6 artes nuevos de criaturas/pociones Talon Zhen: Namasu: Abyssal Guardian
  (reemplaza al viejo ENEMIGOS6, ahora 2/2), Bakeneko: Phantom Paws (1/1),
  Yamata-no-Orochi (3/0), e Inari: Frost Spirit / Yuki-Onna: Frozen Empress /
  Kyūbi: Spirit of Nine Flames (las 3 nuevas cartas de Poción, 0/0 con
  Ability.HEAL_POTION).
Cada una ya trae nombre y stats ATK/DEF pintados en el propio arte (leídos a
mano de la imagen). De esas 18: 2 no encajan en ningún hueco de rareza fijo
del documento (KURAMARUN2 "Celestial Kitsune Operative" 3/3 y KURAMARUN6
"Pyre & Cosmos: Kitsune Celestial" 3/1) -> se catalogan como LEGENDARY fuera
del pool de 27. Las otras 16 se reparten entre los huecos Raros y Estándar
cuyo ATK/DEF coincide (o cuyo rol — potion/ataque puro/híbrida — coincide,
aunque el número exacto sea una "variante de atributos"); el resto de huecos
(11) se cubre con placeholders balanceados (ver generate_placeholder_image).
El documento permite explícitamente "variantes estéticas y de atributos"
dentro de cada bloque, así que no hace falta que cada placeholder matchee un
hueco numerado 1:1.

Ejecutar directamente (`python cards_catalog.py`) regenera:
- cards_database.json (las 27 + 2 cartas, serializadas)
- assets/placeholders/<id>.png (una imagen generada por cada placeholder)
Las cartas con arte real apuntan a image_path relativo a la raíz del
proyecto PVP (ver PVP_ROOT en blockchain_connector.py / pvp_dashboard.py).
"""

from __future__ import annotations

from pathlib import Path

from card_schema import Ability, Card, CardCategory, Element, Faction

ENGINE_DIR = Path(__file__).resolve().parent
PVP_ROOT = ENGINE_DIR.parent
PLACEHOLDER_DIR = ENGINE_DIR / "assets" / "placeholders"
DATABASE_PATH = ENGINE_DIR / "cards_database.json"

FACTION_COLORS = {
    Faction.KURAMARUN: ("#123a63", "#2fb3d9"),
    Faction.TALON_ZHEN: ("#5a1414", "#e0622f"),
    Faction.NEUTRAL: ("#2d1f4d", "#b48cf0"),
}


# --------------------------------------------------------------------------- #
# 1. Cartas con arte real ya terminado (../CARDS)
# --------------------------------------------------------------------------- #

REAL_ART_CARDS: list[Card] = [
    # --- Bando Kuramarun --------------------------------------------------
    Card(
        id="kr_std_atk_mod_1",
        name="Cyber-Kitsune Operative",
        faction=Faction.KURAMARUN,
        category=CardCategory.STANDARD,
        atk=2, defense=0,
        element=Element.ARCANE,
        description="Ataque Moderado — operativa kitsune cibernética.",
        image_path="CARDS/KURAMARUN1-removebg-preview.png",
    ),
    Card(
        id="kr_leg_advanced_1",
        name="Celestial Kitsune Operative",
        faction=Faction.KURAMARUN,
        category=CardCategory.LEGENDARY,
        atk=3, defense=3,
        element=Element.ARCANE,
        description="Arte firma fuera del pool de 27 — potencia simétrica máxima.",
        image_path="CARDS/KURAMARUN2-removebg-preview.png",
    ),
    Card(
        id="kr_std_def_light_1",
        name="Cyber-Kitsune Operative (Guardian)",
        faction=Faction.KURAMARUN,
        category=CardCategory.STANDARD,
        atk=0, defense=2,
        element=Element.ARCANE,
        description="Defensa Ligera — variante de guardia de la operativa cibernética.",
        image_path="CARDS/KURAMARUN3-removebg-preview.png",
    ),
    Card(
        id="kr_std_hybrid_def_1",
        name="Celestial Kitsune Operative (Flux)",
        faction=Faction.KURAMARUN,
        category=CardCategory.STANDARD,
        atk=1, defense=3,
        element=Element.ARCANE,
        description="Híbrida Defensiva (variante de atributos: 1/3) — canalizadora de flujo.",
        image_path="CARDS/KURAMARUN4-removebg-preview.png",
    ),
    Card(
        id="kr_rare_intermediate_1",
        name="Celestial Kitsune Operative (Duelist)",
        faction=Faction.KURAMARUN,
        category=CardCategory.RARE,
        atk=2, defense=2,
        element=Element.ARCANE,
        description="Ataque/Defensa Intermedio — duelista equilibrada.",
        image_path="CARDS/KURAMARUN5-removebg-preview.png",
    ),
    Card(
        id="kr_leg_pyre_cosmos_1",
        name="Pyre & Cosmos: Kitsune Celestial",
        faction=Faction.KURAMARUN,
        category=CardCategory.LEGENDARY,
        atk=3, defense=1,
        element=Element.FIRE,
        description="Arte firma fuera del pool de 27 — invocadora de fuego y cosmos.",
        image_path="CARDS/KURAMARUN6-removebg-preview.png",
    ),
    # --- Bando Talon Zhen (enemigos) ---------------------------------------
    Card(
        id="tz_std_def_light_2",
        name="Raging Earthquake",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.STANDARD,
        atk=0, defense=2,
        element=Element.EARTH,
        subtitle="(Talon Zhen)",
        description="Defensa Ligera — erizo de tierra furioso.",
        image_path="CARDS/ENEMIGOS1-removebg-preview.png",
    ),
    Card(
        id="tz_std_atk_pure_1",
        name="Shadow Claw: Aerial Predator",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.STANDARD,
        atk=3, defense=0,
        element=Element.AIR,
        subtitle="(Talon Zhen)",
        description="Ataque Puro — depredador aéreo de las sombras.",
        image_path="CARDS/ENEMIGOS2-removebg-preview.png",
    ),
    Card(
        id="tz_std_def_min_1",
        name="Flora-Fury: Terrasprout",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.STANDARD,
        atk=0, defense=1,
        element=Element.NATURE,
        subtitle="(Talon Zhen)",
        description="Defensa Pura Mínima — brote furioso recién germinado.",
        image_path="CARDS/ENEMIGOS3-removebg-preview.png",
    ),
    Card(
        id="tz_std_def_shield_1",
        name="Lava Rage: Volcanic Striker",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.STANDARD,
        atk=0, defense=3,
        element=Element.FIRE,
        subtitle="(Talon Zhen)",
        description="Defensa Pura / Escudo — golpeador volcánico.",
        image_path="CARDS/ENEMIGOS4-removebg-preview.png",
    ),
    Card(
        id="tz_std_hybrid_def_2",
        name="Frost Rage: Glacial Stalwart",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.STANDARD,
        atk=0, defense=2,
        element=Element.ICE,
        subtitle="(Talon Zhen)",
        description="Híbrida Defensiva (variante de atributos: 0/2) — baluarte glacial.",
        image_path="CARDS/ENEMIGOS5-removebg-preview.png",
    ),
    Card(
        id="tz_std_hybrid_def_3",
        name="Namasu: Abyssal Guardian",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.STANDARD,
        atk=2, defense=2,
        element=Element.WATER,
        subtitle="(Talon Zhen)",
        description="Híbrida (variante de atributos: 2/2) — guardián abisal de escamas afiladas.",
        image_path="CARDS/Namasu-removedg-removebg-preview.png",
    ),
    Card(
        id="tz_rare_advanced_1",
        name="Kappa: River Demon",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.RARE,
        atk=3, defense=3,
        element=Element.WATER,
        subtitle="(Talon Zhen)",
        description="Ataque/Defensa Avanzado — demonio de río, potencia simétrica máxima.",
        image_path="CARDS/ENEMIGOS7-removebg-preview.png",
    ),
    # --- Nuevas criaturas Talon Zhen (arte añadido 11/10/2026) -------------
    Card(
        id="ph_std_atk_pure_2",
        name="Yamata-no-Orochi",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.STANDARD,
        atk=3, defense=0,
        element=Element.FIRE,
        subtitle="(Talon Zhen)",
        description="Ataque Puro Alternativo — serpiente de ocho cabezas envuelta en fuego.",
        image_path="CARDS/descarga.png",
    ),
    Card(
        id="ph_std_hybrid_off_1",
        name="Bakeneko: Phantom Paws",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.STANDARD,
        atk=1, defense=1,
        element=Element.SHADOW,
        subtitle="(Talon Zhen)",
        description="Híbrida Ligera (variante de atributos: 1/1) — gato fantasma escurridizo entre sombras.",
        image_path="CARDS/776ba0e4-03aa-49d1-a3a7-515de32713de-removebg-preview.png",
    ),
    Card(
        id="ph_rare_potion_minor",
        name="Inari: Frost Spirit",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.RARE,
        atk=0, defense=0,
        ability=Ability.HEAL_POTION,
        element=Element.ICE,
        subtitle="(Talon Zhen)",
        description="Poción Curativa Menor — el espíritu zorro de escarcha bloquea la zona y restaura vitalidad básica.",
        heal_amount=2,
        image_path="CARDS/9a7bf3c9-adcb-465b-9a75-ebd6fcb65f7a-removebg-preview.png",
    ),
    Card(
        id="ph_rare_potion_moderate",
        name="Yuki-Onna: Frozen Empress",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.RARE,
        atk=0, defense=0,
        ability=Ability.HEAL_POTION,
        element=Element.ICE,
        subtitle="(Talon Zhen)",
        description="Poción Curativa Moderada — la emperatriz helada congela el golpe y lo convierte en vida.",
        heal_amount=4,
        image_path="CARDS/descargaaaa.png",
    ),
    Card(
        id="ph_rare_potion_major",
        name="Kyūbi: Spirit of Nine Flames",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.RARE,
        atk=0, defense=0,
        ability=Ability.HEAL_POTION,
        element=Element.FIRE,
        subtitle="(Talon Zhen)",
        description="Poción Curativa Mayor — el espíritu de las nueve llamas bloquea el daño y maximiza la curación.",
        heal_amount=6,
        image_path="CARDS/KURAMARUN7.png",
    ),
    # --- Arte añadido 11/10/2026 (segunda tanda) ---------------------------
    Card(
        id="ph_std_atk_mod_2",
        name="Rookie Striker",
        faction=Faction.KURAMARUN,
        category=CardCategory.STANDARD,
        atk=2, defense=0,
        element=Element.AIR,
        description="Ataque Moderado Alternativo — joven guerrero alado, veloz con la espada.",
        image_path="CARDS/rokiestikeer.png",
    ),
    Card(
        id="ph_std_atk_light_1",
        name="Swift Fang",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.STANDARD,
        atk=1, defense=0,
        element=Element.ARCANE,
        description="Ataque Ligero — dragón celeste, golpe rápido de bajo coste.",
        image_path="CARDS/swiftfang.png",
    ),
    Card(
        id="ph_std_atk_light_2",
        name="Swift Fang Alt",
        faction=Faction.TALON_ZHEN,
        category=CardCategory.STANDARD,
        atk=1, defense=0,
        element=Element.FIRE,
        description="Ataque Ligero Alternativo — variante carmesí del dragón veloz.",
        image_path="CARDS/suiftfanngalt.png",
    ),
    Card(
        id="ph_std_def_min_2",
        name="Paper Guard",
        faction=Faction.KURAMARUN,
        category=CardCategory.STANDARD,
        atk=0, defense=1,
        element=Element.ARCANE,
        description="Defensa Pura Mínima Alternativa — centinela de papeles sagrados.",
        image_path="CARDS/paperguard.png",
    ),
    Card(
        id="ph_std_hybrid_off_2",
        name="Twinblade Adept Alt",
        faction=Faction.KURAMARUN,
        category=CardCategory.STANDARD,
        atk=2, defense=1,
        element=Element.WATER,
        description="Híbrida Ofensiva Secundaria — koi samurái, doble espada entre sombras.",
        image_path="CARDS/twinblade.png",
    ),
]


# --------------------------------------------------------------------------- #
# 2. Huecos del pool de 27 que todavía no tienen arte -> placeholders
# --------------------------------------------------------------------------- #
# (id, name, faction, category, atk, defense, ability, element, heal, counter_dmg)
PLACEHOLDER_SPECS: list[tuple] = [
    # Estándar — Ataque: el Ataque Puro ya tiene 2 artes reales (ENEMIGOS2 y
    # Yamata-no-Orochi), y moderado/ligero x2 también (Rookie Striker, Swift
    # Fang, Swift Fang Alt) -> este grupo ya no tiene huecos sin arte.
    # Estándar — Defensa (falta 1 escudo pura 0/3; la mínima 0/1 ya tiene arte: Paper Guard)
    ("ph_std_def_shield_2", "Iron Carapace", Faction.TALON_ZHEN, CardCategory.STANDARD, 0, 3, Ability.NONE, Element.NONE, 0, 0),
    # Estándar — Híbrida Ofensiva (Bakeneko y Twinblade Adept Alt ya cubren 2; queda esta)
    ("ph_std_hybrid_off_3", "Twinblade Tactician", Faction.TALON_ZHEN, CardCategory.STANDARD, 2, 1, Ability.NONE, Element.NONE, 0, 0),
    # Raras/Especiales — las 3 Pociones ya tienen arte real (Inari/Yuki-Onna/Kyūbi);
    # quedan Perforantes (2) y Contraataque (2)
    ("ph_rare_piercing_1", "Rompedefensas Directo", Faction.TALON_ZHEN, CardCategory.RARE, 2, 0, Ability.PIERCING, Element.SHADOW, 0, 0),
    ("ph_rare_piercing_2", "Ataque Perforante Táctico", Faction.KURAMARUN, CardCategory.RARE, 3, 0, Ability.PIERCING, Element.ARCANE, 0, 0),
    ("ph_rare_counter_1", "Contraataque Estándar", Faction.KURAMARUN, CardCategory.RARE, 1, 1, Ability.COUNTER, Element.ARCANE, 0, 2),
    ("ph_rare_counter_2", "Contraataque de Repsilo Avanzado", Faction.TALON_ZHEN, CardCategory.RARE, 1, 1, Ability.COUNTER, Element.SHADOW, 0, 3),
]


def build_catalog() -> list[Card]:
    cards = list(REAL_ART_CARDS)
    for card_id, name, faction, category, atk, defense, ability, element, heal, counter in PLACEHOLDER_SPECS:
        cards.append(
            Card(
                id=card_id,
                name=name,
                faction=faction,
                category=category,
                atk=atk,
                defense=defense,
                ability=ability,
                element=element,
                heal_amount=heal,
                counter_damage=counter,
                description="Carta placeholder — pendiente de arte final.",
                is_placeholder=True,
                image_path=f"engine/assets/placeholders/{card_id}.png",
            )
        )
    return cards


def generate_placeholder_image(card: Card, out_path: Path) -> None:
    """Dibuja una imagen de carta simple y balanceada (marco por bando, nombre,
    ATK/DEF, icono de habilidad) para que el dashboard tenga algo que mostrar
    mientras no existe el arte final. Requiere Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    bg_color, accent = FACTION_COLORS.get(card.faction, FACTION_COLORS[Faction.NEUTRAL])
    size = 512
    img = Image.new("RGB", (size, size), bg_color)
    draw = ImageDraw.Draw(img)

    border = 10
    draw.rectangle([border, border, size - border, size - border], outline=accent, width=6)

    def font(sz: int):
        try:
            return ImageFont.truetype("arialbd.ttf", sz)
        except OSError:
            return ImageFont.load_default()

    def centered_text(text: str, y: int, sz: int, color: str = "white") -> None:
        f = font(sz)
        bbox = draw.textbbox((0, 0), text, font=f)
        w = bbox[2] - bbox[0]
        draw.text(((size - w) / 2, y), text, font=f, fill=color)

    centered_text(card.name, 40, 26)
    if card.subtitle:
        centered_text(card.subtitle, 75, 16, color="#cccccc")

    # Silueta simple del icono de habilidad / bando en el centro.
    cx, cy, r = size // 2, size // 2 - 20, 90
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=accent, width=8)
    icon_text = {
        Ability.HEAL_POTION: "+HP",
        Ability.PIERCING: "PIERCE",
        Ability.COUNTER: "COUNTER",
    }.get(card.ability, card.faction.value.upper())
    centered_text(icon_text, cy - 14, 24)

    centered_text(f"{card.atk} ATK", size - 150, 34)
    centered_text(f"{card.defense} DEF", size - 100, 34)
    centered_text(f"Coste: {card.cost_energy}", size - 55, 18, color="#dddddd")
    centered_text("PLACEHOLDER", size - 30, 14, color="#999999")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def main() -> None:
    cards = build_catalog()

    PLACEHOLDER_DIR.mkdir(parents=True, exist_ok=True)
    for card in cards:
        if card.is_placeholder:
            generate_placeholder_image(card, ENGINE_DIR / "assets" / "placeholders" / f"{card.id}.png")

    standard = [c for c in cards if c.category == CardCategory.STANDARD]
    rare = [c for c in cards if c.category == CardCategory.RARE]
    legendary = [c for c in cards if c.category == CardCategory.LEGENDARY]
    print(f"Estándar: {len(standard)} (esperado 18)")
    print(f"Raras: {len(rare)} (esperado 9)")
    print(f"Legendarias (fuera de pool): {len(legendary)}")
    assert len(standard) == 18, "El pool estándar debe tener exactamente 18 cartas"
    assert len(rare) == 9, "El pool raro debe tener exactamente 9 cartas"

    import json

    with open(DATABASE_PATH, "w", encoding="utf-8") as f:
        json.dump([c.to_dict() for c in cards], f, indent=2, ensure_ascii=False)
    print(f"Catálogo guardado en {DATABASE_PATH} ({len(cards)} cartas totales)")


if __name__ == "__main__":
    main()
