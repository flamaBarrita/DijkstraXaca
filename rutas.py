from flask import Flask, request, jsonify
from flask_cors import CORS
import osmnx as osmnx
import geopandas as geopandas
import matplotlib.pyplot as plt
import io, base64

# Crea el objeto Flask para la app web
app = Flask(__name__)
CORS(app)

# Algoritmo personalizado de Dijkstra para encontrar la ruta más rápida
def encontrar_ruta_optima(mapa, nodo_inicio, nodo_final):
    # Inicializamos las distancias y el historial de predecesores
    distancias = {lugar: float('inf') for lugar in mapa.nodes()}
    distancias[nodo_inicio] = 0
    historial_predecesores = {lugar: None for lugar in mapa.nodes()}

    nodos_sin_visitar = list(mapa.nodes())

    while nodos_sin_visitar:
        # Busca el nodo sin visitar con menor distancia acumulada
        nodo_actual = min(nodos_sin_visitar, key=lambda lugar: distancias[lugar])
        if nodo_actual == nodo_final:
            break
        nodos_sin_visitar.remove(nodo_actual)

        for vecino in mapa.neighbors(nodo_actual):
            if vecino not in nodos_sin_visitar:
                continue
            info_tramo = mapa.get_edge_data(nodo_actual, vecino)
            if not info_tramo:
                continue
            clave_tramo = list(info_tramo.keys())[0]
            tiempo_cruce = info_tramo[clave_tramo].get('tiempo_viaje', float('inf'))

            nueva_distancia = distancias[nodo_actual] + tiempo_cruce

            if nueva_distancia < distancias[vecino]:
                distancias[vecino] = nueva_distancia
                historial_predecesores[vecino] = nodo_actual

    # Reconstruye la ruta desde el nodo final al inicio
    ruta_optima = []
    nodo_actual = nodo_final
    while nodo_actual is not None:
        ruta_optima.insert(0, nodo_actual)
        nodo_actual = historial_predecesores[nodo_actual]
        if nodo_actual == nodo_inicio and nodo_inicio not in ruta_optima:
            ruta_optima.insert(0, nodo_actual)
            break

    # Valida si la ruta es válida
    if not ruta_optima or ruta_optima[0] != nodo_inicio:
        return None

    return ruta_optima

# Obtiene los detalles (distancia, tiempo, nombre de calle) de cada segmento de la ruta
def detalles_por_segmento(mapa, ruta):
    lista_segmentos = []
    metros_totales = 0
    segundos_totales = 0
    for i in range(len(ruta) - 1):
        origen = ruta[i]
        destino = ruta[i + 1]
        datos_tramo = mapa.get_edge_data(origen, destino)

        if datos_tramo:
            clave_tramo = list(datos_tramo.keys())[0]
            datos = datos_tramo[clave_tramo]
            metros = datos.get('length', 0)
            segundos = datos.get('tiempo_viaje', 0)
            metros_totales += metros
            segundos_totales += segundos
            nombre_via = datos.get('name', 'Sin nombre')
            
            # CORRECCIÓN: Usar los mismos nombres que espera el frontend
            segmento = {
                'segmento': i + 1,  # Cambiado de 'numero_segmento' a 'segmento'
                'calle': nombre_via if nombre_via else 'Sin nombre',  # Cambiado de 'nombre_calle' a 'calle'
                'distancia_metros': round(metros, 2),
                'tiempo_minutos': round(segundos / 60, 2)  # Cambiado de 'tiempo_segundos' a usar directamente minutos
            }
            lista_segmentos.append(segmento)    

    return lista_segmentos, metros_totales, segundos_totales

