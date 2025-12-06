# app.py
import os
import json
import googlemaps
import streamlit as st
import streamlit.components.v1 as components
from itertools import islice

st.set_page_config(page_title="Trajets Victoire → Gare Centrale (Option A)", layout="wide")

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
if not API_KEY:
    st.error("ERREUR: définissez la variable d'environnement GOOGLE_MAPS_API_KEY avant de lancer l'app.")
    st.stop()

gmaps = googlemaps.Client(key=API_KEY)

st.title("Trajets — Rond-point Victoire → Gare Centrale (Option A)")
st.markdown(
    """
    Cette app construit **tous les chemins simples** (sans répétition d'arrêt) entre Victoire et Gare Centrale
    à partir d'un graphe d'adjacence défini. Chaque chemin est transformé en requête Google Directions (waypoints = noeuds intermédiaires).
    Cliquez sur une route sur la carte pour voir distance / durée.
    """
)

# -------------------------
# 1) Définition des arrêts
# -------------------------
# Liste principale d'arrêts (nœuds) 
STOPS = [
    "Rond-point Victoire, Kinshasa, DRC",                     # START
    "Marché Central, Kinshasa, DRC",
    "Avenue du 30 Juin & Boulevard du 30 Juin, Kinshasa",
    "Hôpital Général, Kinshasa, DRC",
    "Gare Centrale, Kinshasa, DRC"                            # END
]

START = STOPS[0]
END = STOPS[-1]


# 2) Graphe d'adjacence (orienté non-orienté logique)
#    — définit quels arrêts sont "connectés" directement.
#    IMPORTANT : ce graphe contrôle les chemins possibles.

ADJ = {
    "Rond-point Victoire, Kinshasa, DRC": [
        "Marché Central, Kinshasa, DRC",
        "Avenue du 30 Juin & Boulevard du 30 Juin, Kinshasa"
    ],
    "Marché Central, Kinshasa, DRC": [
        "Avenue du 30 Juin & Boulevard du 30 Juin, Kinshasa",
        "Hôpital Général, Kinshasa, DRC",
        "Gare Centrale, Kinshasa, DRC"
    ],
    "Avenue du 30 Juin & Boulevard du 30 Juin, Kinshasa": [
        "Hôpital Général, Kinshasa, DRC",
        "Gare Centrale, Kinshasa, DRC"
    ],
    "Hôpital Général, Kinshasa, DRC": [
        "Gare Centrale, Kinshasa, DRC"
    ],
    "Gare Centrale, Kinshasa, DRC": []
}

# -------------------------
# 3) Énumération des chemins simples (DFS)

def simple_paths(start, end, adj, max_paths=50):
    """Génère tous les chemins simples de start à end sans répétition.
       Limite pratique `max_paths` pour éviter des appels API massifs."""
    stack = [(start, [start])]
    paths = []
    while stack:
        (node, path) = stack.pop()
        if node == end:
            paths.append(list(path))
            if len(paths) >= max_paths:
                break
            continue
        for nbr in adj.get(node, []):
            if nbr not in path:
                stack.append((nbr, path + [nbr]))
    return paths

# on limite par défaut pour la sécurité ; mais avec notre graphe on aura peu de chemins
MAX_ROUTES = 12
paths = simple_paths(START, END, ADJ, max_paths=MAX_ROUTES)

if not paths:
    st.warning("Aucun chemin trouvé entre START et END avec la topologie actuelle.")
    st.stop()

# Afficher les chemins trouvés
st.sidebar.header("Paramètres")
st.sidebar.write(f"{len(paths)} chemin(s) simple(s) trouvé(s). (limite {MAX_ROUTES})")
# Multiselect pour choisir quelles routes afficher
default_selected = list(range(len(paths)))  # par défaut montrer tous
route_options = {i: "  →  ".join([p.split(",")[0] for p in path]) for i, path in enumerate(paths)}
selected = st.sidebar.multiselect(
    "Sélectionner les trajets à afficher (par index)",
    options=list(route_options.keys()),
    format_func=lambda i: f"#{i+1} : {route_options[i]}",
    default=default_selected
)

# Option pour limiter l'usage API / forcer refresh
use_cache = st.sidebar.checkbox("Utiliser le cache (recommandé)", value=True)
if not use_cache:
    # on peut forcer la suppression du cache en redémarrant l'app manuellement
    st.sidebar.info("Désactiver le cache peut augmenter les appels API et ralentir l'app.")

# -------------------------
# 4) Géocodage des arrêts (cache léger)
# -------------------------
@st.cache_data(show_spinner=False)
def geocode_addresses(addresses):
    coords = {}
    for a in addresses:
        try:
            res = gmaps.geocode(a)
            if res:
                loc = res[0]["geometry"]["location"]
                coords[a] = {"lat": loc["lat"], "lng": loc["lng"]}
            else:
                coords[a] = None
        except Exception as e:
            coords[a] = None
    return coords

