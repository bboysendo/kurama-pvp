"""
Simulador/panel visual del PvP de Kurama Run (Streamlit) — para probar el
motor de combate (pvp_engine.py) y la integración blockchain
(blockchain_connector.py) en local, sin tocar el proyecto principal ni la
BSC real.

Ejecución:
    cd PVP/engine
    pip install -r ../requirements.txt
    streamlit run pvp_dashboard.py

Modos:
- Vs. Bot: controlas al Jugador A, una IA mínima juega al Jugador B.
- Hotseat (2 jugadores locales): controlas A y B por turnos en el mismo
  panel — útil para probar combos y el motor sin necesitar dos sesiones.
La wallet es siempre SIMULADA por defecto (BlockchainConnector(simulate=True))
para poder jugar sin RPC ni fondos reales; hay un checkbox para intentar la
BSC real si el equipo quiere probarlo con web3 instalado.
"""

from __future__ import annotations

import html
import json
import random
import sys
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_ENGINE_DIR = Path(__file__).resolve().parent
_PVP_ROOT = _ENGINE_DIR.parent
_MARKETPLACE_DIR = _PVP_ROOT / "MARKETPLACE"
if str(_PVP_ROOT) not in sys.path:
    sys.path.insert(0, str(_PVP_ROOT))
if str(_MARKETPLACE_DIR) not in sys.path:
    sys.path.insert(0, str(_MARKETPLACE_DIR))

import kurama_theme  # noqa: E402
import marketplace_app  # noqa: E402  (MARKETPLACE/ — ver render_marketplace_panel() más abajo)

from blockchain_connector import (
    DECK_STAT_BUFF_PCT,
    BlockchainConnector,
    apply_nft_buffs_to_deck,
    compute_base_hp,
    compute_match_economy,
    derive_placeholder_rarity,
)
from card_schema import (
    CRITICAL_COMBO_DAMAGE,
    DECK_SIZE,
    ENERGY_PER_TURN,
    HAND_SIZE,
    MAX_ENERGY_SPEND_PER_TURN,
    STARTING_ENERGY,
    TRIPARTITE_COMBO_BONUS,
    TURN_TIMER_SECONDS,
    Card,
    CardCategory,
    NFTRarity,
)
from pvp_engine import (
    EngineError,
    MatchStatus,
    Zone,
    auto_skip_turn,
    bot_choose_plays,
    build_deck_from_pool,
    end_turn,
    get_match_state,
    play_card,
    start_match,
)

ENGINE_DIR = _ENGINE_DIR
PVP_ROOT = _PVP_ROOT
DATABASE_PATH = ENGINE_DIR / "cards_database.json"

# Única fuente de verdad para las 4 secciones del panel — la navegación vive
# exclusivamente en el menú superior (st.popover "☰ MENÚ DE NAVEGACIÓN", ver
# más abajo), que lee y escribe st.session_state["panel_mode"]. No hay barra
# lateral: se eliminó por completo.
PANEL_OPTIONS = ["⚔️ Modo Batalla", "🎓 Tutorial Interactivo", "📖 Catálogo de Cartas", "🛒 Marketplace NFT ($KUR)"]


@st.cache_data
def load_catalog() -> list[dict]:
    with open(DATABASE_PATH, encoding="utf-8") as f:
        return json.load(f)


def card_image_path(card_dict: dict) -> Path | None:
    rel = card_dict.get("image_path")
    if not rel:
        return None
    p = PVP_ROOT / rel
    return p if p.exists() else None


def init_state() -> None:
    if "connector" not in st.session_state:
        st.session_state.connector = BlockchainConnector(simulate=True)
        st.session_state.connector.simulated_backend.seed_wallet(
            "0xTESTWALLET000000000000000000000000AAAA", token_ids=[7, 42, 1337], kur_balance=150.0
        )
    if "wallet_address" not in st.session_state:
        st.session_state.wallet_address = "0xTESTWALLET000000000000000000000000AAAA"
    if "match" not in st.session_state:
        st.session_state.match = None
    if "vs_bot" not in st.session_state:
        st.session_state.vs_bot = True
    if "tutorial_step" not in st.session_state:
        st.session_state.tutorial_step = 0
    # Fuente de verdad para Modo/Oponente — tanto la barra de configuración
    # contextual de arriba como el "🏆 Modo de partida" de la barra lateral
    # leen y escriben estas mismas claves (mismo key= en el widget de la
    # barra lateral; la de arriba son botones que las fijan y hacen
    # st.rerun() antes de que ese widget se instancie, igual que panel_mode).
    if "match_mode" not in st.session_state:
        st.session_state.match_mode = "free"
    if "opponent_choice" not in st.session_state:
        st.session_state.opponent_choice = "Bot" if st.session_state.vs_bot else "Hotseat (2 jugadores locales)"


def rarity_label(rarity: NFTRarity) -> str:
    return {
        NFTRarity.NONE: "Sin NFT",
        NFTRarity.COMMON: "Común",
        NFTRarity.RARE: "Raro",
        NFTRarity.EPIC: "Épico",
        NFTRarity.LEGENDARY: "Legendario",
    }[rarity]


def resolve_equipped_rarity(connector: BlockchainConnector, wallet_address: str) -> NFTRarity:
    """Rareza que decide HP base y buff de mazo para la próxima partida: la
    del NFT elegido en render_nft_equip_section si sigue en la wallet; si no
    hay ninguno equipado (o cambió de wallet mientras tanto), cae a la mejor
    rareza disponible — mismo comportamiento que antes de tener selector."""
    owned = connector.owned_token_ids(wallet_address)
    equipped_id = st.session_state.get("equipped_token_id")
    if equipped_id in owned:
        return derive_placeholder_rarity(equipped_id)
    return connector.best_rarity_for_wallet(wallet_address)


# --------------------------------------------------------------------------- #
# Galería / Catálogo de cartas
# --------------------------------------------------------------------------- #
CATEGORY_LABELS = {"standard": "Común", "rare": "Rara", "legendary": "Legendaria"}


def card_type_label(card_dict: dict) -> str:
    """Tipo de carta para filtrar la galería: deriva de su habilidad especial
    (Poción/Perforante/Contraataque) o, si no tiene, de si pega, protege o
    ambas cosas (Ataque/Defensa/Híbrida)."""
    ability = card_dict.get("ability", "none")
    if ability == "heal_potion":
        return "Poción"
    if ability == "piercing":
        return "Perforante"
    if ability == "counter":
        return "Contraataque"
    atk, defense = card_dict["atk"], card_dict["defense"]
    if atk > 0 and defense > 0:
        return "Híbrida"
    if atk > 0:
        return "Ataque"
    if defense > 0:
        return "Defensa"
    return "Especial"


def card_effect_text(card_dict: dict) -> str:
    """Efecto exacto en combate de una carta, según su habilidad especial
    (misma mecánica que resuelve pvp_engine.py `_resolve_direction` /
    `_collect_counter_hits`) o, si no tiene, según si ataca y/o defiende."""
    ability = card_dict.get("ability", "none")
    if ability == "heal_potion":
        heal = card_dict.get("heal_amount") or 0
        return (
            f"🧪 Bloquea la zona elegida: si el rival te ataca ahí, conviertes "
            f"ese daño en HP (mínimo {heal})."
        )
    if ability == "piercing":
        return "🗡️ Ignora la defensa rival de la zona elegida: tu ataque pasa directo, sin bloqueo."
    if ability == "counter":
        dmg = card_dict.get("counter_damage") or 0
        return f"↩️ Si el rival no ataca esa zona este turno, le devuelves {dmg} de daño."
    atk, defense = card_dict["atk"], card_dict["defense"]
    if atk > 0 and defense > 0:
        return "⚔️🛡️ Ataca y defiende la zona elegida a la vez."
    if atk > 0:
        return "⚔️ Inflige daño directo a la zona elegida (daño real = tu ATK − DEF rival)."
    if defense > 0:
        return "🛡️ Defiende la zona elegida, bloqueando el daño entrante."
    return "✨ Efecto especial — ver descripción."


def render_gallery(catalog_dicts: list[dict]) -> None:
    st.header("📖 Catálogo de Cartas")
    st.caption(
        f"Las {len(catalog_dicts)} cartas registradas en el juego — arte final y placeholders "
        "incluidos. Filtra por rareza o por tipo para revisarlas todas de un vistazo."
    )

    all_types = sorted({card_type_label(c) for c in catalog_dicts})

    f1, f2 = st.columns([1, 2])
    with f1:
        rarity_filter = st.radio(
            "Rareza", ["Todas", "Común", "Rara", "Legendaria"], key="gallery_rarity_filter"
        )
    with f2:
        type_filter = st.multiselect("Tipo", all_types, default=all_types, key="gallery_type_filter")

    filtered = catalog_dicts
    if rarity_filter != "Todas":
        wanted_category = next(k for k, v in CATEGORY_LABELS.items() if v == rarity_filter)
        filtered = [c for c in filtered if c["category"] == wanted_category]
    filtered = [c for c in filtered if card_type_label(c) in type_filter]

    st.caption(f"Mostrando {len(filtered)} de {len(catalog_dicts)} cartas.")
    if not filtered:
        st.info("Ninguna carta coincide con los filtros seleccionados.")
        return

    st.divider()
    with st.container(key="gallery_grid"):
        cols = st.columns(4)
        for i, card_dict in enumerate(filtered):
            with cols[i % 4]:
                img = card_image_path(card_dict)
                if img:
                    st.image(str(img), width="stretch")
                else:
                    st.caption("🖼️ (sin imagen)")
                st.markdown(f"<div class='card-name'>{html.escape(card_dict['name'])}</div>", unsafe_allow_html=True)
                st.caption(f"⚡ Coste {card_dict['cost_energy']} · {card_dict['atk']} ATK / {card_dict['defense']} DEF")
                st.caption(f"🏷️ {CATEGORY_LABELS.get(card_dict['category'], card_dict['category'])} · {card_type_label(card_dict)}")
                ability_note = ""
                if card_dict.get("ability") == "heal_potion" and card_dict.get("heal_amount"):
                    ability_note = f"💚 Cura mínimo {card_dict['heal_amount']} HP"
                elif card_dict.get("ability") == "counter" and card_dict.get("counter_damage"):
                    ability_note = f"↩️ Devuelve {card_dict['counter_damage']} de daño"
                st.markdown(f"<div class='gallery-ability'>{ability_note or '&nbsp;'}</div>", unsafe_allow_html=True)
                st.markdown(
                    f"<div class='gallery-desc'>{html.escape(card_dict.get('description') or '')}</div>",
                    unsafe_allow_html=True,
                )
                if card_dict.get("is_placeholder"):
                    st.caption("🧩 Arte pendiente (placeholder)")
                st.divider()