# Define el endpoint para calcular la ruta basándose en la ubicación del usuario
@app.route('/ruta', methods=['POST'])
def procesar_calculo_ruta():
    try:
        info_usuario = request.get_json()
        ubicacion_origen = (info_usuario['origen']['lat'], info_usuario['origen']['lng'])
        ubicacion_destino = (info_usuario['destino']['lat'], info_usuario['destino']['lng'])
        print(f"Origen: {ubicacion_origen}, Destino: {ubicacion_destino}")

        # Carga la red vial de Oaxaca y agrega los parques al mapa
        centro_mapa = (17.026351452600192, -96.73258533277694)
        parques = osmnx.features.features_from_point(centro_mapa, tags={'leisure': 'park'}, dist=15000)
        parques_gdf = geopandas.GeoDataFrame(parques)
        red_vial = osmnx.graph_from_point(centro_mapa, dist=15000, network_type='drive')
        red_vial = osmnx.routing.add_edge_speeds(red_vial)

        # Asigna el peso personalizado (tiempo de viaje estimado) a cada tramo de la red vial
        for inicio, final, clave, datos in red_vial.edges(keys=True, data=True):
            metros = datos.get('length', 1)
            limite_velocidad = datos.get('maxspeed', None)
            if isinstance(limite_velocidad, list):
                limite_velocidad = limite_velocidad[0]
            try:
                velocidad_kmh = float(limite_velocidad)
            except (TypeError, ValueError):
                velocidad_kmh = 20
            velocidad_ms = max(velocidad_kmh / 3.6, 1)
            datos['tiempo_viaje'] = metros / velocidad_ms

        # Buscar los nodos más cercanos a las ubicaciones dadas por el usuario
        lat_origen, lon_origen = ubicacion_origen
        lat_destino, lon_destino = ubicacion_destino
        try:
            nodo_inicio = osmnx.distance.nearest_nodes(red_vial, X=lon_origen, Y=lat_origen)
            nodo_final = osmnx.distance.nearest_nodes(red_vial, X=lon_destino, Y=lat_destino)
        except Exception:
            return jsonify({
                'error': 'Coordenadas fuera del área de cobertura del mapa. Por favor selecciona puntos cercanos a Oaxaca.'
            }), 400

        try:
            ruta_final = encontrar_ruta_optima(red_vial, nodo_inicio, nodo_final)
            if ruta_final is None:
                raise ValueError("No se encontró una ruta válida")
            print(f"Ruta calculada con {len(ruta_final)} nodos")
        except Exception as err:
            print(f"Error ruta óptima: {err}")
            return jsonify({
                'error': 'No se encontró una ruta entre los puntos seleccionados. Revisa si hay calles conectadas entre ambos lugares.'
            }), 404

        # Obtiene los detalles descriptivos de cada segmento de la ruta
        segmentos, metros_totales, segundos_totales = detalles_por_segmento(red_vial, ruta_final)

        resumen_ruta = {
            "distancia_total_m": round(metros_totales, 2),
            "tiempo_total_min": round(segundos_totales / 60, 2),
            "tiempo_total_seg": round(segundos_totales, 2),
            "segmentos_totales": len(ruta_final) - 1
        }

        # Genera el mapa y lo convierte a imagen base64
        fig, ax = osmnx.plot_graph_route(
            red_vial, ruta_final,
            node_size=0,
            bgcolor='white',
            edge_color='lightgray',
            route_color='red',
            route_linewidth=3,
            figsize=(10, 8),
            show=False,
            close=False
        )
        parques_gdf.plot(ax=ax, color='green', alpha=0.3)
        x = [red_vial.nodes[n]['x'] for n in ruta_final]
        y = [red_vial.nodes[n]['y'] for n in ruta_final]
        margen = 0.001
        ax.set_xlim(min(x) - margen, max(x) + margen)
        ax.set_ylim(min(y) - margen, max(y) + margen)
        ax.axis('off')
        ax.set_aspect('equal', 'box')
        buf = io.BytesIO()
        plt.tight_layout(pad=1.0)
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)

        # DEBUG: Imprimir la estructura de datos que se envía
        print("Estructura de segmentos enviada:")
        for i, segmento in enumerate(segmentos):
            print(f"Segmento {i+1}: {segmento}")

        return jsonify({
            'mensaje': 'Ruta calculada correctamente',
            'resumen': resumen_ruta,
            'segmentos': segmentos,
            'mapa': f"data:image/png;base64,{img_base64}"
        })

    except Exception as err:
        print("Error general:", str(err))
        return jsonify({
            'error': 'Ocurrió un error inesperado al calcular la ruta. Intenta nuevamente.',
            'detalle': str(err)
        }), 500

if __name__ == '__main__':
    app.run(debug=True)