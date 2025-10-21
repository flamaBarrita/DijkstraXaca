from flask import Flask, request, jsonify
from flask_cors import CORS
import osmnx as osmnx
import geopandas as geopandas
import matplotlib.pyplot as plt
import io, base64

# Se crea una instancia de Flask que servirá como backend del servicio web
app = Flask(__name__)
# Se habilita CORS para permitir solicitudes desde distintos dominios (por ejemplo, un frontend en React o Vue)
CORS(app)

# Función: encontrar_ruta_optima
# Algoritmo para encontrar la ruta más rápida entre dos nodos.

def encontrar_ruta_optima(mapa, nodo_inicio, nodo_final):
    # Inicializamos un diccionario con las distancias más cortas conocidas desde el nodo de inicio
    distancias = {lugar: float('inf') for lugar in mapa.nodes()}
    distancias[nodo_inicio] = 0  # la distancia inicial es 0 para el punto de partida

    # Se guarda el nodo predecesor que permite reconstruir la ruta posteriormente
    historial_predecesores = {lugar: None for lugar in mapa.nodes()}

    # Lista de nodos que faltan por visitar
    nodos_sin_visitar = list(mapa.nodes())

    while nodos_sin_visitar:
        # Se elige el nodo más cercano (con menor distancia acumulada)
        nodo_actual = min(nodos_sin_visitar, key=lambda lugar: distancias[lugar])
        if nodo_actual == nodo_final:
            # Si llegamos al destino, se detiene la búsqueda
            break
        nodos_sin_visitar.remove(nodo_actual)

        # Se recorren los vecinos del nodo actual para actualizar sus distancias
        for vecino in mapa.neighbors(nodo_actual):
            if vecino not in nodos_sin_visitar:
                continue
            info_tramo = mapa.get_edge_data(nodo_actual, vecino)
            if not info_tramo:
                continue
            clave_tramo = list(info_tramo.keys())[0]
            tiempo_cruce = info_tramo[clave_tramo].get('tiempo_viaje', float('inf'))

            # Cálculo de nueva distancia considerando el tiempo de viaje como peso
            nueva_distancia = distancias[nodo_actual] + tiempo_cruce

            # Si encontramos una mejor ruta, actualizamos la distancia y el predecesor
            if nueva_distancia < distancias[vecino]:
                distancias[vecino] = nueva_distancia
                historial_predecesores[vecino] = nodo_actual

    # Reconstruimos la ruta desde el nodo final al inicial usando el historial
    ruta_optima = []
    nodo_actual = nodo_final
    while nodo_actual is not None:
        ruta_optima.insert(0, nodo_actual)
        nodo_actual = historial_predecesores[nodo_actual]
        if nodo_actual == nodo_inicio and nodo_inicio not in ruta_optima:
            ruta_optima.insert(0, nodo_actual)
            break

    # Validamos si la ruta reconstruida es válida
    if not ruta_optima or ruta_optima[0] != nodo_inicio:
        return None

    return ruta_optima



# Función: detalles_por_segmento
# Obtiene información detallada sobre cada tramo de la ruta, incluyendo nombre de calle,

def detalles_por_segmento(mapa, ruta):
    lista_segmentos = []
    metros_totales = 0
    segundos_totales = 0
    for i in range(len(ruta) - 1):
        origen = ruta[i]
        destino = ruta[i + 1]
        datos_tramo = mapa.get_edge_data(origen, destino)
        #para cada tramo, obtenemos la información relevante
        if datos_tramo:
            clave_tramo = list(datos_tramo.keys())[0]
            datos = datos_tramo[clave_tramo]
            metros = datos.get('length', 0)
            segundos = datos.get('tiempo_viaje', 0)
            metros_totales += metros
            segundos_totales += segundos
            nombre_via = datos.get('name', 'Sin nombre')

            # Se construye un diccionario con la información que el frontend espera
            segmento = {
                'segmento': i + 1,
                'calle': nombre_via if nombre_via else 'Sin nombre',
                'distancia_metros': round(metros, 2),
                'tiempo_minutos': round(segundos / 60, 2)
            }
            lista_segmentos.append(segmento)

    return lista_segmentos, metros_totales, segundos_totales


# Endpoint: /ruta (POST)
# Recibe coordenadas de origen y destino, calcula la ruta óptima y devuelve resultados.