# --------------------------------------------------------------------------- #
# Tutorial interactivo
# --------------------------------------------------------------------------- #
# Guion de la "Demostración Paso a Paso": una partida de ejemplo ya escrita a
# mano (no usa el motor en vivo, para que sea siempre la misma secuencia
# didáctica) pero con los daños calculados exactamente como los calcularía
# pvp_engine.py, para que lo que se enseña aquí sea 100% lo que pasa jugando
# de verdad. HP inicial de ejemplo: Tú 50, Rival 50.
TUTORIAL_STEPS = [
    {
        "title": "Así arranca la partida",
        "actor": None,
        "card_id": None,
        "zone": None,
        "text": (
            "Tú y el rival empezáis con la misma vida (depende de vuestra rareza de NFT — "
            "aquí usamos 50 HP de ejemplo) y 3 cartas en la mano. Cada turno podéis gastar "
            "hasta 2 puntos de energía jugando cartas, eligiendo en qué **zona** (Cabeza, "
            "Tronco o Piernas) actúa cada una."
        ),
        "hp_a": 50, "hp_b": 50,
        "badge": None,
    },
    {
        "title": "Turno 1 — Ataque básico",
        "actor": "tú",
        "card_id": "tz_std_atk_pure_1",
        "zone": "Cabeza",
        "text": (
            "Juegas **Shadow Claw: Aerial Predator** (3 ATK / 0 DEF) apuntando a la **Cabeza** "
            "del rival. Como él no protegió esa zona este turno, el daño pasa entero: "
            "**Daño = 3 ATK − 0 DEF = 3**."
        ),
        "hp_a": 50, "hp_b": 47,
        "badge": "⚔️ Ataque normal",
    },
    {
        "title": "Turno 2 — El rival se cura con una poción",
        "actor": "rival",
        "card_id": "ph_rare_potion_minor",
        "zone": "Piernas",
        "text": (
            "Atacas las **Piernas**, pero el rival había jugado una **Poción Curativa Menor** "
            "ahí mismo. Las pociones **bloquean el daño de esa zona por completo** y "
            "convierten esa cantidad en vida: en vez de recibir daño, el rival **cura**. "
            "Esta zona NO cuenta como \"dañada\" para tu combo — quedó totalmente protegida."
        ),
        "hp_a": 50, "hp_b": 49,
        "badge": "🧪 Poción / Curación",
    },
    {
        "title": "Turno 3 — El escudo detiene el golpe",
        "actor": "rival",
        "card_id": "tz_std_def_shield_1",
        "zone": "Tronco",
        "text": (
            "El rival protege el **Tronco** con **Lava Rage: Volcanic Striker** (0 ATK / 3 DEF). "
            "Atacas ahí con una carta de 2 ATK normal: **Daño = max(0, 2 − 3) = 0**. "
            "El escudo absorbe el golpe entero — con cartas normales, contra un buen escudo no pasa nada."
        ),
        "hp_a": 50, "hp_b": 49,
        "badge": "🛡️ Defensa / Escudo",
    },
    {
        "title": "Turno 4 — Una Perforante ignora el escudo",
        "actor": "tú",
        "card_id": "ph_rare_piercing_2",
        "zone": "Tronco",
        "text": (
            "Esta vez usas **Ataque Perforante Táctico** (3 ATK, habilidad Perforante) en el "
            "**Tronco**. Las cartas perforantes **ignoran la defensa del rival por completo**, "
            "así que el escudo no sirve de nada: **Daño = 3** aunque el rival tenga DEF alta."
        ),
        "hp_a": 50, "hp_b": 46,
        "badge": "🗡️ Ataque perforante",
    },
    {
        "title": "Turno 5 — ¡Combo Tripartito!",
        "actor": "tú",
        "card_id": "ph_std_hybrid_off_3",
        "zone": "Piernas",
        "text": (
            f"Atacas las **Piernas** sin que el rival se proteja: 2 de daño normal. Pero ya habías "
            f"dañado antes la Cabeza (turno 1) y el Tronco (turno 4) — ¡al dañar también las Piernas "
            f"completas las 3 zonas en el transcurso de la partida! Eso activa el **Combo Tripartito**: "
            f"+{TRIPARTITE_COMBO_BONUS} de daño extra automático en ese mismo golpe. "
            f"**Daño total = 2 + {TRIPARTITE_COMBO_BONUS} = 5**."
        ),
        "hp_a": 50, "hp_b": 41,
        "badge": "🔥 Combo Tripartito",
    },
    {
        "title": "Turno 6 — ¡Combo Crítico!",
        "actor": "tú",
        "card_id": "ph_std_atk_pure_2",
        "zone": "Cabeza",
        "text": (
            f"Con el Combo Tripartito ya activo, **cualquier carta de Ataque Puro (3 ATK / 0 DEF)** "
            f"que juegues se convierte en un golpe crítico: **{CRITICAL_COMBO_DAMAGE} de daño fijo, "
            f"ignorando cualquier defensa o escudo**. Es el mejor momento de la partida para rematar."
        ),
        "hp_a": 50, "hp_b": 21,
        "badge": "💥 Combo Crítico",
    },
    {
        "title": "Turno 7 — El rival contraataca",
        "actor": "rival",
        "card_id": "ph_rare_counter_2",
        "zone": "Cabeza",
        "text": (
            "El rival juega **Contraataque de Repsilo Avanzado** en la **Cabeza**, apostando a que "
            "no ataques ahí. Tú atacas las Piernas en su lugar (2 de daño). Como no atacaste la "
            "Cabeza, el contraataque se activa y te devuelve 3 de daño directo — más 1 de daño "
            "normal de la propia carta, que tampoco protegiste. **Recibes 4 en total.**"
        ),
        "hp_a": 46, "hp_b": 19,
        "badge": "↩️ Contraataque",
    },
    {
        "title": "Turno 8 — ¡Victoria!",
        "actor": "tú",
        "card_id": "tz_std_atk_pure_1",
        "zone": "Tronco",
        "text": (
            f"Rematas con otro Ataque Puro — como el combo sigue activo, vuelve a ser un golpe "
            f"crítico de {CRITICAL_COMBO_DAMAGE}. El rival llega a 0 HP. **¡Ganas la partida!**"
        ),
        "hp_a": 46, "hp_b": 0,
        "badge": "🏆 Victoria",
    },
]


