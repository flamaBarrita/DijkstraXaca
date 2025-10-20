from flask import Flask, request, jsonify
from flask_cors import CORS
import osmnx as ox 
import geopandas as gpd
import matplotlib.pyplot as plt
import io, base64
import networkx as nx

app = Flask(__name__)
CORS(app)

def dijkstra_personalizado(grafo, inicial, destino):
    # Inicializar distancias y predecesores
    distancia = {nodo: float('inf') for nodo in grafo.nodes()}
    distancia[inicial] = 0
    lista_predecedores = {nodo: None for nodo in grafo.nodes()}
    
    # Lista de nodos no visitados
    lista_nodos_no_visitados = list(grafo.nodes())
    
    while lista_nodos_no_visitados:
        # Encontrar el nodo con la distancia mínima
        nodo_actual = min(lista_nodos_no_visitados, key=lambda nodo: distancia[nodo])
        
        # Si llegamos al destino, podemos terminar
        if nodo_actual == destino:
            break
            
        lista_nodos_no_visitados.remove(nodo_actual)
        
        # Explorar vecinos
        for vecino in grafo.neighbors(nodo_actual):
            if vecino not in lista_nodos_no_visitados:
                continue
                
            # Obtener el peso de la arista
            edge_data = grafo.get_edge_data(nodo_actual, vecino)
            if not edge_data:
                continue
                
            # Tomar el primer edge (puede haber múltiples entre los mismos nodos)
            first_key = list(edge_data.keys())[0]
            costo = edge_data[first_key].get('pesos_tiempo_viaje', float('inf'))
            
            # Calcular nueva distancia
            distancia_nueva = distancia[nodo_actual] + costo
            
            # Actualizar si encontramos un camino más corto
            if distancia_nueva < distancia[vecino]:
                distancia[vecino] = distancia_nueva
                lista_predecedores[vecino] = nodo_actual
    
    # Reconstruir la ruta
    ruta = []
    nodo_actual = destino
    
    while nodo_actual is not None:
        ruta.insert(0, nodo_actual)
        nodo_actual = lista_predecedores[nodo_actual]
        
        # Si no hay camino, retornar None
        if nodo_actual == inicial and inicial not in ruta:
            ruta.insert(0, nodo_actual)
            break
    
    # Verificar que la ruta es válida
    if not ruta or ruta[0] != inicial:
        return None
        
    return ruta

@app.route('/ruta', methods=['POST'])
def calcular_ruta():
    try:
        data_user = request.get_json()
        # Con la data recibida del usuario, extraemos las coordenadas
        coords_origen = (data_user['origen']['lat'], data_user['origen']['lng'])
        coords_destino = (data_user['destino']['lat'], data_user['destino']['lng'])
        print(f"Coordenadas recibidas - Origen: {coords_origen}, Destino: {coords_destino}")
        
        # Cargar red vial de Oaxaca
        punto_inicial = (17.026351452600192, -96.73258533277694)
        # Tomamos un punto medio entre las localidades para mejorar la carga del grafo
        parques_oax = ox.features.features_from_point(punto_inicial, tags={'leisure': 'park'}, dist=15000)
        # Añadimos los parques al mapa para darle una mejor perspectiva visual y de ubicación
        parques_oax_gdf = gpd.GeoDataFrame(parques_oax)
        # Bajamos el grafo de la red vial
        G = ox.graph_from_point(punto_inicial, dist=15000, network_type='drive')
        # Añadimos velocidades a las aristas
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
        # Asignamos las coordenadas recibidas a variables individuales (latitud y longitud)
        coords_origen_lat, coords_origen_lon = coords_origen
        coords_destino_lat, coords_destino_lon = coords_destino
        try:
            coords_origen_nodo = ox.distance.nearest_nodes(G, X=coords_origen_lon, Y=coords_origen_lat)
            coords_destino_nodo = ox.distance.nearest_nodes(G, X=coords_destino_lon, Y=coords_destino_lat)
        except Exception:
            return jsonify({
                # Agregamos un mensaje si las coordenadas están fuera del área de cobertura
                'error': 'Coordenadas fuera del área de cobertura del mapa. Por favor selecciona puntos cercanos a Oaxaca.'
            }), 400

        # Calcular ruta usando Dijkstra personalizado
        try:
            ruta = dijkstra_personalizado(G, coords_origen_nodo, coords_destino_nodo)
            if ruta is None:
                raise ValueError("No se encontró una ruta válida")
        except Exception as e:
            print(f"Error en Dijkstra personalizado: {e}")
            return jsonify({
                'error': 'No se encontró una ruta entre los puntos seleccionados. Revisa si hay calles conectadas entre ambos lugares.'
            }), 404

        # Calcular distancia y tiempo total
        distancia_total = 0
        tiempo_total = 0
        
        for i in range(len(ruta) - 1):
            u = ruta[i]
            v = ruta[i + 1]
            edge_data = G.get_edge_data(u, v)
            if edge_data:
                first_key = list(edge_data.keys())[0]
                distancia_total += edge_data[first_key].get('length', 0)
                tiempo_total += edge_data[first_key].get('pesos_tiempo_viaje', 0)

        # Crear un resumen de la ruta usando un diccionario
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
        # Agregar color de los parques al mapa
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
            'mensaje': 'Ruta calculada correctamente con Dijkstra personalizado',
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