@app.route('/ruta', methods=['POST'])
def procesar_calculo_ruta():
    try:
        # Se extraen los datos del cuerpo de la solicitud enviada por el frontend
        info_usuario = request.get_json()
        ubicacion_origen = (info_usuario['origen']['lat'], info_usuario['origen']['lng'])
        ubicacion_destino = (info_usuario['destino']['lat'], info_usuario['destino']['lng'])
        print(f"Origen: {ubicacion_origen}, Destino: {ubicacion_destino}")

        # Se carga el mapa vial de Oaxaca y los parques cercanos
        centro_mapa = (17.026351452600192, -96.73258533277694)
        #bajamos los parques en un radio de 15 km
        parques = osmnx.features.features_from_point(centro_mapa, tags={'leisure': 'park'}, dist=15000)
        parques_gdf = geopandas.GeoDataFrame(parques)
        #bajamos la red vial con 15 km de radio
        red_vial = osmnx.graph_from_point(centro_mapa, dist=15000, network_type='drive')
        red_vial = osmnx.routing.add_edge_speeds(red_vial)

        # Calcula el tiempo de viaje basado en longitud y velocidad máxima del tramo
        for inicio, final, clave, datos in red_vial.edges(keys=True, data=True):
            metros = datos.get('length', 1)
            limite_velocidad = datos.get('maxspeed', None)
            if isinstance(limite_velocidad, list):
                limite_velocidad = limite_velocidad[0]
            try:
                velocidad_kmh = float(limite_velocidad)
            except (TypeError, ValueError):
                velocidad_kmh = 20  # valor por defecto
            velocidad_ms = max(velocidad_kmh / 3.6, 1)
            datos['tiempo_viaje'] = metros / velocidad_ms

        # Localiza los nodos más cercanos al origen y destino definidos por el usuario
        lat_origen, lon_origen = ubicacion_origen
        lat_destino, lon_destino = ubicacion_destino
        try:
            nodo_inicio = osmnx.distance.nearest_nodes(red_vial, X=lon_origen, Y=lat_origen)
            nodo_final = osmnx.distance.nearest_nodes(red_vial, X=lon_destino, Y=lat_destino)
        except Exception:
            return jsonify({'error': 'Coordenadas fuera de cobertura. Selecciona puntos dentro del área de Oaxaca.'}), 400

        # Calcula la ruta más eficiente con el algoritmo personalizado
        try:
            ruta_final = encontrar_ruta_optima(red_vial, nodo_inicio, nodo_final)
            if ruta_final is None:
                raise ValueError("No se encontró una ruta válida")
            print(f"Ruta calculada con {len(ruta_final)} nodos")
        except Exception as err:
            print(f"Error ruta óptima: {err}")
            return jsonify({'error': 'No se encontró una ruta entre los puntos seleccionados.'}), 404

        # Obtiene los detalles descriptivos de la ruta final
        segmentos, metros_totales, segundos_totales = detalles_por_segmento(red_vial, ruta_final)

        resumen_ruta = {
            'distancia_total_m': round(metros_totales, 2),
            'tiempo_total_min': round(segundos_totales / 60, 2),
            'tiempo_total_seg': round(segundos_totales, 2),
            'segmentos_totales': len(ruta_final) - 1
        }

        # Dibuja la ruta sobre el mapa y la convierte en imagen base64 para enviarlo al frontend
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
        ax.set_xlim(min(x) - 0.001, max(x) + 0.001)
        ax.set_ylim(min(y) - 0.001, max(y) + 0.001)
        ax.axis('off')
        buf = io.BytesIO()
        plt.tight_layout(pad=1.0)
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)

        # Imprime en consola una vista previa del resultado para depuración
        print("Estructura de segmentos enviada:")
        for i, segmento in enumerate(segmentos):
            print(f"Segmento {i+1}: {segmento}")

        # Devuelve respuesta JSON con resumen, segmentos y mapa visual
        return jsonify({
            'mensaje': 'Ruta calculada correctamente',
            'resumen': resumen_ruta,
            'segmentos': segmentos,
            'mapa': f"data:image/png;base64,{img_base64}"
        })

    except Exception as err:
        # Manejo general de errores no previstos
        print("Error general:", str(err))
        return jsonify({'error': 'Error inesperado al calcular la ruta.', 'detalle': str(err)}), 500


# Punto de entrada principal: ejecuta la aplicación en modo debug
if __name__ == '__main__':
    app.run(debug=True)