coords = geocode_addresses(STOPS)

# -------------------------
# 5) Obtenir Directions pour chaque chemin (waypoints = internes)
# -------------------------
@st.cache_data(show_spinner=False)
def get_routes_for_paths(paths_list):
    routes = []
    for path in paths_list:
        # waypoints = nodes between start and end (excluded)
        waypoints = [node for node in path[1:-1]]
        try:
            directions = gmaps.directions(
                origin=path[0],
                destination=path[-1],
                mode="driving",
                waypoints=waypoints if waypoints else None,
                departure_time="now",
                alternatives=False
            )
        except Exception as e:
            directions = None

        if not directions:
            # on met une entrée vide mais continue : l'app ne casse pas
            routes.append({
                "path": path,
                "waypoints": waypoints,
                "poly": None,
                "distance_m": None,
                "duration_s": None,
                "error": True
            })
            continue

        overview_poly = directions[0].get("overview_polyline", {}).get("points")
        distance = sum(leg.get("distance", {}).get("value", 0) for leg in directions[0].get("legs", []))
        duration = sum(leg.get("duration", {}).get("value", 0) for leg in directions[0].get("legs", []))

        routes.append({
            "path": path,
            "waypoints": waypoints,
            "poly": overview_poly,
            "distance_m": distance,
            "duration_s": duration,
            "error": False
        })
    return routes

# Récupérer (pense à la limite d'appels API)
routes_all = get_routes_for_paths(paths)

# -------------------------
# 6) Coloration et préparation HTML
#    - on ordonne par nombre d'étapes pour choix de couleurs cohérent
# -------------------------
# palette simple
PALETTE = ["blue", "yellow", "purple", "orange", "green", "cyan", "magenta"]

# Ordre par nombre d'intermédiaires (moins d'arrêts => priorité bleu)
order = sorted(range(len(routes_all)), key=lambda i: len(routes_all[i]["path"]))
colors_per_index = [None] * len(routes_all)
for rank, idx in enumerate(order):
    colors_per_index[idx] = PALETTE[rank % len(PALETTE)]

html_routes = []
for i, r in enumerate(routes_all):
    html_routes.append({
        "poly": r["poly"],
        "stops_count": len(r["path"]) - 2,  # intermédiaires
        "distance_m": r["distance_m"],
        "duration_s": r["duration_s"],
        "waypoints": r["waypoints"],
        "path": r["path"],
        "color": colors_per_index[i],
        "error": r["error"]
    })

# Markers: marquer tous les arrêts utilisés
major_stop_icons = {}
for r in html_routes:
    for w in r["waypoints"]:
        major_stop_icons[w] = True

markers = []
for s in STOPS:
    markers.append({
        "title": s,
        "coord": coords.get(s),
        "is_major": bool(major_stop_icons.get(s, False)),
        "is_end": (s == END),
        "is_start": (s == START)
    })

# Filtrer pour afficher seulement les routes sélectionnées
routes_to_show = [html_routes[i] for i in selected]

