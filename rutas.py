from flask import Flask, request, jsonify
from flask_cors import CORS
import osmnx as ox 
import geopandas as gpd
import matplotlib.pyplot as plt
import io, base64
import networkx as nx

app = Flask(__name__)
CORS(app)

@app.route('/ruta', methods=['POST'])
def calcular_ruta():
    try:
        data_user = request.get_json()
        #con la data recibida del usuario, extraemos las coordenadas
        coords_origen = (data_user['origen']['lat'], data_user['origen']['lng'])
        coords_destino = (data_user['destino']['lat'], data_user['destino']['lng'])
        print(f"Coordenadas recibidas - Origen: {coords_origen}, Destino: {coords_destino}")
        
        # Cargar red vial de Oaxaca
        punto_inicial = (17.026351452600192, -96.73258533277694)
        #tomamos un punto medio entre las localidades para mejorar la carga del grafo
        parques_oax = ox.features.features_from_point(punto_inicial, tags={'leisure': 'park'}, dist=15000)
        #añadimos los parques al mapa para darle una mejor perspectiva visual y de ubicación
        parques_oax_gdf = gpd.GeoDataFrame(parques_oax)
        #bajamos el grafo de la red vial
        G = ox.graph_from_point(punto_inicial, dist=15000, network_type='drive')
        #añadimos velocidades a las aristas
        G = ox.routing.add_edge_speeds(G)

        # Asignar peso personalizado
        for u, v, k, d in G.edges(keys=True, data=True):
            distancia = d.get('length', 1)

            # Obtener el valor de maxspeed (puede ser texto o lista)
            maxspeed = d.get('maxspeed', None)
            if isinstance(maxspeed, list):
                maxspeed = maxspeed[0]  # tomar el primer valor si es lista
            try:
                velocidad_kph = float(maxspeed)
            except (TypeError, ValueError):
                velocidad_kph = 20  # valor por defecto si no hay dato válido
                
            velocidad_ms = max(velocidad_kph / 3.6, 1)
            d['pesos_tiempo_viaje'] = distancia / velocidad_ms

        # Buscar nodos más cercanos
        #asiganamos las coordenadas recibidas a variables individuales (latitud y longitud)
        coords_origen_lat, coords_origen_lon = coords_origen
        coords_destino_lat, coords_destino_lon = coords_destino
        try:
            coords_origen_nodo = ox.distance.nearest_nodes(G, X=coords_origen_lon, Y=coords_origen_lat)
            coords_destino_nodo = ox.distance.nearest_nodes(G, X=coords_destino_lon, Y=coords_destino_lat)
        except Exception:
            return jsonify({
                #agragamos un mensaje si las coordenadas están fuera del área de cobertura
                'error': 'Coordenadas fuera del área de cobertura del mapa. Por favor selecciona puntos cercanos a Oaxaca.'
            }), 400

        # Calcular ruta
        try:
            ruta = ox.shortest_path(G, coords_origen_nodo, coords_destino_nodo, weight='pesos_tiempo_viaje')
            if ruta is None:
                raise ValueError("No se encontró una ruta válida")
        except (nx.NetworkXNoPath, ValueError):
            return jsonify({
                'error': 'No se encontró una ruta entre los puntos seleccionados. Revisa si hay calles conectadas entre ambos lugares.'
            }), 404

        # zip hace el manejo de tuplas para recorrer los nodos de la ruta y busca la distancia y el tiempo total
        distancia_total = sum(G.edges[u, v, 0].get('length', 0) for u, v in zip(ruta[:-1], ruta[1:]))
        tiempo_total = sum(G.edges[u, v, 0].get('pesos_tiempo_viaje', 0) for u, v in zip(ruta[:-1], ruta[1:]))
         #creamos un resumen de la ruta usando un diccionario
        resumen = {
            "distancia_total_m": round(distancia_total, 2),
            "tiempo_total_min": round(tiempo_total / 60, 2)
        }

        # Generar mapa reducido para mejor visualización
        fig, ax = ox.plot_graph_route(
            G, ruta,
            node_size=0,
            bgcolor='white',
            edge_color='lightgray',
            route_color='red',
            route_linewidth=3,
            figsize=(6, 6),
            show=False,
            close=False
        )
        #agregamos color de los parques al mapa
        parques_oax_gdf.plot(ax=ax, color='green', alpha=0.5)

        x = [G.nodes[n]['x'] for n in ruta]
        y = [G.nodes[n]['y'] for n in ruta]
        margen = 0.001
        ax.set_xlim(min(x) - margen, max(x) + margen)
        ax.set_ylim(min(y) - margen, max(y) + margen)
        ax.axis('off')
        ax.set_aspect('equal', 'box')


        # Convertir figura a Base64 para que se pueda enviar en JSON
        buf = io.BytesIO()
        plt.tight_layout(pad=0.5)
        plt.savefig(buf, format='png', dpi=200, bbox_inches=None)
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)

        return jsonify({
            'mensaje': 'Ruta calculada correctamente',
            'resumen': resumen,
            'mapa': f"data:image/png;base64,{img_base64}"
        })

    except Exception as e:
        print("Error general:", str(e))
        return jsonify({
            'error': 'Ocurrió un error inesperado al calcular la ruta. Intenta nuevamente.',
            'detalle': str(e)
        }), 500


if __name__ == '__main__':
    app.run(debug=True)
