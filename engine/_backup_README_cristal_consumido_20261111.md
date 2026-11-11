# Kurama Run — Motor PVP (prototipo desacoplado)

Prototipo local del sistema de combate PVP, basado en `PVP_Rules/PVP KURAMARUN....md`.
Pensado para jugar y probar en local, y para integrarse luego en el proyecto principal sin arrastrar nada de este repo salvo lo que haga falta.

## ⚠️ Antes de nada: revisa la dirección del contrato NFT

La dirección que me diste para el NFT (`0x8331e28e7818d72ee58dfcabd7f8b71afe143f06`) **no es la colección real de Kurama Run** — está desplegada en BSC, pero su `symbol()` on-chain devuelve `"Reward at [5000usd.us]"`, que es la firma típica de un contrato de phishing (de los que mandan transferencias basura a tu wallet para que aparezcan en tu historial y piques en la URL). Es decir: la dirección correcta de tu colección "Genesis Founders" (1600 fox, mint.kuramarun.com) NO es esa. El token `$KUR` sí cuadra (`name="Kurama"`, `symbol="KRM"`, 9 decimales, 10M de supply).

El engine funciona igual mientras tanto (`NFT_CONTRACT_ADDRESS` en `engine/blockchain_connector.py` es una única constante), pero **no la uses contra la BSC real hasta confirmar la dirección correcta** (búscala en vuestro propio deploy, o en BscScan verificando el contrato de `mint.kuramarun.com`).

## Estructura

```
PVP/
  CARDS/                 10 artes de carta terminados (PNG transparente; 6 Kuramarun + 4 Talon Zhen)
                           CARDS_NO_USE/  versiones antiguas/descartadas — el catálogo nunca las usa
  GAMENFT/                1600 imágenes fox_<id>.png del NFT (sin metadata de rareza)
  PVP_Rules/              documento de diseño original (fuente de verdad de las reglas)
  Kuramarun_Whitepaper.htm  cascarón HTML vacío (Next.js) — no trae contenido real
  requirements.txt
  engine/
    card_schema.py         estructura de datos de una carta + constantes de reglas
    cards_catalog.py        genera cards_database.json (16 cartas únicas) y las imágenes de engine/assets/cards
    cards_database.json     catálogo generado (ejecutar cards_catalog.py para regenerarlo)
    blockchain_connector.py  lectura Web3/BSC — con modo SIMULACIÓN automático si no hay web3/RPC
    pvp_engine.py            motor de combate puro (start_match/play_card/end_turn/get_match_state)
    pvp_dashboard.py          panel Streamlit para jugar y probar (incluye modo ⚔️ Batalla y 🎓 Tutorial Interactivo)
    test_pvp_engine_manual.py  batería de escenarios del motor (daño, poción, perforante, contraataque, combos, energía, victoria)
    assets/cards/             cartas generadas con arte NFT (GAMENFT) + copia con fondo transparente de Raging Earthquake
```

## Reglas de cartas y energía (revisión 02/11/2026)

- **Energía**: empieza en 3, +1 por turno, y **nunca supera 3**. Se pueden gastar hasta 3 por turno.
- **Unicidad total: 16 cartas, una por combinación ATK/DEF** — ataque 3/3, 3/2, 3/1, 3/0, 2/2, 2/1, 2/0, 1/1, 1/0, 0/0
  y su inverso defensivo 2/3, 1/3, 0/3, 1/2, 0/2, 0/1. Ninguna carta ni combinación se repite en el catálogo, el mazo
  (16 cartas = el set completo) ni la mano. 10 usan los artes de `CARDS/` (Raging Earthquake = 1/2) y 6 usan zorros NFT
  de `GAMENFT/`. No hay cartas especiales repetibles.
- **Cristales, acceso y modos**: ver "Economía y reglas de negocio" más abajo (reglas definitivas 10/11/2026).

## Cómo jugar en local

```bash
cd PVP
pip install -r requirements.txt
cd engine
streamlit run pvp_dashboard.py
```

Abre el enlace que imprime Streamlit (normalmente `http://localhost:8501`). La wallet viene simulada por defecto (3 NFTs falsos + 150 KUR) — puedes cambiar los token IDs y el balance en la barra lateral para probar distintas rarezas.

## Qué falta para integrarlo en el proyecto principal