def render_tutorial(catalog_dicts: list[dict]) -> None:
    cards_by_id = {c["id"]: c for c in catalog_dicts}

    st.header("🎓 Tutorial Interactivo — Cómo jugar al PVP de Kurama Run")
    st.caption("Todo lo que necesitas para entender una partida, explicado paso a paso y en español.")

    tab_basics, tab_zones, tab_cards, tab_combos, tab_demo = st.tabs(
        ["📖 Conceptos Básicos", "🎯 Zonas de Impacto", "🃏 Tipos de Cartas", "⚡ Combos", "👣 Demostración Paso a Paso"]
    )

    with tab_basics:
        st.subheader("Lo esencial antes de tu primera partida")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("### ❤️ Puntos de Vida (HP)")
            st.markdown(
                "Cada jugador tiene una barra de vida que depende de si tiene un NFT y de su "
                "rareza (de 50 a 100 HP). **Cuando tu HP llega a 0, pierdes la partida.** "
                "No hay vida por zona: las 3 zonas (Cabeza, Tronco, Piernas) comparten la misma barra."
            )
        with c2:
            st.markdown("### ⚡ Energía por turno")
            st.markdown(
                f"Empiezas con **{STARTING_ENERGY} de energía** y ganas **+{ENERGY_PER_TURN} cada turno**. "
                f"Pero solo puedes **gastar hasta {MAX_ENERGY_SPEND_PER_TURN} por turno**, aunque tengas "
                f"más acumulada — así que jugar una carta de coste 2, o dos de coste 1, es tu máximo por turno."
            )
        with c3:
            st.markdown("### 🂠 El mazo")
            st.markdown(
                f"Tu mazo de combate tiene **{DECK_SIZE} cartas**. Cada turno robas **{HAND_SIZE} nuevas** "
                f"(la mano se renueva entera) y tienes **{TURN_TIMER_SECONDS} segundos** para decidir — "
                f"si el mazo se queda sin cartas, la pila de descarte se baraja y vuelve a usarse."
            )
        st.info(
            "💡 **Resumen rápido**: cada carta que juegas apunta a UNA zona. Si la carta tiene ATK, "
            "ataca esa zona del rival. Si tiene DEF, protege esa misma zona tuya. Algunas cartas "
            "tienen las dos cosas a la vez (son las *híbridas*)."
        )

    with tab_zones:
        st.subheader("Cabeza, Tronco y Piernas: elige bien dónde golpear")
        zc1, zc2, zc3 = st.columns(3)
        with zc1:
            st.markdown("## 🗣️ Cabeza")
            st.markdown("Una de las 3 zonas de impacto. Atacarla daña la Cabeza del rival; "
                        "defenderla protege la tuya contra ataques a esa misma zona.")
        with zc2:
            st.markdown("## 🫁 Tronco")
            st.markdown("La zona \"central\". Igual de válida que las otras — no hay ninguna "
                        "zona mecánicamente mejor, solo depende de lo que juegue el rival.")
        with zc3:
            st.markdown("## 🦵 Piernas")
            st.markdown("La tercera zona. Dañar las 3 zonas distintas a lo largo de la partida "
                        "es justo lo que activa el Combo Tripartito (pestaña ⚡ Combos).")
        st.markdown(
            "**¿Cómo se calcula el daño en una zona?** Si atacas una zona donde el rival NO se "
            "protegió, pasa todo tu ATK. Si SÍ se protegió con una carta de DEF en esa misma zona, "
            "se resta: `Daño real = max(0, tu ATK − su DEF en esa zona)`. Por eso elegir bien la "
            "zona (o adivinar dónde se protegerá el rival) es la clave táctica del juego."
        )

    with tab_cards:
        st.subheader("Tipos de cartas, con ejemplos reales del juego")

        def show_card(col, card_id: str, note: str = "") -> None:
            card_dict = cards_by_id.get(card_id)
            if not card_dict:
                return
            with col, st.container(key=f"tutorial_card_{card_id}"):
                img = card_image_path(card_dict)
                if img:
                    st.image(str(img), width=160)
                st.markdown(f"**{card_dict['name']}**")
                st.caption(f"{card_dict['atk']} ATK / {card_dict['defense']} DEF · Coste {card_dict['cost_energy']}")
                if note:
                    st.caption(note)

        st.markdown("#### ⚔️ Ataque Normal vs. 🗡️ Ataque Perforante")
        st.caption(
            "Ambas atacan una zona. La diferencia: la Normal se mitiga por la defensa de esa zona, "
            "la Perforante **ignora cualquier escudo** y pasa el daño completo."
        )
        c1, c2 = st.columns(2)
        show_card(c1, "tz_std_atk_pure_1", "Ataque Normal: su daño SÍ se reduce por el escudo rival.")
        show_card(c2, "ph_rare_piercing_2", "Perforante: ignora la defensa de la zona — siempre pasa entero.")

        st.markdown("#### 🛡️ Defensa / Escudo")
        st.caption("No atacan: protegen tu propia zona, restando su DEF al daño que recibas ahí este turno.")
        c3, c4 = st.columns(2)
        show_card(c3, "tz_std_def_shield_1", "Escudo pesado (0/3) — la mejor mitigación posible en una zona.")
        show_card(c4, "tz_std_def_light_2", "Escudo ligero (0/2) — mitiga menos, pero cuesta menos energía.")

        st.markdown("#### 🧪 Cartas Curativas / Pociones")
        st.caption(
            "Bloquean el daño de la zona elegida por completo y, además, convierten esa cantidad "
            "en vida para ti. Hay 3 niveles, de menor a mayor curación garantizada."
        )
        c5, c6, c7 = st.columns(3)
        show_card(c5, "ph_rare_potion_minor", "Curación mínima garantizada: 2 HP (o el daño bloqueado, el que sea mayor).")
        show_card(c6, "ph_rare_potion_moderate", "Curación mínima garantizada: 4 HP.")
        show_card(c7, "ph_rare_potion_major", "Curación mínima garantizada: 6 HP.")

    with tab_combos:
        st.subheader("Mecánicas especiales que deciden las partidas")

        with st.expander(f"🔥 Combo Tripartito (+{TRIPARTITE_COMBO_BONUS} de daño extra)", expanded=True):
            st.markdown(
                f"La primera vez que consigues dañar **las 3 zonas distintas** del rival — Cabeza, "
                f"Tronco y Piernas — en algún momento de la partida (no hace falta que sea en el "
                f"mismo turno), el golpe que completa la tercera zona suma automáticamente "
                f"**+{TRIPARTITE_COMBO_BONUS} de daño extra**. Solo se activa una vez por partida."
            )
            st.caption("Consejo: varía la zona a la que atacas en vez de insistir siempre en la misma.")

        with st.expander(f"💥 Combo Crítico ({CRITICAL_COMBO_DAMAGE} de daño fijo)"):
            st.markdown(
                f"Una vez que ya activaste el Combo Tripartito, cada carta de **Ataque Puro "
                f"(3 ATK / 0 DEF)** que juegues después inflige **{CRITICAL_COMBO_DAMAGE} de daño "
                f"directo y fijo**, ignorando escudos, perforación o cualquier otra defensa. "
                f"Es el mejor momento para usar tus cartas más fuertes."
            )

        with st.expander("↩️ Contraataques"):
            st.markdown(
                "Las cartas de Contraataque se juegan como protección en una zona, apostando a que "
                "el rival **no** vaya a atacar ahí ese turno. Si aciertas (el rival ataca otra zona), "
                "le devuelves daño directo. Si el rival SÍ ataca esa zona, el contraataque no se "
                "activa — la carta solo actúa como una defensa normal con su propio DEF."
            )

    with tab_demo:
        st.subheader("Demostración paso a paso")
        st.caption(
            "Una partida de ejemplo ya escrita, turno a turno, para ver las mecánicas en acción "
            "sin tener que jugar tú mismo. Pulsa Siguiente para avanzar."
        )

        total_steps = len(TUTORIAL_STEPS)
        step_idx = min(st.session_state.tutorial_step, total_steps - 1)
        step = TUTORIAL_STEPS[step_idx]

        st.progress((step_idx + 1) / total_steps, text=f"Paso {step_idx + 1} de {total_steps}")

        nav1, nav2, nav3 = st.columns([1, 1, 1])
        with nav1:
            if st.button("⬅️ Paso anterior", disabled=step_idx == 0, width="stretch"):
                st.session_state.tutorial_step = max(0, step_idx - 1)
                st.rerun()
        with nav2:
            if st.button("🔄 Reiniciar demo", width="stretch"):
                st.session_state.tutorial_step = 0
                st.rerun()
        with nav3:
            if st.button("Siguiente paso ➡️", disabled=step_idx == total_steps - 1, width="stretch", type="primary"):
                st.session_state.tutorial_step = min(total_steps - 1, step_idx + 1)
                st.rerun()

        st.divider()
        demo_col, hp_col = st.columns([2, 1])
        with demo_col:
            st.markdown(f"### {step['title']}")
            if step["badge"]:
                st.caption(step["badge"])
            st.markdown(step["text"])
            if step["card_id"]:
                card_dict = cards_by_id.get(step["card_id"])
                if card_dict:
                    img_col, info_col = st.columns([1, 2])
                    with img_col, st.container(key=f"tutorial_card_demo_{step_idx}"):
                        img = card_image_path(card_dict)
                        if img:
                            st.image(str(img), width=160)
                    with info_col:
                        quien = "Tú jugaste" if step["actor"] == "tú" else "El rival jugó"
                        st.markdown(f"**{quien}:** {card_dict['name']}")
                        st.caption(f"{card_dict['atk']} ATK / {card_dict['defense']} DEF · Zona: {step['zone']}")
        with hp_col:
            st.markdown("**Vida en este momento**")
            st.progress(max(0.0, step["hp_a"] / 50), text=f"Tú: {step['hp_a']} / 50 HP")
            st.progress(max(0.0, step["hp_b"] / 50), text=f"Rival: {step['hp_b']} / 50 HP")


# --------------------------------------------------------------------------- #
# Integración del Marketplace: el código vive 100% en MARKETPLACE/ (import
# arriba) — aquí solo se llama a su marketplace_app.render() y se sincroniza
# su wallet simulada con la de este panel (misma dirección, mismos NFTs,
# mismo balance $KUR) en los dos sentidos, para que "Modo Batalla" y
# "Marketplace NFT" cuenten siempre la misma historia dentro de la sesión.
# Ver "Integración modular" en MARKETPLACE/README.md.
# --------------------------------------------------------------------------- #
def render_marketplace_panel(connector: BlockchainConnector, wallet_address: str) -> None:
    mkt_wallet = marketplace_app.get_wallet()

    # Solo se tira (pull) del estado del PVP al ENTRAR a esta pestaña o al
    # cambiar de dirección — nunca en cada rerun. Si se sincronizara siempre,
    # el st.rerun() interno del modal de Comprar/Vender (marketplace_app.py)
    # cortaría la función antes de llegar al push-back de más abajo, y el
    # siguiente rerun pisaría la compra recién hecha con el estado viejo del
    # PVP. Con el guard, una vez sincronizada una dirección, la wallet del
    # Marketplace manda hasta que cambie la dirección, y cada rerun hace
    # push-back de su estado actual hacia el PVP sin perder nada por medio.
    if st.session_state.get("_mkt_synced_address") != wallet_address:
        mkt_wallet.sync_external_state(
            address=wallet_address,
            owned_token_ids=set(connector.owned_token_ids(wallet_address)),
            kur_balance=connector.kur_balance(wallet_address),
        )
        st.session_state["_mkt_synced_address"] = wallet_address

    marketplace_app.render(show_sidebar=False)

    # Marketplace -> PVP: cualquier compra/venta hecha arriba se refleja de
    # inmediato en la wallet simulada del PVP (mismo backend en memoria),
    # para que al volver a "Modo Batalla" se vea el NFT/balance nuevo.
    if connector.simulated_backend is not None:
        connector.simulated_backend.seed_wallet(
            wallet_address, sorted(mkt_wallet.owned_token_ids), mkt_wallet.kur_balance
        )


# --------------------------------------------------------------------------- #
# Selector de Modo/Oponente (y, en modo Competitivo, Apuesta en KUR): vive
# justo debajo del CTA "🔥 NUEVA PARTIDA" en la columna de acción/derecha
# (ver el layout de más abajo). Cada grupo es un st.segmented_control de
# selección única — mutuamente excluyente por construcción (nunca hay 0 ni 2
# activos a la vez dentro del mismo grupo) en vez de pares de botones
# independientes; el key coincide con la clave de session_state que ya lee
# el resto del panel (match_mode, opponent_choice), así el widget es la
# única fuente de verdad. El CSS de más abajo (st-key-battle_mode_bar) pinta
# la opción activa en naranja/rojo y las inactivas en gris apagado.
#
# segmented_control permite deseleccionar su propia opción activa (vuelve
# None) — para que el modo nunca quede "vacío" sin violar las reglas de
# Streamlit, la reposición al valor por defecto se hace en un callback
# on_change (el único momento en que es válido reescribir el key de un
# widget ya instanciado); hacerlo en el cuerpo del script después de crear
# el widget, como antes, disparaba StreamlitWidgetAlreadyInstantiatedError.
# --------------------------------------------------------------------------- #
def _reset_match_mode_if_empty() -> None:
    if st.session_state.get("match_mode") is None:
        st.session_state.match_mode = "free"


def _reset_opponent_choice_if_empty() -> None:
    if st.session_state.get("opponent_choice") is None:
        st.session_state.opponent_choice = "Bot"


def render_battle_mode_bar(connector: BlockchainConnector, wallet_address: str, pool_27: list[Card]) -> None:
    with st.container(key="battle_mode_bar"):
        st.segmented_control(
            "Modo",
            options=["free", "competitive"],
            format_func=lambda v: "🆓 Free" if v == "free" else "💰 Competitivo",
            key="match_mode",
            on_change=_reset_match_mode_if_empty,
            label_visibility="collapsed",
        )
        st.segmented_control(
            "Oponente",
            options=["Bot", "Hotseat (2 jugadores locales)"],
            format_func=lambda v: "🤖 Bot" if v == "Bot" else "👥 Hotseat",
            key="opponent_choice",
            on_change=_reset_opponent_choice_if_empty,
            label_visibility="collapsed",
        )

        if st.session_state.match_mode == "competitive":
            stake_col, economy_col = st.columns([1, 3])
            with stake_col:
                stake = st.number_input(
                    "Apuesta en KUR", min_value=0.0, value=10.0, step=1.0, key="navbar_stake"
                )
            economy = compute_match_economy(st.session_state.match_mode, stake)
            with economy_col:
                st.caption(
                    f"Victoria: +{economy.win_elo} ELO · {economy.win_kur_reward:.0f} KUR · "
                    f"Derrota: {economy.loss_elo} ELO · comisión {economy.fee_kur:.2f} KUR"
                )
        else:
            economy = compute_match_economy(st.session_state.match_mode, 0.0)
            st.caption(f"Victoria: +{economy.win_elo} ELO · {economy.win_kur_reward:.0f} KUR · Derrota: {economy.loss_elo} ELO")


