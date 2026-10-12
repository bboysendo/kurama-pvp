"""
Tema visual compartido por el PVP y el Marketplace de Kurama Run.

Única fuente de verdad para colores, tipografías y el CSS base que ambos
paneles de Streamlit inyectan (`engine/pvp_dashboard.py` y
`MARKETPLACE/marketplace_app.py`) — así no se desincroniza el "look" entre
los dos. Inspirado en kuramarun.com: fondo oscuro profundo, acento naranja
neón en botones/bordes activos, verde menta neón para badges de estado y
púrpura/cian para las rarezas.

Streamlit no permite importar un tema nativo desde Python: el
`.streamlit/config.toml` de cada panel (`engine/.streamlit/config.toml` y
`MARKETPLACE/.streamlit/config.toml`) fija los mismos colores para los
widgets nativos (botones, sliders, métricas). Si cambias una paleta aquí,
actualiza también esos dos `.toml` a mano.
"""

from __future__ import annotations

# --- Paleta base --------------------------------------------------------- #
BG_PRIMARY = "#0C0C0E"
BG_SECONDARY = "#121216"
BG_CARD = "#17171B"
ACCENT_ORANGE = "#FF5500"
ACCENT_ORANGE_SOFT = "#FF6B00"
ACCENT_MINT = "#00E599"
ACCENT_PURPLE = "#9D4DFF"
ACCENT_CYAN = "#4DD8FF"
TEXT_PRIMARY = "#F5F5F7"
TEXT_MUTED = "#9A9AA2"
BORDER = "rgba(255, 85, 0, 0.18)"

FONT_HEADING = "'Orbitron', 'Rajdhani', sans-serif"
FONT_BODY = "'Inter', sans-serif"

# Colores de rareza — deben coincidir con las zonas NFTRarity de
# engine/card_schema.py (NONE/COMMON/RARE/EPIC/LEGENDARY).
RARITY_COLORS = {
    "none": "#6B6B73",
    "common": "#9A9AA2",
    "rare": ACCENT_CYAN,
    "epic": ACCENT_PURPLE,
    "legendary": ACCENT_ORANGE,
}

RARITY_LABELS = {
    "none": "Sin rareza",
    "common": "Común",
    "rare": "Raro",
    "epic": "Épico",
    "legendary": "Legendario",
}

GOOGLE_FONTS_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=Orbitron:wght@600;800&family=Rajdhani:wght@600;700&"
    "family=Inter:wght@400;500;600&display=swap');"
)