- **Metadata/rareza real de los NFT**: ahora mismo `derive_placeholder_rarity()` en `blockchain_connector.py` inventa una rareza determinista a partir del token_id (no hay traits en esta carpeta). Sustituir por la fuente real (tokenURI, API propia, etc.) en cuanto exista.
- **Dirección del contrato NFT**: ver el aviso de arriba.
- **Arte propio para las cartas con zorro NFT**: si se dibuja arte para 2/1, 1/1, 1/0, 0/0, 2/3 o 3/2, basta con dejarlo en `CARDS/` y cambiar su `image_path` en `cards_catalog.py`.
- **Cristales reales**: `CRYSTAL_RARITIES` en `blockchain_connector.py` usa la rareza placeholder; sustituir por el dato real de cristales cuando exista.
- **Ejecución real de comisiones/recompensas en KUR**: `compute_match_economy()` solo calcula importes; moverlos on-chain debe hacerlo el backend con custodia de claves que ya tengáis (igual que hace kasman con su pool de KAS), nunca este conector de lectura.
- **Energía/turnos síncronos**: el documento de reglas tiene un par de puntos ambiguos (tope de gasto vs. acumulación de energía, exactamente cómo se resuelve "combo máximo") resueltos con un criterio explícito y comentado en `pvp_engine.py` — fácil de ajustar si el equipo de diseño quiso decir otra cosa.

## Economía y reglas de negocio (servidor, sin contratos) — `engine/economy_rules.py`

Libro mayor interno en Python puro (sin Streamlit ni web3): saldos $KUR / $KUSD, cristales, Elo,
cupo diario Free, histórico de combates, escrow de retos y comisión de la casa. Validado con
`python engine/test_economy_rules.py`. Todas las cifras son constantes al principio del módulo.
Reglas definitivas (10/11/2026):

| Regla | Dónde |
|---|---|
| **Acceso**: NFT **o** 300 combates totales (Free + Competitivo, histórico) → desbloquea Competitivo, depósitos y retiradas. Sin acceso solo quedan las 10 Free diarias (el camino hacia los 300) | `Account.access_unlocked` · `access_status(w)` · `deposit()` / `withdraw()` |
| **Free con cristales** (solo NFT): apuesta 1 💎 cada uno; gana → cristales en juego, +20 Elo, +3 $KUR; pierde → −10 Elo y su cristal; 60 s sin rival real → Bot (aporta el otro cristal) | `create_challenge(w, ChallengeKind.FREE)` (la variante la decide el NFT) · `process_expired()` · `report_result()` |
| **Free sin cristales** (solo SIN NFT): 10 al día (UTC), sin apuesta ni $KUR, ±10 Elo; 60 s sin rival real → Bot | ídem · `free_ranked_remaining(w)` |
| **Competitivo** ($KUR / $KUSD): ilimitado, sin cristales, escrow, ganador = bote − 10 %, empate = reembolso, ±25 Elo. **Cero bots**: a los 60 s la búsqueda se relanza (`Challenge.relaunches`) hasta que acepte un jugador real o el creador cancele | `create_challenge(w, ChallengeKind.MONEY, stake, Currency.KUR \| KUSD)` |
| Lista pública: cada cuenta ve solo los retos que puede aceptar (su variante de Free, o los competitivos) | `visible_challenges(viewer, kind=None)` |
| Cristales del runner / evolución de rareza 20·50·100 | `grant_crystals()` · `evolve_nft()` |
| Saldo traído de la wallet por el servidor (no es depósito del usuario, no pasa el control de acceso) | `sync_balance()` |

En el dashboard: panel de acceso (🔓/🔒 + progreso hacia 300) con **💼 Depositar / Retirar**, el
Competitivo y "🤝 Retos 1v1" van por el escrow del libro mayor, y en "🛠️ Configuración" hay un campo
de pruebas para fijar los combates jugados. Hotseat es práctica local sin economía. Como no hay
backend multijugador, los "jugadores reales" de la lista son cuentas simuladas (`sim:*`) cuyas
jugadas mueve el equipo local; aun así el libro mayor nunca asigna un bot en Competitivo.

### Minimarket (para Milton)

- Publicar: `list_item(vendedor, tipo, cantidad, precio_kusd)` — el ítem queda en escrow.
- Comprar: `buy_listing(comprador, listing_id)` — cobra $KUSD, la casa se queda el 10 % (`HOUSE_FEE_PCT`), el vendedor recibe el 90 %.
- Retirar: `cancel_listing(vendedor, listing_id)` · Listar: `open_listings(tipo)`.
- `crystals` viene registrado (cualquiera con cristales puede venderlos). Para un ítem nuevo:
  `register_item_type("skin", take=fn_quitar, give=fn_entregar, requires_unlock=True)` — con
  `requires_unlock=True` solo venden cuentas con NFT o con 300 combates.
- Comisiones acumuladas: `server.house` · auditoría de cada movimiento: `server.journal` · persistencia: `to_json()` / `EconomyServer.from_json()`.