# --------------------------------------------------------------------------- #
# CTA principal de la pantalla de Batalla: botón grande, ancho y llamativo
# (rojo/naranja vivo, ver CSS "st-key-new_match_cta" más abajo), arriba de
# todo en la columna de acción/derecha — justo debajo va render_battle_mode_bar
# (Modo/Oponente) en el mismo layout, ver más abajo. Vive aparte del bloque
# compacto de NFT (izquierda) para que destaque como la acción principal
# antes de empezar a jugar.
# --------------------------------------------------------------------------- #
def render_new_match_button(connector: BlockchainConnector, wallet_address: str, pool_27: list[Card]) -> None:
    with st.container(key="new_match_cta"):
        if st.button("🛡️⚔️ NUEVA PARTIDA ⚔️🛡️", key="navbar_new_match", width="stretch", type="primary"):
            rarity = resolve_equipped_rarity(connector, wallet_address)
            base_hp = compute_base_hp(rarity)
            rng = random.Random()
            deck_a = build_deck_from_pool(pool_27, rng=rng)
            deck_b = build_deck_from_pool(pool_27, rng=rng)
            deck_a = apply_nft_buffs_to_deck(deck_a, rarity)
            hp_b = compute_base_hp(NFTRarity.NONE) if st.session_state.vs_bot else base_hp
            st.session_state.match = start_match(
                deck_a, deck_b, hp_a=base_hp, hp_b=hp_b,
                name_a="Tú (Jugador A)", name_b="Bot" if st.session_state.vs_bot else "Jugador B",
            )
            st.session_state["scroll_to_battle"] = True
            st.session_state["turn_start_time"] = time.time()
            st.rerun()


# --------------------------------------------------------------------------- #
# Barra rápida del Marketplace (navbar superior, a la derecha del menú ☰):
# balance + accesos directos a Explorar/Inventario, visible solo en el panel
# 🛒 — independiente de render_battle_mode_bar (esa vive en la página de
# Batalla, no aquí).
# --------------------------------------------------------------------------- #
def render_marketplace_quickbar(connector: BlockchainConnector, wallet_address: str) -> None:
    kur_balance = connector.kur_balance(wallet_address)
    owned_count = len(connector.owned_token_ids(wallet_address))
    info_col, explore_col, inv_col = st.columns([2.2, 1.3, 1.3])
    with info_col:
        st.markdown(
            "<div class='kr-muted'>Balance rápido</div>"
            f"<div class='kr-price' style='font-size:0.95rem'>{kur_balance:,.2f} $KUR · {owned_count} Fox</div>",
            unsafe_allow_html=True,
        )
    with explore_col:
        st.caption("​")
        if st.button("🛒 Explorar", key="navbar_mkt_explore", width="stretch"):
            st.session_state["mkt_active_tab"] = marketplace_app.TAB_EXPLORE
            st.rerun()
    with inv_col:
        st.caption("​")
        if st.button("🎒 Inventario", key="navbar_mkt_inventory", width="stretch"):
            st.session_state["mkt_active_tab"] = marketplace_app.TAB_INVENTORY
            st.rerun()


def render_wallet_and_nft_bar(connector: BlockchainConnector, wallet_address: str) -> None:
    """Wallet + NFT equipado en disposición lateral/paralela, en dos
    columnas únicamente (nunca una tercera fila que vuelva a estirar el
    panel verticalmente): selector + avatar a la izquierda, y Dirección /
    NFTs·$KUR / Rareza / HP base / Buff mazo apilados a la derecha, todo a
    la misma altura que el avatar. La rareza del NFT elegido aquí decide el
    HP base y el buff de mazo (ver resolve_equipped_rarity, usado por
    🔥 NUEVA PARTIDA)."""
    owned = connector.owned_token_ids(wallet_address)
    kur_balance = connector.kur_balance(wallet_address)
    short_addr = f"{wallet_address[:6]}…{wallet_address[-4:]}" if len(wallet_address) > 12 else wallet_address

    with st.container(key="wallet_nft_bar"):
        if not owned:
            st.session_state.equipped_token_id = None
            st.metric("🦊 Dirección", short_addr)
            w2, w3 = st.columns(2)
            w2.metric("NFTs", 0)
            w3.metric("Balance $KUR", f"{kur_balance:,.2f}")
            st.caption("Sin NFTs en esta wallet — jugarás con el HP base por defecto (sin NFT).")
            return

        if st.session_state.get("equipped_token_id") not in owned:
            best = connector.best_rarity_for_wallet(wallet_address)
            st.session_state.equipped_token_id = next(
                (tid for tid in owned if derive_placeholder_rarity(tid) == best), owned[0]
            )

        img_col, wallet_col = st.columns([1, 1.3])

        with img_col:
            equipped_id = st.selectbox(
                "🎴 NFT equipado",
                owned,
                format_func=lambda tid: f"Fox #{tid} · {rarity_label(derive_placeholder_rarity(tid))}",
                key="equipped_token_id",
            )
            fox_path = PVP_ROOT / "GAMENFT" / f"fox_{equipped_id}.png"
            if fox_path.exists():
                st.image(str(fox_path), width=160)
        rarity = derive_placeholder_rarity(equipped_id)

        with wallet_col:
            st.metric("🦊 Dirección", short_addr)
            st.metric("NFTs · $KUR", f"{len(owned)} · {kur_balance:,.0f}")
            buff_pct = DECK_STAT_BUFF_PCT.get(rarity, 0.0)
            s1, s2, s3 = st.columns(3)
            s1.metric("Rareza", rarity_label(rarity))
            s2.metric("HP base", compute_base_hp(rarity))
            s3.metric("Buff mazo", f"+{buff_pct:.0%}" if buff_pct else "—")


def render_wallet_config_expander(connector: BlockchainConnector, wallet_address: str) -> None:
    """Herramientas de testeo de la wallet simulada (cambiar dirección,
    previsualizar un NFT, recargar Token IDs/$KUR) — antes en la barra
    lateral, ahora plegadas en la página de Batalla para no molestar
    durante la partida."""
    with st.expander("🛠️ Configuración y Recarga de Wallet Simulada"):
        st.caption(f"Modo: {'SIMULACIÓN' if connector.is_simulated else 'BSC real'}")
        new_address = st.text_input("Dirección de wallet", value=wallet_address, key="battle_wallet_address_input")
        if new_address != wallet_address:
            st.session_state.wallet_address = new_address
            st.rerun()

        owned = connector.owned_token_ids(wallet_address)
        kur_balance = connector.kur_balance(wallet_address)

        if owned:
            preview_id = st.selectbox("Previsualizar NFT", owned, key="preview_nft")
            fox_path = PVP_ROOT / "GAMENFT" / f"fox_{preview_id}.png"
            if fox_path.exists():
                st.image(
                    str(fox_path),
                    caption=f"Fox #{preview_id} · {rarity_label(derive_placeholder_rarity(preview_id))}",
                    width="stretch",
                )

        st.divider()
        st.caption("Recargar wallet de prueba con otros NFTs/KUR:")
        new_ids = st.text_input(
            "Token IDs (separados por coma)", value=",".join(str(i) for i in owned), key="battle_wallet_ids"
        )
        new_kur = st.number_input(
            "$KUR simulado", min_value=0.0, value=float(kur_balance), step=10.0, key="battle_wallet_kur"
        )
        if st.button("💾 Actualizar wallet simulada", width="stretch", key="battle_wallet_update"):
            try:
                ids = [int(x.strip()) for x in new_ids.split(",") if x.strip()]
            except ValueError:
                ids = []
            connector.simulated_backend.seed_wallet(wallet_address, ids, new_kur)
            st.rerun()


def render_wallet_badge(connector: BlockchainConnector, wallet_address: str) -> None:
    kur_balance = connector.kur_balance(wallet_address)
    short_addr = f"{wallet_address[:6]}…{wallet_address[-4:]}" if len(wallet_address) > 12 else wallet_address
    with st.popover(f"🦊 {short_addr} · {kur_balance:,.2f} $KUR", width="stretch"):
        st.caption("Wallet simulada — recargar balance / NFTs (misma wallet de toda la sesión)")
        owned = connector.owned_token_ids(wallet_address)
        new_ids = st.text_input(
            "Token IDs (separados por coma)", value=",".join(str(i) for i in owned), key="navbar_wallet_ids"
        )
        new_kur = st.number_input(
            "$KUR simulado", min_value=0.0, value=float(kur_balance), step=10.0, key="navbar_wallet_kur"
        )
        if st.button("💾 Guardar", key="navbar_wallet_save", width="stretch", type="primary"):
            try:
                ids = [int(x.strip()) for x in new_ids.split(",") if x.strip()]
            except ValueError:
                ids = []
            if connector.simulated_backend is not None:
                connector.simulated_backend.seed_wallet(wallet_address, ids, new_kur)
            st.rerun()


st.set_page_config(
    page_title="Kurama Run · PVP Simulator", page_icon="⚔️", layout="wide", initial_sidebar_state="collapsed"
)
init_state()
catalog_dicts = load_catalog()
catalog = [Card.from_dict(d) for d in catalog_dicts]
pool_27 = [c for c in catalog if c.category in (CardCategory.STANDARD, CardCategory.RARE)]
cards_by_id = {c.id: c for c in catalog}

# Disponibles pronto, antes de renderizar nada, para que la barra de
# configuración contextual de arriba (render_context_bar/render_wallet_badge)
# pueda leer la wallet y lanzar partidas de inmediato — todo el panel lee el
# mismo st.session_state.connector / .wallet_address, nunca se desincroniza.
connector: BlockchainConnector = st.session_state.connector
current_wallet_address = st.session_state.wallet_address
st.session_state.vs_bot = st.session_state.opponent_choice == "Bot"

# Tema compartido con el Marketplace (../kurama_theme.py): fondo oscuro,
# tipografías gaming y badges de rareza/estado comunes a los dos paneles.
st.markdown(kurama_theme.base_css(), unsafe_allow_html=True)