def base_css() -> str:
    """CSS compartido: fondo degradado oscuro (sin depender de ningún
    asset de imagen — overlay tipo "bosque de bambú" simulado con
    gradientes radiales de verde menta/naranja sobre negro, para que el
    texto siempre sea legible), tipografías gaming, tarjetas con borde
    redondeado y resplandor naranja al pasar el ratón, y badges de estado
    y rareza. Cada panel SUMA su propio CSS específico encima de este (ver
    `pvp_dashboard.py` / `marketplace_app.py`)."""
    return f"""
    <style>
    {GOOGLE_FONTS_IMPORT}

    html, body, [class*="css"] {{
        font-family: {FONT_BODY};
    }}

    .stApp {{
        background:
            radial-gradient(circle at 12% -5%, rgba(0, 229, 153, 0.08), transparent 42%),
            radial-gradient(circle at 88% 105%, rgba(255, 85, 0, 0.12), transparent 48%),
            linear-gradient(180deg, {BG_PRIMARY} 0%, {BG_SECONDARY} 100%);
        color: {TEXT_PRIMARY};
    }}

    h1, h2, h3, .kr-heading {{
        font-family: {FONT_HEADING};
        text-transform: uppercase;
        letter-spacing: 0.03em;
        font-weight: 800;
    }}

    h1 strong, .kr-accent {{
        color: {ACCENT_ORANGE};
    }}

    [data-testid="stMetricValue"] {{
        font-family: {FONT_HEADING};
        color: {ACCENT_ORANGE};
    }}

    [data-testid="stTabs"] button[role="tab"] {{
        font-family: {FONT_HEADING};
        text-transform: uppercase;
        font-size: 0.82rem;
        letter-spacing: 0.03em;
    }}

    .stButton > button[kind="primary"] {{
        background: linear-gradient(135deg, {ACCENT_ORANGE} 0%, {ACCENT_ORANGE_SOFT} 100%);
        border: none;
        font-family: {FONT_HEADING};
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }}

    /* Tarjeta genérica reutilizable (catálogo del Marketplace, inventario,
       detalle) — bordes redondeados 16px y resplandor naranja tenue al
       hover/activo, como pide el estilo de kuramarun.com. */
    .kr-card {{
        background: {BG_CARD};
        border: 1px solid {BORDER};
        border-radius: 16px;
        padding: 0.75rem;
        transition: box-shadow 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
    }}
    .kr-card:hover {{
        border-color: {ACCENT_ORANGE};
        box-shadow: 0 0 22px rgba(255, 85, 0, 0.25);
        transform: translateY(-2px);
    }}
    .kr-card img {{
        border-radius: 12px;
    }}

    .kr-badge {{
        display: inline-block;
        font-family: {FONT_HEADING};
        font-size: 0.65rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        padding: 0.15rem 0.55rem;
        border-radius: 999px;
        margin: 0 0.25rem 0.25rem 0;
    }}
    .kr-badge-active {{
        background: rgba(0, 229, 153, 0.14);
        color: {ACCENT_MINT};
        border: 1px solid rgba(0, 229, 153, 0.4);
    }}
    .kr-badge-listed {{
        background: rgba(255, 85, 0, 0.14);
        color: {ACCENT_ORANGE};
        border: 1px solid rgba(255, 85, 0, 0.4);
    }}
    .kr-badge-owned {{
        background: rgba(77, 216, 255, 0.14);
        color: {ACCENT_CYAN};
        border: 1px solid rgba(77, 216, 255, 0.4);
    }}

    .kr-price {{
        font-family: {FONT_HEADING};
        color: {ACCENT_MINT};
        font-size: 1.05rem;
        font-weight: 700;
    }}
    .kr-price-sub {{
        color: {TEXT_MUTED};
        font-size: 0.75rem;
    }}
    .kr-muted {{
        color: {TEXT_MUTED};
        font-size: 0.8rem;
    }}

    /* Botón de colapsar/desplegar la barra lateral: por defecto Streamlit
       solo pinta un icono "»" diminuto y poco visible (sobre todo cuando la
       barra está contraída, stExpandSidebarButton en la esquina superior
       izquierda). Lo convertimos en un botón con fondo oscuro, borde
       naranja y la etiqueta "☰ MENÚ" para que sea evidente que ahí se abre
       la navegación. stSidebarCollapseButton (el de "cerrar", dentro de la
       propia barra ya abierta) recibe el mismo estilo por consistencia. */
    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapseButton"] button {{
        background: {BG_CARD} !important;
        border: 1px solid rgba(255, 85, 0, 0.45) !important;
        border-radius: 8px !important;
        width: auto !important;
        height: auto !important;
        padding: 0.35rem 0.7rem !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 0.35rem;
        transition: box-shadow 0.2s ease, border-color 0.2s ease, transform 0.15s ease;
    }}
    [data-testid="stExpandSidebarButton"] svg,
    [data-testid="stSidebarCollapseButton"] button svg {{
        fill: {ACCENT_ORANGE} !important;
        color: {ACCENT_ORANGE} !important;
    }}
    [data-testid="stExpandSidebarButton"]::after,
    [data-testid="stSidebarCollapseButton"] button::after {{
        content: "☰ MENÚ";
        font-family: {FONT_HEADING};
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.03em;
        color: {ACCENT_ORANGE};
        white-space: nowrap;
    }}
    [data-testid="stExpandSidebarButton"]:hover,
    [data-testid="stSidebarCollapseButton"] button:hover {{
        border-color: {ACCENT_ORANGE} !important;
        box-shadow: 0 0 10px rgba(255, 85, 0, 0.5);
        transform: translateY(-1px);
    }}
    </style>
    """


def rarity_badge_html(rarity_value: str) -> str:
    """Badge HTML coloreado para una rareza (`NFTRarity.value`): mismo
    criterio de color en el Catálogo de Cartas del PVP y en el Marketplace."""
    color = RARITY_COLORS.get(rarity_value, RARITY_COLORS["none"])
    label = RARITY_LABELS.get(rarity_value, rarity_value.title())
    return (
        f"<span class='kr-badge' style='background:{color}22; color:{color}; "
        f"border:1px solid {color}66;'>{label}</span>"
    )


def status_badge_html(label: str, kind: str) -> str:
    """Badge de estado genérico: kind = 'active' | 'listed' | 'owned'."""
    css_class = {"active": "kr-badge-active", "listed": "kr-badge-listed", "owned": "kr-badge-owned"}.get(
        kind, "kr-badge-active"
    )
    return f"<span class='kr-badge {css_class}'>{label}</span>"