# -------------------------
# 7) Construire l'HTML/JS pour la map (même logique qu'avant)
# -------------------------
html_template = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Trajets Victoire → Gare Centrale (Option A)</title>
  <style>
    html, body, #map {{ height: 100%; margin: 0; padding: 0; }}
    #map {{ height: 100vh; width: 100%; }}
    .legend {{
      background: white;
      padding: 10px;
      font-size: 13px;
      box-shadow: 0 2px 6px rgba(0,0,0,0.3);
      margin: 10px;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="legend" class="legend"></div>

  <script>
    const ROUTES = {json.dumps(routes_to_show)};
    const MARKERS = {json.dumps(markers)};

    function initMap() {{
      const startObj = MARKERS.find(m => m.is_start);
      const start = startObj && startObj.coord ? startObj.coord : {{lat: -4.0, lng: 15.3}};
      const map = new google.maps.Map(document.getElementById('map'), {{
        zoom: 13,
        center: start,
        mapTypeId: 'roadmap'
      }});

      const bounds = new google.maps.LatLngBounds();
      const destMarker = MARKERS.find(m => m.is_end && m.coord);
      const destLatLng = destMarker ? new google.maps.LatLng(destMarker.coord.lat, destMarker.coord.lng) : null;

      ROUTES.forEach((r, idx) => {{
        if (!r.poly || r.error) return; // skip erreurs
        const path = google.maps.geometry.encoding.decodePath(r.poly || "");
        const latLngs = path.map(p => new google.maps.LatLng(p.lat(), p.lng()));

        // Trim near destination to avoid tiny overshoots
        let usedLatLngs = latLngs;
        if (destLatLng && latLngs.length > 0) {{
          let minIdx = latLngs.length - 1;
          let minDist = Infinity;
          for (let i = 0; i < latLngs.length; i++) {{
            const d = google.maps.geometry.spherical.computeDistanceBetween(latLngs[i], destLatLng);
            if (d < minDist) {{ minDist = d; minIdx = i; }}
          }}
          usedLatLngs = latLngs.slice(0, Math.min(minIdx + 1, latLngs.length));
          const last = usedLatLngs[usedLatLngs.length - 1];
          if (last && google.maps.geometry.spherical.computeDistanceBetween(last, destLatLng) > 5) {{
            usedLatLngs.push(destLatLng);
          }}
        }}

        const line = new google.maps.Polyline({{
          path: usedLatLngs,
          strokeColor: r.color,
          strokeOpacity: 0.95,
          strokeWeight: (r.color === 'blue') ? 8 : 6,
          map: map
        }});

        const info = new google.maps.InfoWindow();
        line.addListener('click', (ev) => {{
          const content = `<div style="min-width:180px"><b>Trajet ${{idx+1}}</b><br/>Chemin: ${{r.path.map(p=>p.split(',')[0]).join(' → ')}}<br/>Arrêts intermédiaires: ${{r.stops_count}}<br/>Distance: ${{r.distance_m ? Math.round(r.distance_m) + ' m' : 'N/A'}}<br/>Durée: ${{r.duration_s ? Math.round(r.duration_s) + ' s' : 'N/A'}}</div>`;
          info.setContent(content);
          info.setPosition(ev.latLng);
          info.open(map);
        }});

        usedLatLngs.forEach(p => bounds.extend(p));
      }});

      // markers
      MARKERS.forEach(m => {{
        if (!m.coord) return;
        let icon = null;
        if (m.is_end) icon = 'http://maps.google.com/mapfiles/ms/icons/green-dot.png';
        else if (m.is_start) icon = 'http://maps.google.com/mapfiles/ms/icons/blue-dot.png';
        else if (m.is_major) icon = 'http://maps.google.com/mapfiles/ms/icons/black-dot.png';
        else icon = 'http://maps.google.com/mapfiles/ms/icons/red-dot.png';

        const marker = new google.maps.Marker({{
          position: m.coord,
          map: map,
          title: m.title,
          icon: icon
        }});

        const iw = new google.maps.InfoWindow({{ content: `<div><b>${{m.title}}</b></div>` }});
        marker.addListener('click', () => iw.open(map, marker));
        bounds.extend(new google.maps.LatLng(m.coord.lat, m.coord.lng));
      }});

      map.fitBounds(bounds);

      const legend = document.getElementById('legend');
      let legendHtml = `<div><b>Légende</b></div>
        <div><img src="http://maps.google.com/mapfiles/ms/icons/blue-dot.png"> Départ (Victoire)</div>
        <div><img src="http://maps.google.com/mapfiles/ms/icons/green-dot.png"> Arrivée (Gare Centrale)</div>
        <div><img src="http://maps.google.com/mapfiles/ms/icons/black-dot.png"> Grand arrêt (intermédiaire fréquent)</div>
        <div style="margin-top:6px"><b>Routes affichées:</b></div>`;
      ROUTES.forEach((r, idx) => {{
        legendHtml += `<div style="margin-top:4px"><span style="display:inline-block;width:18px;height:8px;background:${{r.color}};margin-right:8px;border:1px solid #222"></span> Trajet ${{idx+1}} — ${{r.path.map(p=>p.split(',')[0]).join(' → ')}}</div>`;
      }});
      legend.innerHTML = legendHtml;
      map.controls[google.maps.ControlPosition.RIGHT_TOP].push(legend);
    }}
  </script>

  <script async src="https://maps.googleapis.com/maps/api/js?key={API_KEY}&libraries=geometry&callback=initMap"></script>
</body>
</html>
"""

# -------------------------
# 8) Affichage Streamlit :
#    - résumé latéral des routes (distance/durée)
#    - rendu HTML
# -------------------------
st.header("Résumé des trajets sélectionnés")
if not selected:
    st.info("Sélectionnez au moins un trajet dans la barre latérale pour l'afficher.")
else:
    for idx in selected:
        r = html_routes[idx]
        st.markdown(f"**Trajet #{idx+1}** — { ' → '.join([p.split(',')[0] for p in r['path']]) }")
        if r["error"] or (r["distance_m"] is None):
            st.write("  - ❗ Erreur lors de la récupération du trajet (ou données indisponibles).")
        else:
            st.write(f"  - Arrêts intermédiaires : {r['stops_count']}")
            st.write(f"  - Distance : {round(r['distance_m'])} m")
            st.write(f"  - Durée : {round(r['duration_s'])} s")
        st.write("---")

st.markdown("**Carte (interagissez avec les trajets)**")
components.html(html_template, height=720, scrolling=True)