# --------------------------------------------------------------------------- #
# SFX 8-bit al interactuar (botones, zona, Resolver turno): un solo listener
# global de click en window.parent.document (mismo truco que el resto del
# JS del archivo), instalado una vez (guard en window.parent) para que no se
# duplique en cada rerun de Streamlit. El pitido se sintetiza con WebAudio
# (sin archivo de audio, sin latencia) y se calla solo si el reproductor de
# música de fondo (nav_music_player, único control de mute que existe hoy)
# está silenciado o pausado — así respeta el mismo mute que la música.
# --------------------------------------------------------------------------- #
components.html(
    """<script>
    (function () {
        const w = window.parent;
        if (w.__kuramaSfxInstalled) { return; }
        w.__kuramaSfxInstalled = true;
        let audioCtx = null;

        function isMusicMuted() {
            const el = w.document.querySelector('[class*="st-key-nav_music_player"] audio');
            return el ? (el.muted || el.volume === 0) : false;
        }

        function playBlip() {
            if (isMusicMuted()) { return; }
            try {
                const AC = w.AudioContext || w.webkitAudioContext;
                if (!audioCtx) { audioCtx = new AC(); }
                if (audioCtx.state === 'suspended') { audioCtx.resume(); }
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'square';
                osc.frequency.setValueAtTime(1100, audioCtx.currentTime);
                gain.gain.setValueAtTime(0.08, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.08);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.08);
            } catch (e) { /* navegador sin WebAudio: no interrumpe la UI */ }
        }

        w.document.addEventListener('click', function (ev) {
            const hit = ev.target.closest('button, [role="option"], [role="combobox"], [data-baseweb="select"]');
            if (hit) { playBlip(); }
        }, true);
    })();
    </script>""",
    height=0,
)

# --------------------------------------------------------------------------- #
# Navbar superior: solo menú ☰ + badge de wallet (y, en el panel 🛒, la
# barra rápida de Marketplace). Los controles de Modo/Oponente/Nueva
# partida ya no viven aquí — están más abajo, en la página de Batalla
# (render_battle_mode_bar), debajo del panel de Wallet + NFT equipado. No
# hay barra lateral: toda la navegación y configuración vive en el área
# principal.
# --------------------------------------------------------------------------- #
st.session_state.setdefault("panel_mode", PANEL_OPTIONS[0])

nav_menu_col, _nav_spacer, nav_music_col, nav_wallet_col = st.columns([1.5, 4.0, 1.5, 2.1])

with nav_menu_col:
    with st.popover("☰ MENÚ DE NAVEGACIÓN"):
        st.caption("Ir directamente a:")
        for _option in PANEL_OPTIONS:
            if st.button(
                _option,
                key=f"nav_top_{_option}",
                width="stretch",
                type="primary" if st.session_state.panel_mode == _option else "secondary",
            ):
                st.session_state.panel_mode = _option
                st.rerun()

with nav_music_col:
    # Chiptune de fondo discreto: "Spooky Dungeon" de Memoraphile (OpenGameArt,
    # CC0/CC-BY), en loop y autoplay — el reproductor nativo de Streamlit ya
    # trae su propio botón de play/pausa (sirve de silenciar). Solo se
    # renderiza en combate activo: antes de pulsar NUEVA PARTIDA o tras
    # Victoria/Derrota/Empate, match es None o su status ya no es ONGOING, y
    # el reproductor se desmonta deteniendo el audio al instante.
    _active_match = st.session_state.get("match")
    if _active_match is not None and _active_match.status == MatchStatus.ONGOING:
        with st.container(key="nav_music_player"):
            st.audio(
                "https://opengameart.org/sites/default/files/Memoraphile%20-%20Spooky%20Dungeon.mp3",
                loop=True,
                autoplay=True,
            )

with nav_wallet_col:
    render_wallet_badge(connector, current_wallet_address)

panel_mode = st.session_state.panel_mode
if panel_mode.startswith("🛒"):
    render_marketplace_quickbar(connector, current_wallet_address)

st.title("⚔️ Kurama Run — Simulador PVP")
st.caption("Prototipo local y desacoplado — motor de combate + lectura BSC simulada. No usa fondos reales.")

