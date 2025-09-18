from flask import Flask, jsonify, request
from flasgger import Swagger
from math import radians, sin, cos, asin, sqrt

app = Flask(__name__)

# Configuração mínima do Swagger
swagger_template = {
    "swagger": "2.0",
    "info": {
        "title": "API Secundária - MVP 2",
        "description": "Documentação da API Secundária (Flask + Flasgger).",
        "version": "1.0.0"
    },
    "basePath": "/",
}
Swagger(app, template=swagger_template)

@app.route("/health", methods=["GET"])
def health():
    """
    Verificar saúde da API Secundária
    ---
    tags:
      - Sistema
    responses:
      200:
        description: API operante
        schema:
          type: object
          properties:
            status:
              type: string
              example: ok
    """
    return jsonify({"status": "ok"}), 200

def haversine_km(lat1, lon1, lat2, lon2):
    """Calcula distância em km entre dois pontos (lat/lon) pela fórmula de Haversine."""
    R = 6371.0  # Raio médio da Terra em km
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return R * c

@app.route("/calcular-distancia", methods=["POST"])
def calcular_distancia():
    """
    Calcular distância entre duas coordenadas (Haversine)
    ---
    tags:
      - Distância
    consumes:
      - application/json
    parameters:
      - in: body
        name: payload
        required: true
        schema:
          type: object
          required:
            - origem
            - destino
          properties:
            origem:
              type: object
              required: [lat, lon]
              properties:
                lat:
                  type: number
                  example: -22.9068
                lon:
                  type: number
                  example: -43.1729
            destino:
              type: object
              required: [lat, lon]
              properties:
                lat:
                  type: number
                  example: -23.5505
                lon:
                  type: number
                  example: -46.6333
    responses:
      200:
        description: Distância calculada com sucesso
        schema:
          type: object
          properties:
            origem:
              type: object
            destino:
              type: object
            distancia_km:
              type: number
              example: 357.8
      400:
        description: Erro de validação ou payload inválido
    """
    try:
        data = request.get_json(silent=True) or {}
        origem = data.get("origem") or {}
        destino = data.get("destino") or {}

        for campo, bloco in (("origem", origem), ("destino", destino)):
            if not isinstance(bloco, dict):
                return {"erro": f"'{campo}' deve ser um objeto com lat e lon"}, 400
            if "lat" not in bloco or "lon" not in bloco:
                return {"erro": f"'{campo}' deve conter 'lat' e 'lon'"}, 400

        lat1 = float(origem["lat"])
        lon1 = float(origem["lon"])
        lat2 = float(destino["lat"])
        lon2 = float(destino["lon"])

        distancia = haversine_km(lat1, lon1, lat2, lon2)
        return {
            "origem": {"lat": lat1, "lon": lon1},
            "destino": {"lat": lat2, "lon": lon2},
            "distancia_km": round(distancia, 3)
        }, 200
    except (ValueError, TypeError):
        return {"erro": "Valores de latitude/longitude inválidos."}, 400
    except Exception as e:
        return {"erro": f"Erro inesperado: {str(e)}"}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
