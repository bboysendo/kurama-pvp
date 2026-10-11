# Kurama Run — Motor PVP (prototipo desacoplado)

Prototipo local del sistema de combate PVP, basado en `PVP_Rules/PVP KURAMARUN....md`.
Pensado para jugar y probar en local, y para integrarse luego en el proyecto principal sin arrastrar nada de este repo salvo lo que haga falta.

## ⚠️ Antes de nada: revisa la dirección del contrato NFT

La dirección que me diste para el NFT (`0x8331e28e7818d72ee58dfcabd7f8b71afe143f06`) **no es la colección real de Kurama Run** — está desplegada en BSC, pero su `symbol()` on-chain devuelve `"Reward at [5000usd.us]"`, que es la firma típica de un contrato de phishing (de los que mandan transferencias basura a tu wallet para que aparezcan en tu historial y piques en la URL). Es decir: la dirección correcta de tu colección "Genesis Founders" (1600 fox, mint.kuramarun.com) NO es esa. El token `$KUR` sí cuadra (`name="Kurama"`, `symbol="KRM"`, 9 decimales, 10M de supply).

El engine funciona igual mientras tanto (`NFT_CONTRACT_ADDRESS` en `engine/blockchain_connector.py` es una única constante), pero **no la uses contra la BSC real hasta confirmar la dirección correcta** (búscala en vuestro propio deploy, o en BscScan verificando el contrato de `mint.kuramarun.com`).

## Estructura

```
PVP/
  CARDS/                 18 artes de carta ya terminados (PNG transparente; 6 Kuramarun + 12 Talon Zhen)
                           CARDS_NO_USE/  versiones antiguas/descartadas — el catálogo nunca las usa
  GAMENFT/                1600 imágenes fox_<id>.png del NFT (sin metadata de rareza)
  PVP_Rules/              documento de diseño original (fuente de verdad de las reglas)
  Kuramarun_Whitepaper.htm  cascarón HTML vacío (Next.js) — no trae contenido real
  requirements.txt
  engine/
    card_schema.py         estructura de datos de una carta + constantes de reglas
    cards_catalog.py        genera cards_database.json (27 cartas + 2 legendarias) y los placeholders
    cards_database.json     catálogo generado (ejecutar cards_catalog.py para regenerarlo)
    blockchain_connector.py  lectura Web3/BSC — con modo SIMULACIÓN automático si no hay web3/RPC
    pvp_engine.py            motor de combate puro (start_match/play_card/end_turn/get_match_state)
    pvp_dashboard.py          panel Streamlit para jugar y probar (incluye modo ⚔️ Batalla y 🎓 Tutorial Interactivo)
    test_pvp_engine_manual.py  batería de escenarios del motor (daño, poción, perforante, contraataque, combos, energía, victoria)
    assets/placeholders/      cartas generadas para los huecos sin arte final
```

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
- **Arte final de las 16 cartas placeholder**: están listadas en `cards_catalog.py` (`PLACEHOLDER_SPECS`) con sus stats ya balanceadas; solo hace falta sustituir la imagen generada por el arte definitivo y actualizar `image_path`.
- **Ejecución real de comisiones/recompensas en KUR**: `compute_match_economy()` solo calcula importes; moverlos on-chain debe hacerlo el backend con custodia de claves que ya tengáis (igual que hace kasman con su pool de KAS), nunca este conector de lectura.
- **Energía/turnos síncronos**: el documento de reglas tiene un par de puntos ambiguos (tope de gasto vs. acumulación de energía, exactamente cómo se resuelve "combo máximo") resueltos con un criterio explícito y comentado en `pvp_engine.py` — fácil de ajustar si el equipo de diseño quiso decir otra cosa.