# --------------------------------------------------------------------------- #
# Playmat compacto: imágenes de mano más pequeñas, nombre/habilidad a altura
# fija (para que Zona/Jugar queden siempre en la misma línea entre cartas) y
# el feed de combate flotante (ver más abajo) compactos por CSS.
# --------------------------------------------------------------------------- #
st.markdown(
    """
    <style>
    /* Efecto "presionado" (:active) global para todos los botones del
       juego (⚔️ JUGAR, ℹ️ Detalles, Jugar otra partida, Resolver turno...):
       el botón se hunde y la sombra se aplana al pulsar — un <button> real
       (lo que genera cada st.button/st.popover) dispara :active igual con
       click de ratón que con tap táctil, sin JS necesario. La CTA grande
       "NUEVA PARTIDA" tiene su propio override más abajo (misma física,
       sombra a su escala). */
    .stButton > button,
    .stPopover button {
        transition: transform 0.05s ease, box-shadow 0.05s ease;
    }
    .stButton > button:active,
    .stPopover button:active {
        transform: translateY(2px) scale(0.98);
        box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.45) !important;
    }
    /* Sin barra lateral: se oculta por completo, junto con su botón de
       abrir/cerrar (kurama_theme.py lo restylea como "☰ MENÚ", pero ya hay
       un menú real arriba — este oculta ese botón huérfano también). */
    [data-testid="stSidebar"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="collapsedControl"],
    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapseButton"] {
        display: none !important;
    }
    /* Mano de cartas: ancho de columna fijo, imágenes TODAS al mismo
       tamaño exacto (160x240, object-fit: contain para no deformar el
       arte aunque cada PNG traiga su propio recorte/proporción — con
       "cover" se vería bien pero recortaría parte del dibujo en las
       cartas que no encajen justo en 2:3), centradas como grupo en vez de
       repartidas a los lados con huecos en blanco. */
    [class*="st-key-battle_hand"] img {
        width: 160px !important;
        height: 240px !important;
        max-width: 100% !important;
        object-fit: contain;
        background: rgba(255, 255, 255, 0.04);
        border-radius: 8px;
        display: block;
        margin: 0 auto;
    }
    [class*="st-key-battle_hand"] [data-testid="stHorizontalBlock"] {
        justify-content: center !important;
        align-items: stretch !important;
        flex-wrap: wrap;
        gap: 1.5rem !important;
    }
    [class*="st-key-battle_hand"] [data-testid="stColumn"] {
        flex: 0 0 240px !important;
        width: 240px !important;
        max-width: 240px !important;
        min-width: 180px;
    }
    /* Cada carta completa (imagen + nombre + info + zona + jugar) a la
       misma altura que sus vecinas de la fila (align-items:stretch arriba
       iguala las columnas; esto reparte ese alto dentro de cada carta para
       que el botón "Jugar" quede siempre a la misma altura entre cartas,
       tengan o no línea de habilidad). */
    [class*="st-key-battle_hand"] [data-testid="stColumn"] [data-testid="stVerticalBlock"] {
        height: 100%;
    }
    [class*="st-key-card_glow_playable"],
    [class*="st-key-card_glow_locked"] {
        display: flex !important;
        flex-direction: column;
        justify-content: space-between;
        height: 100%;
    }
    .card-name {
        font-weight: 600;
        font-size: 0.85rem;
        min-height: 2.3em;
        line-height: 1.15em;
        overflow: hidden;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        margin-top: 0.25rem;
    }
    /* Altura fija para la línea de habilidad ('piercing', 'counter', ...)
       aunque esté vacía, para que el selector de Zona y el botón Jugar
       queden siempre alineados entre cartas con y sin habilidad. */
    .card-ability {
        font-size: 0.75rem;
        color: rgba(135, 135, 135, 0.9);
        min-height: 1.3em;
        line-height: 1.3em;
        margin: 0.1rem 0 0.2rem;
    }
    /* "ℹ️ Info" por carta: tooltip 100% CSS (sin widget de Streamlit, sin
       rerun del servidor) para que se vea al INSTANTE con el ratón encima —
       antes era un st.popover que había que pulsar, y con los 20s de
       cuenta atrás del turno cada click de más cuenta. :hover lo abre en
       PC; :focus hace lo mismo al tocar con el dedo en móvil (un <button>
       real recibe foco al tap). z-index por encima del zoom de la imagen
       (z-index:100 más abajo) para que no se la coma ese hover. */
    .card-info-wrap {
        position: relative;
        display: inline-block;
    }
    .card-info-trigger {
        position: relative;
        z-index: 150;
        display: inline-block;
        font-size: 0.72rem;
        padding: 0.1rem 0.5rem;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.08);
        border: 1px solid rgba(255, 255, 255, 0.25);
        color: inherit;
        cursor: pointer;
    }
    .card-info-panel {
        position: absolute;
        z-index: 200;
        top: 100%;
        left: 0;
        margin-top: 0.3rem;
        width: max-content;
        max-width: min(260px, 85vw);
        padding: 0.5rem 0.65rem;
        border-radius: 8px;
        background: rgba(20, 20, 22, 0.97);
        border: 1px solid rgba(255, 255, 255, 0.2);
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.5);
        font-size: 0.78rem;
        line-height: 1.3em;
        opacity: 0;
        visibility: hidden;
        transition: opacity 0.08s ease;
        pointer-events: none;
    }
    .card-info-trigger:hover + .card-info-panel,
    .card-info-trigger:focus + .card-info-panel {
        opacity: 1;
        visibility: visible;
    }
    .card-info-panel .card-info-name {
        font-weight: 700;
        margin-bottom: 0.2rem;
    }
    .card-info-panel .card-info-desc {
        color: rgba(210, 210, 210, 0.85);
        margin-top: 0.15rem;
    }
    /* Catálogo de cartas: imagen a altura fija (object-fit: contain, en vez
       de estirar al ancho y dejar que la relación de aspecto de cada arte
       decida la altura de la fila) y cajas de texto de altura fija para la
       nota de habilidad y la descripción (recortada con línea de puntos si
       es más larga que otras), para que las 4 columnas de la parrilla
       queden siempre alineadas y del mismo tamaño. */
    [class*="st-key-gallery_grid"] [data-testid="stImage"] img {
        height: 230px !important;
        width: auto !important;
        max-width: 100% !important;
        object-fit: contain;
        display: block;
        margin: 0 auto;
    }
    .gallery-ability {
        font-size: 0.8rem;
        color: rgb(107, 163, 107);
        min-height: 1.3em;
        line-height: 1.3em;
        margin: 0.15rem 0;
    }
    .gallery-desc {
        font-size: 0.78rem;
        color: rgba(135, 135, 135, 0.95);
        min-height: 3.3em;
        max-height: 3.3em;
        line-height: 1.1em;
        overflow: hidden;
        display: -webkit-box;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        margin-top: 0.2rem;
    }
    /* Zoom al pasar el ratón por una carta, tanto en la mano de combate
       como en el Catálogo de Cartas y el Tutorial Interactivo: la imagen
       se amplía por encima de sus vecinas (los contenedores padres no
       recortan con overflow). */
    [class*="st-key-battle_hand"] [data-testid="stImage"] img,
    [class*="st-key-gallery_grid"] [data-testid="stImage"] img,
    [class*="st-key-tutorial_card"] [data-testid="stImage"] img {
        transition: transform 0.25s ease-in-out, box-shadow 0.25s ease-in-out;
        cursor: pointer;
    }
    [class*="st-key-battle_hand"] [data-testid="stImage"] img:hover,
    [class*="st-key-gallery_grid"] [data-testid="stImage"] img:hover,
    [class*="st-key-tutorial_card"] [data-testid="stImage"] img:hover {
        transform: scale(1.35);
        position: relative;
        z-index: 100;
        box-shadow: 0px 10px 25px rgba(0, 0, 0, 0.6);
        border-radius: 8px;
    }
    .combat-feed {
        background: rgba(135, 135, 135, 0.08);
        border: 1px solid rgba(135, 135, 135, 0.25);
        border-radius: 8px;
        padding: 6px 12px;
        font-size: 0.82rem;
        line-height: 1.35;
        max-height: 6.5em;
        overflow-y: auto;
        margin-bottom: 0.4rem;
    }
    /* Tarjetas del log (feed compacto y registro completo, ver
       _format_log_entry): color por bando y separador de turno, para que se
       lea como un log de TCG en vez de texto plano. */
    .log-entry {
        padding: 3px 8px;
        margin: 2px 0;
        border-radius: 6px;
        border-left: 3px solid transparent;
    }
    .log-entry.log-side-a {
        background: rgba(66, 135, 245, 0.12);
        border-left-color: #4287f5;
    }
    .log-entry.log-side-b {
        background: rgba(230, 57, 70, 0.12);
        border-left-color: #e63946;
    }
    .log-entry.log-side-neutral {
        background: rgba(135, 135, 135, 0.1);
        border-left-color: rgba(135, 135, 135, 0.4);
    }
    .log-turn-sep {
        text-align: center;
        font-weight: 700;
        text-transform: uppercase;
        font-size: 0.72rem;
        letter-spacing: 0.05em;
        color: rgba(255, 255, 255, 0.5);
        margin: 6px 0 2px;
    }
    /* Resplandor en cartas jugables (coste <= energía disponible) y
       atenuado en las que no se pueden pagar — ver el key dinámico
       card_glow_playable_*/card_glow_locked_* en render_hand. */
    [class*="st-key-card_glow_playable"] img {
        box-shadow: 0 0 12px rgba(0, 255, 136, 0.7);
        border: 1px solid #00ff88;
        border-radius: 8px;
    }
    [class*="st-key-card_glow_locked"] {
        opacity: 0.7;
    }
    /* Cabecera de combate (HP/energía/turno) más compacta, para que el
       tablero entero quepa de un vistazo sin tanto scroll. */
    [class*="st-key-battle_header"] [data-testid="stVerticalBlock"] {
        gap: 0.5rem !important;
    }
    [class*="st-key-battle_header"] [data-testid="stHeading"] h3 {
        font-size: 1rem !important;
        margin: 0 !important;
    }
    [class*="st-key-battle_header"] [data-testid="stHeading"] h2 {
        font-size: 1.05rem !important;
        margin: 0 !important;
    }
    [class*="st-key-battle_header"] [data-testid="stProgress"] {
        margin: 0.1rem 0 !important;
    }
    [class*="st-key-battle_header"] [data-testid="stProgressBarTrack"] {
        height: 6px !important;
    }
    [class*="st-key-battle_header"] [data-testid="stCaptionContainer"] p {
        font-size: 0.78rem !important;
        line-height: 1.25em !important;
    }
    /* Energía disponible: destacada y grande junto a la barra de vida de
       cada jugador (antes iba metida en una caption pequeña junto a otros
       datos secundarios como mazo/descarte). */
    .energy-display {
        font-size: 1.4rem;
        font-weight: 700;
        color: #ffd54a;
        margin: 0.15rem 0 0.1rem;
        line-height: 1.2em;
    }
    .energy-display.energy-low {
        color: #ff6b6b;
    }
    .deck-discard-count {
        font-size: 0.78rem;
        color: rgba(200, 200, 200, 0.75);
    }
    /* Zonas dañadas (combo tripartito): badge por zona en vez del texto
       plano anterior — intacta = gris sutil, dañada = rojo con brillo. */
    .zone-badges {
        display: flex;
        align-items: center;
        gap: 0.35rem;
        margin: 0.2rem 0 0.1rem;
        flex-wrap: wrap;
        cursor: help;
    }
    .zone-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.15rem;
        font-size: 1rem;
        padding: 0.1rem 0.45rem;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.06);
        border: 1px solid rgba(255, 255, 255, 0.18);
        filter: grayscale(0.7);
        opacity: 0.7;
    }
    .zone-badge-damaged {
        background: rgba(139, 0, 0, 0.35);
        border: 1px solid #ff6b6b;
        box-shadow: 0 0 6px rgba(255, 107, 107, 0.65);
        filter: none;
        opacity: 1;
    }
    .zone-combo-claimed {
        font-size: 0.78rem;
        font-weight: 700;
        color: #ffd54a;
        margin-left: 0.1rem;
    }
    .zone-combo-progress {
        font-size: 0.78rem;
        font-weight: 600;
        color: rgba(220, 220, 220, 0.65);
        margin-left: 0.1rem;
    }
    /* Reproductor de música de fondo en la navbar: discreto, no todo el
       ancho de la columna que Streamlit le da por defecto. */
    [class*="st-key-nav_music_player"] audio {
        height: 32px;
        max-width: 210px;
    }
    /* Temporizador de turno (col_vs, junto a "Turno N"): barra que se vacía
       vía JS (ver components.html más abajo) + número de segundos. El botón
       que dispara el fin de turno automático al llegar a 0 está forzado
       invisible — sigue siendo "clicable" por JS (.click() dispara su
       listener real aunque esté display:none), solo no debe verse. */
    .turn-timer-wrap {
        margin-top: 0.3rem;
    }
    .turn-timer-track {
        height: 5px;
        border-radius: 3px;
        background: rgba(135, 135, 135, 0.25);
        overflow: hidden;
    }
    .turn-timer-fill {
        height: 100%;
        background: linear-gradient(90deg, #00ff88, #ffd54a 60%, #ff6b6b);
        transition: width 0.3s linear;
    }
    .turn-timer-label {
        text-align: center;
        font-size: 0.78rem;
        color: rgba(220, 220, 220, 0.85);
        margin-top: 0.15rem;
    }
    [class*="st-key-turn_timeout_trigger"] {
        display: none !important;
    }
    /* Wallet + NFT equipado (render_wallet_and_nft_bar): una sola fila
       horizontal y compacta — imagen de NFT chica, métricas con poco
       relleno y fuente reducida para que ocupe el mínimo alto posible. */
    [class*="st-key-wallet_nft_bar"] [data-testid="stVerticalBlock"] {
        gap: 0.25rem !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stHorizontalBlock"] {
        gap: 0.6rem !important;
        align-items: center !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stMetric"] {
        padding: 0.15rem 0.3rem !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stMetricValue"] {
        font-size: 1rem !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stMetricLabel"] p {
        font-size: 0.65rem !important;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
        line-height: 1.1 !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stImage"] img {
        border-radius: 6px;
        max-height: 160px;
        width: auto !important;
        object-fit: contain;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stSelectbox"] label p {
        font-size: 0.78rem !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stCaptionContainer"] p {
        font-size: 0.72rem !important;
        margin: 0.1rem 0 0 !important;
    }
    /* Barra de Modo/Oponente/Nueva partida (render_battle_mode_bar): botones
       bastante más chicos que el tamaño por defecto de Streamlit — menos
       padding, fuente reducida y altura fija — para que no domine la
       pantalla ahora que vive debajo del panel de Wallet + NFT. */
    [class*="st-key-battle_mode_bar"] [data-testid="stVerticalBlock"] {
        gap: 0.3rem !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stHorizontalBlock"] {
        gap: 0.4rem !important;
    }
    /* Selectores de Modo (Free/Competitivo) y Oponente (Bot/Hotseat):
       st.segmented_control, de selección única y excluyente — el grupo
       ocupa todo el ancho disponible (botones grandes, sin huecos a los
       lados), en gris oscuro con borde; la opción activa
       (aria-checked="true") en naranja/rojo vivo y las inactivas en gris
       apagado. */
    [class*="st-key-battle_mode_bar"] [data-testid="stElementContainer"] {
        width: 100% !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stButtonGroup"] {
        display: flex !important;
        width: 100% !important;
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(135, 135, 135, 0.3);
        border-radius: 8px;
        padding: 3px;
        gap: 3px !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stButtonGroup"] > div {
        flex: 1 1 0 !important;
        display: flex !important;
        width: 100% !important;
        max-width: none !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stButtonGroup"] button {
        flex: 1 1 0 !important;
        width: 100% !important;
        background: transparent !important;
        border: none !important;
        color: rgba(170, 170, 170, 0.85) !important;
        font-size: 0.92rem !important;
        font-weight: 600 !important;
        padding: 0.75rem 0.3rem !important;
        min-height: 3.4rem !important;
        height: auto !important;
        border-radius: 6px !important;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
        line-height: 1.2 !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stButtonGroup"] button[aria-checked="true"] {
        background: linear-gradient(135deg, #FF5500, #FF6B00) !important;
        color: #fff !important;
        font-weight: 700 !important;
    }
    /* El bloque de Modo/Oponente es, igual que el panel de NFT, una tarjeta
       propia con borde — para que los 4 botones se lean como parte de un
       panel delimitado y no floten sueltos con huecos vacíos alrededor. */
    [class*="st-key-battle_mode_bar"] {
        border: 1px solid rgba(135, 135, 135, 0.25);
        border-radius: 10px;
        padding: 0.7rem 0.8rem;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stNumberInput"] input {
        font-size: 0.72rem !important;
        padding: 0.2rem 0.5rem !important;
        height: 1.9rem !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stNumberInput"] label p {
        font-size: 0.68rem !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stCaptionContainer"] p {
        font-size: 0.68rem !important;
        margin: 0.15rem 0 0 !important;
    }
    /* Panel compacto de Wallet + NFT equipado, a la izquierda: tarjeta con
       borde a juego con la de Modo/Oponente (misma línea/radio) y estirada
       a la misma altura que la columna de acción de la derecha (CTA +
       Modo/Oponente), para que ambos bloques queden simétricos. */
    [class*="st-key-left_info_panel"] {
        border: 1px solid rgba(135, 135, 135, 0.25);
        border-radius: 10px;
        padding: 0.6rem 0.8rem 0.4rem;
        height: 100%;
        box-sizing: border-box;
    }
    /* CTA principal "🔥 NUEVA PARTIDA" (render_new_match_button): grande,
       ancha y en rojo/naranja vivo para que sea la acción principal de la
       pantalla antes de empezar a jugar. */
    [class*="st-key-new_match_cta"] {
        height: 100%;
        display: flex;
        align-items: stretch;
    }
    [class*="st-key-new_match_cta"] [data-testid="stVerticalBlock"] {
        height: 100%;
        display: flex;
        align-items: stretch;
    }
    [class*="st-key-new_match_cta"] .stButton {
        width: 100%;
        height: 100%;
    }
    [class*="st-key-new_match_cta"] .stButton > button {
        background: linear-gradient(135deg, #FF5500, #FF6B00) !important;
        border: none !important;
        color: #fff !important;
        font-size: 1.5rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.04em;
        text-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
        white-space: normal !important;
        min-height: 6rem !important;
        height: 100% !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 16px rgba(255, 85, 0, 0.55);
        transition: transform 0.15s ease-in-out, box-shadow 0.15s ease-in-out;
    }
    [class*="st-key-new_match_cta"] .stButton > button:hover {
        transform: scale(1.04);
        box-shadow: 0 8px 28px rgba(255, 107, 0, 0.85);
        color: #fff !important;
    }
    [class*="st-key-new_match_cta"] .stButton > button:active {
        transform: translateY(2px) scale(0.98) !important;
        box-shadow: 0 2px 8px rgba(255, 85, 0, 0.5) !important;
        transition: transform 0.05s ease, box-shadow 0.05s ease !important;
    }
    /* Exclusivo para móvil: el escritorio se queda con el diseño original
       (fuera de esta media query no hay ningún override de estas reglas) —
       marcador en una sola fila delgada, proporción 42/16/42 entre columnas
       (en vez de la [5,1,5] por defecto, que en pantallas anchas ya se ve
       bien) y mano de cartas en carrusel horizontal scrollable en vez de
       apiladas, para que quepa de un vistazo.
       shortcut: max-width 768px no cubre móviles muy anchos en landscape
       (>768px, p.ej. algunos phablets); si hace falta, añadir aquí
       "and (max-height: 500px)" junto a un "(orientation: landscape)". */
    @media (max-width: 768px) {
        [class*="st-key-battle_header"] [data-testid="stHorizontalBlock"] {
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 0.3rem !important;
        }
        [class*="st-key-battle_header"] [data-testid="stColumn"] {
            min-width: 0 !important;
            padding: 0 !important;
        }
        [class*="st-key-battle_header"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1),
        [class*="st-key-battle_header"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(3) {
            flex: 0 0 42% !important;
            width: 42% !important;
        }
        [class*="st-key-battle_header"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) {
            flex: 0 0 16% !important;
            width: 16% !important;
        }
        [class*="st-key-battle_header"] [data-testid="stVerticalBlock"] {
            gap: 0.1rem !important;
        }
        [class*="st-key-battle_header"] [data-testid="stHeading"] h3 {
            font-size: 0.75rem !important;
        }
        [class*="st-key-battle_header"] h2 {
            font-size: 1.1rem !important;
            margin: 0 !important;
        }
        [class*="st-key-battle_header"] [data-testid="stProgressBarTrack"] {
            height: 4px !important;
        }
        [class*="st-key-battle_header"] [data-testid="stProgress"] p {
            font-size: 0.8rem !important;
        }
        [class*="st-key-battle_header"] .energy-display {
            font-size: 0.88rem !important;
            margin: 0.1rem 0 0.15rem !important;
        }
        [class*="st-key-battle_header"] [data-testid="stCaptionContainer"] p {
            font-size: 0.6rem !important;
            line-height: 1.1em !important;
        }
        [class*="st-key-battle_hand"] [data-testid="stHorizontalBlock"] {
            flex-wrap: nowrap !important;
            overflow-x: auto !important;
            justify-content: flex-start !important;
            gap: 0.5rem !important;
            padding-bottom: 0.3rem;
            -webkit-overflow-scrolling: touch;
        }
        [class*="st-key-battle_hand"] [data-testid="stColumn"] {
            flex: 0 0 130px !important;
            width: 130px !important;
            max-width: 130px !important;
            min-width: 130px !important;
        }
        [class*="st-key-battle_hand"] img {
            width: 107px !important;
            height: 160px !important;
        }
        [class*="st-key-battle_hand"] .stButton > button {
            font-size: 0.72rem !important;
            padding: 0.3rem 0.4rem !important;
            min-height: 2rem !important;
        }
        [class*="st-key-battle_hand"] .card-name {
            font-size: 0.7rem !important;
            min-height: 1.8em !important;
        }
        [class*="st-key-battle_hand"] .card-ability {
            font-size: 0.6rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


if panel_mode.startswith("🎓"):
    render_tutorial(catalog_dicts)
    st.stop()

if panel_mode.startswith("📖"):
    render_gallery(catalog_dicts)
    st.stop()

if panel_mode.startswith("🛒"):
    render_marketplace_panel(connector, current_wallet_address)
    st.stop()

# --------------------------------------------------------------------------- #
# Página de Batalla: tarjeta de estado de la wallet + NFT equipado, barra
# compacta de Modo/Oponente/Nueva partida justo debajo, y herramientas de
# testeo plegadas en un expander para que no estorben durante la partida —
# ver render_wallet_and_nft_bar / render_battle_mode_bar /
# render_wallet_config_expander más arriba (antes vivían en la barra lateral
# o en el navbar superior).
# --------------------------------------------------------------------------- #
game_started = st.session_state.match is not None
with st.expander("⚙️ Configuración de Partida y NFT", expanded=not game_started):
    left_info_col, cta_col = st.columns([1, 1])
    with left_info_col:
        with st.container(key="left_info_panel"):
            render_wallet_and_nft_bar(connector, current_wallet_address)
    with cta_col:
        render_new_match_button(connector, current_wallet_address, pool_27)
        render_battle_mode_bar(connector, current_wallet_address, pool_27)
render_wallet_config_expander(connector, current_wallet_address)

match = st.session_state.match
if match is None:
    st.info("Pulsa **🔥 NUEVA PARTIDA** arriba para empezar a jugar.")
    st.stop()


def _fire_event_toasts(log_before: int) -> None:
    """Un toast rápido por cada línea de log que generó la última jugada
    (daño, curación, contraataque, carta del Bot...), en vez de un único
    mensaje genérico — así el jugador ve el resultado real al instante sin
    bajar al registro completo."""
    new_lines = [line for line in match.log[log_before:] if not line.startswith("--- Turno")]
    for line in new_lines[:4]:
        if "inflige" in line or "Combo" in line:
            icon = "⚔️"
        elif "cura" in line or "bloquea" in line:
            icon = "💚"
        elif "contraataca" in line:
            icon = "↩️"
        elif "juega" in line:
            icon = "🎴"
        else:
            icon = "ℹ️"
        st.toast(line, icon=icon)


_ZONE_ICONS = {"head": "🗣️", "torso": "👕", "legs": "🦵"}
_ZONE_LABELS = {"head": "Cabeza", "torso": "Torso", "legs": "Piernas"}


def _render_zone_badges(p: dict) -> None:
    """Badge por zona corporal (combo tripartito) en vez del texto plano
    anterior: intacta en gris sutil, dañada resaltada en rojo con brillo y
    check — más fácil de leer de un vistazo que una lista separada por comas."""
    damaged = set(p["zones_damaged"])
    badges = []
    for zone in ("head", "torso", "legs"):
        is_damaged = zone in damaged
        css_class = "zone-badge zone-badge-damaged" if is_damaged else "zone-badge"
        mark = " ✅" if is_damaged else ""
        badges.append(
            f"<span class='{css_class}' title='{_ZONE_LABELS[zone]}'>"
            f"{_ZONE_ICONS[zone]}{mark}</span>"
        )
    if p["tripartite_claimed"]:
        combo_tag = " <span class='zone-combo-claimed'>🔥 Combo Tripartito</span>"
    else:
        combo_tag = f" <span class='zone-combo-progress'>🔥 Progreso: {len(damaged)}/3</span>"
    tooltip = (
        "Completa las 3 zonas (🗣️ Cabeza, 👕 Torso, 🦵 Piernas) para activar el Combo Tripartito"
    )
    st.markdown(
        f"<div class='zone-badges' title='{tooltip}'>{''.join(badges)}{combo_tag}</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Temporizador de turno (TURN_TIMER_SECONDS = 20s, card_schema.py): si pasa
# el tiempo sin que el jugador actúe, el turno se resuelve solo (motor:
# auto_skip_turn + end_turn, ya existían en pvp_engine.py sin usar desde
# ningún sitio). st.session_state.turn_start_time se arma en NUEVA PARTIDA y
# se reinicia en cada end_turn() (los 2 sitios donde ya se llamaba, más este
# de aquí) — así el reloj siempre refleja el inicio real del turno actual,
# aunque Streamlit solo re-ejecute este script ante una interacción.
# --------------------------------------------------------------------------- #
st.session_state.setdefault("turn_start_time", time.time())
if match.status == MatchStatus.ONGOING:
    elapsed = time.time() - st.session_state.turn_start_time
    if elapsed >= TURN_TIMER_SECONDS:
        log_before = len(match.log)
        if st.session_state.vs_bot:
            auto_skip_turn(match, "a")
            bot_choose_plays(match, "b")
        end_turn(match)
        st.session_state.turn_start_time = time.time()
        _fire_event_toasts(log_before)
        st.rerun()

state = get_match_state(match)


# --------------------------------------------------------------------------- #
# Cabecera: HP / energía / estado (contenedor con key propia para poder
# compactarla por CSS sin afectar a cabeceras de otras secciones). El ancla
# "battle-header" vive justo aquí (no antes, donde todavía podía quedar por
# encima el panel de Configuración animándose al colapsar) para que el
# auto-scroll al pulsar NUEVA PARTIDA alinee el borde superior de la ventana
# justo encima de las barras de HP / TURNO — el setTimeout espera a que
# termine la transición CSS de colapso del expander antes de medir la
# posición, si no el scroll se calcula con el layout aún alto y queda corto.
# --------------------------------------------------------------------------- #
st.markdown('<div id="battle-header" style="scroll-margin-top: 20px;"></div>', unsafe_allow_html=True)
if st.session_state.pop("scroll_to_battle", False):
    components.html(
        """<script>
        setTimeout(function () {
            const el = window.parent.document.getElementById('battle-header');
            if (el) { el.scrollIntoView({behavior: 'smooth', block: 'start'}); }
        }, 350);
        </script>""",
        height=0,
    )
with st.container(key="battle_header"):
    col_a, col_vs, col_b = st.columns([5, 1, 5])
    for col, p, key in ((col_a, state["player_a"], "a"), (col_b, state["player_b"], "b")):
        with col:
            st.subheader(p["name"])
            st.progress(max(0.0, p["hp"] / p["max_hp"]), text=f"{p['hp']} / {p['max_hp']} HP")
            remaining_energy = min(MAX_ENERGY_SPEND_PER_TURN, p["energy"]) - p["energy_spent_this_turn"]
            st.markdown(
                f"<div class='energy-display'>⚡ Energía: {remaining_energy} / {p['energy']}</div>",
                unsafe_allow_html=True,
            )
            st.caption(
                f"Gastado este turno: {p['energy_spent_this_turn']} (tope {MAX_ENERGY_SPEND_PER_TURN}) · "
                f"Mazo: {p['deck_remaining']} · Descarte: {p['discard_count']}"
            )
            _render_zone_badges(p)
    with col_vs:
        st.markdown(f"<h2 style='text-align:center'>Turno {state['turn_number']}</h2>", unsafe_allow_html=True)
        if state["status"] == MatchStatus.ONGOING.value:
            remaining = max(0.0, TURN_TIMER_SECONDS - (time.time() - st.session_state.turn_start_time))
            pct = (remaining / TURN_TIMER_SECONDS) * 100
            st.markdown(
                f"<div class='turn-timer-wrap'>"
                f"<div class='turn-timer-track'><div class='turn-timer-fill' id='turn-timer-fill' "
                f"style='width:{pct:.1f}%'></div></div>"
                f"<div class='turn-timer-label'>⏱️ <span id='turn-timer-seconds'>{int(remaining)}</span>s</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            with st.container(key="turn_timeout_trigger"):
                st.button("⏱", key="turn_timeout_btn")
            components.html(
                f"""<script>
                (function () {{
                    const totalMs = {TURN_TIMER_SECONDS * 1000};
                    const remainingMs = {remaining * 1000:.0f};
                    const startedAt = Date.now();
                    const fill = window.parent.document.getElementById('turn-timer-fill');
                    const label = window.parent.document.getElementById('turn-timer-seconds');
                    const btn = window.parent.document.querySelector(
                        '[class*="st-key-turn_timeout_trigger"] button'
                    );
                    const iv = setInterval(function () {{
                        const left = Math.max(0, remainingMs - (Date.now() - startedAt));
                        const pct = Math.max(0, Math.min(100, (left / totalMs) * 100));
                        if (fill) fill.style.width = pct + '%';
                        if (label) label.textContent = Math.ceil(left / 1000);
                        if (left <= 0) {{
                            clearInterval(iv);
                            if (btn) btn.click();
                        }}
                    }}, 250);
                }})();
                </script>""",
                height=0,
            )

if state["status"] != MatchStatus.ONGOING.value:
    banner = {
        MatchStatus.PLAYER_A_WINS.value: f"🏆 ¡Gana {state['player_a']['name']}!",
        MatchStatus.PLAYER_B_WINS.value: f"🏆 ¡Gana {state['player_b']['name']}!",
        MatchStatus.DRAW.value: "🤝 Empate.",
    }[state["status"]]
    st.success(banner)
    if st.button("🔄 Jugar otra partida", type="primary"):
        st.session_state.match = None
        st.rerun()

def _format_log_entry(line: str, name_a: str, name_b: str) -> str:
    """Una línea del log como tarjeta con color por bando (🔵 TÚ / 🔴 BOT) y un
    emoji de acción (⚔️ daño/combo, 🛡️ curación/bloqueo/contraataque), para
    que se lea como un log de TCG en vez de texto plano."""
    if line.startswith("--- Turno"):
        return f"<div class='log-turn-sep'>{html.escape(line)}</div>"
    # El motor siempre pone el nombre de quien actúa primero en la frase
    # (ej. "Bot inflige ... de Tú (Jugador A)."), así que cuando aparecen los
    # dos nombres gana el que sale antes en el texto, no "name_a in line".
    idx_a = line.find(name_a)
    idx_b = line.find(name_b)
    if idx_a != -1 and (idx_b == -1 or idx_a < idx_b):
        side_class, side_icon = "log-side-a", "🔵"
    elif idx_b != -1:
        side_class, side_icon = "log-side-b", "🔴"
    else:
        side_class, side_icon = "log-side-neutral", "ℹ️"
    if "inflige" in line or "Combo" in line:
        action_icon = "⚔️ "
    elif "cura" in line or "bloquea" in line or "contraataca" in line:
        action_icon = "🛡️ "
    else:
        action_icon = ""
    return f"<div class='log-entry {side_class}'>{side_icon} {action_icon}{html.escape(line)}</div>"


# Feed de combate compacto (últimas 4 líneas, fijo justo sobre las cartas) —
# complementa el registro completo (más abajo, colapsado) y los toasts.
if state["log"]:
    recent_lines = "".join(
        _format_log_entry(line, state["player_a"]["name"], state["player_b"]["name"])
        for line in reversed(state["log"][-4:])
    )
    st.markdown(f'<div class="combat-feed">{recent_lines}</div>', unsafe_allow_html=True)

ZONE_LABELS = {"head": "🗣️ Cabeza", "torso": "🫁 Tronco", "legs": "🦵 Piernas"}


def render_hand(player_key: str, label: str, auto_resolve: bool = False) -> None:
    """auto_resolve=True (modo Vs. Bot): cada "Jugar" ejecuta la jugada, hace
    responder al Bot y resuelve el turno en un solo paso — sin esto, cada
    carta se queda solo "preparada" hasta pulsar Resolver turno (modo Hotseat,
    donde sí hace falta que ambos jugadores decidan antes de aplicar el turno
    a la vez, sección 2.4 de PVP_Rules). Nombre y habilidad reservan siempre
    la misma altura (vía CSS/placeholder) para que Zona y Jugar queden
    alineados en la misma línea entre cartas, tengan o no habilidad especial."""
    p = state[f"player_{player_key}"]
    remaining_energy = min(MAX_ENERGY_SPEND_PER_TURN, p["energy"]) - p["energy_spent_this_turn"]
    energy_css_class = "energy-low" if remaining_energy <= 0 else ""
    st.markdown(
        f"**Mano de {label}** · <span class='energy-display {energy_css_class}' "
        f"style='font-size:1.05rem'>⚡ {remaining_energy} disponible{'s' if remaining_energy != 1 else ''}</span> · "
        f"<span class='deck-discard-count'>🂠 Mazo: {p['deck_remaining']} · 🗑️ Descarte: {p['discard_count']}</span>",
        unsafe_allow_html=True,
    )
    if not p["hand"]:
        st.caption("Sin cartas en mano.")
        return
    with st.container(key=f"battle_hand_{player_key}"):
        cols = st.columns(len(p["hand"]))
        for col, card_dict in zip(cols, p["hand"]):
            affordable = card_dict["cost_energy"] <= remaining_energy
            glow_key = f"card_glow_{'playable' if affordable else 'locked'}_{player_key}_{card_dict['id']}"
            with col, st.container(key=glow_key):
                img = card_image_path(card_dict)
                if img:
                    st.image(str(img), width="stretch")
                st.markdown(f"<div class='card-name'>{html.escape(card_dict['name'])}</div>", unsafe_allow_html=True)
                st.caption(f"{card_dict['atk']} ATK / {card_dict['defense']} DEF · Coste {card_dict['cost_energy']}")
                ability = card_dict.get("ability")
                ability_text = f"✨ {html.escape(ability)}" if ability and ability != "none" else "&nbsp;"
                st.markdown(f"<div class='card-ability'>{ability_text}</div>", unsafe_allow_html=True)
                desc_html = (
                    f"<div class='card-info-desc'>{html.escape(card_dict['description'])}</div>"
                    if card_dict.get("description")
                    else ""
                )
                st.markdown(
                    "<div class='card-info-wrap'>"
                    "<button type='button' class='card-info-trigger' tabindex='0'>ℹ️ Info</button>"
                    "<div class='card-info-panel'>"
                    f"<div class='card-info-name'>{html.escape(card_dict['name'])} · {card_type_label(card_dict)}</div>"
                    f"<div>{card_effect_text(card_dict)}</div>"
                    f"{desc_html}"
                    "</div></div>",
                    unsafe_allow_html=True,
                )
                zone = st.selectbox(
                    "Zona",
                    [z.value for z in Zone],
                    key=f"zone_{player_key}_{card_dict['id']}",
                    format_func=lambda z: ZONE_LABELS.get(z, z),
                    label_visibility="collapsed",
                )
                label_btn = "⚔️ Jugar" if auto_resolve else "Jugar"
                if st.button(
                    label_btn,
                    key=f"play_{player_key}_{card_dict['id']}",
                    width="stretch",
                    type="primary" if auto_resolve else "secondary",
                    disabled=not affordable,
                ):
                    log_before = len(match.log)
                    try:
                        play_card(match, player_key, card_dict["id"], Zone(zone))
                    except EngineError as e:
                        st.error(str(e))
                    else:
                        if auto_resolve:
                            bot_choose_plays(match, "b")
                            end_turn(match)
                            st.session_state.turn_start_time = time.time()
                        _fire_event_toasts(log_before)
                    st.rerun()
                if not affordable:
                    st.caption("⚠️ Energía insuficiente")

    if p["staged_plays"]:
        staged_txt = ", ".join(
            f"{sp['card']['name']} → {sp['zone']}" for sp in p["staged_plays"]
        )
        st.caption(f"Jugadas preparadas este turno: {staged_txt}")


if state["status"] == MatchStatus.ONGOING.value:
    if st.session_state.vs_bot:
        render_hand("a", state["player_a"]["name"], auto_resolve=True)
    else:
        render_hand("a", state["player_a"]["name"], auto_resolve=False)
        render_hand("b", state["player_b"]["name"], auto_resolve=False)
        if st.button("⏭️ Resolver turno", type="primary", width="stretch"):
            log_before = len(match.log)
            end_turn(match)
            st.session_state.turn_start_time = time.time()
            _fire_event_toasts(log_before)
            st.rerun()

with st.expander("📜 Registro completo de la partida", expanded=False):
    log_html = "".join(
        _format_log_entry(line, state["player_a"]["name"], state["player_b"]["name"])
        for line in reversed(state["log"][-40:])
    )
    st.markdown(log_html, unsafe_allow_html=True)

with st.expander("🗂️ Catálogo de cartas (27 competitivas + 2 legendarias)"):
    tabs = st.tabs(["Estándar (18)", "Raras (9)", "Legendarias (fuera de pool)"])
    buckets = {
        0: [c for c in catalog_dicts if c["category"] == "standard"],
        1: [c for c in catalog_dicts if c["category"] == "rare"],
        2: [c for c in catalog_dicts if c["category"] == "legendary"],
    }
    for idx, tab in enumerate(tabs):
        with tab:
            bucket = buckets[idx]
            cols = st.columns(6)
            for i, card_dict in enumerate(bucket):
                with cols[i % 6]:
                    img = card_image_path(card_dict)
                    if img:
                        st.image(str(img), width="stretch")
                    st.caption(f"**{card_dict['name']}**")
                    st.caption(f"{card_dict['atk']}/{card_dict['defense']} · coste {card_dict['cost_energy']}")
