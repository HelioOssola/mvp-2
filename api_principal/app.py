import os
import sqlite3
from datetime import datetime
from typing import Tuple

from flask import Flask, jsonify, request, g
from flasgger import Swagger
import requests

# ---------------------------------------------
# Configurações básicas
# ---------------------------------------------
app = Flask(__name__)

swagger_template = {
    "swagger": "2.0",
    "info": {
        "title": "API Principal - MVP 2",
        "description": "Recebe CEPs, consulta ViaCEP, geocodifica (Nominatim/OSM), delega cálculo à API Secundária e persiste resultados em SQLite.",
        "version": "1.0.0"
    },
    "basePath": "/",
}
Swagger(app, template=swagger_template)

# URL da API Secundária (pode ser alterada por variável de ambiente)
API_SECUNDARIA_URL = os.getenv("API_SECUNDARIA_URL", "http://127.0.0.1:5001")
# Caminho do banco SQLite
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "mvp2.db"))

# ---------------------------------------------
# Camada de persistência (SQLite)
# ---------------------------------------------
def get_db() -> sqlite3.Connection:
    """Obtém conexão SQLite por request (thread-safe no contexto Flask)."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    """Fecha conexão ao final do request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    """Cria a tabela se não existir."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS consultas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cep_origem TEXT NOT NULL,
                cep_destino TEXT NOT NULL,
                lat1 REAL NOT NULL,
                lon1 REAL NOT NULL,
                lat2 REAL NOT NULL,
                lon2 REAL NOT NULL,
                distancia_km REAL NOT NULL,
                criado_em TEXT NOT NULL,
                observacoes TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

# Inicializa o banco no startup
init_db()

# ---------------------------------------------
# Utilitários externos (ViaCEP + Nominatim)
# ---------------------------------------------
def via_cep(cep: str) -> dict:
    """Consulta ViaCEP e retorna JSON do endereço. Lança ValueError se inválido."""
    cep = cep.replace("-", "").strip()
    url = f"https://viacep.com.br/ws/{cep}/json/"
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        raise ValueError(f"ViaCEP HTTP {r.status_code}")
    data = r.json()
    if data.get("erro"):
        raise ValueError("CEP inválido no ViaCEP")
    return data

def endereco_para_query(endereco: dict) -> str:
    """Monta uma query textual amigável para geocodificação."""
    logradouro = endereco.get("logradouro") or ""
    bairro = endereco.get("bairro") or ""
    localidade = endereco.get("localidade") or ""
    uf = endereco.get("uf") or ""
    componentes = [logradouro, bairro, localidade, uf, "Brazil"]
    return ", ".join([c for c in componentes if c])

def geocode_osm(query: str) -> Tuple[float, float]:
    """
    Geocodifica um endereço via Nominatim/OSM.
    Retorna (lat, lon) ou lança ValueError se não encontrar.
    """
    headers = {
        "User-Agent": "MVP2-CEP-Distancia/1.0 (contato-exemplo@exemplo.com)"
    }

    def _search(q: str):
        params = {"q": q, "format": "json", "limit": 1, "addressdetails": 0, "countrycodes": "br"}
        resp = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        items = resp.json()
        if not items:
            return None
        return float(items[0]["lat"]), float(items[0]["lon"])

    # 1ª tentativa: query completa
    coords = _search(query)
    if coords:
        return coords

    # 2ª tentativa: heurística simples (cidade/UF/país)
    partes = [p.strip() for p in query.split(",")]
    if len(partes) >= 3:
        cidade_uf = ", ".join(partes[-3:])
        coords = _search(cidade_uf)
        if coords:
            return coords

    raise ValueError("Não foi possível geocodificar o endereço informado.")

# ---------------------------------------------
# Rotas
# ---------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    """
    Verificar saúde da API Principal
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

@app.route("/distancia-por-cep", methods=["POST"])
def distancia_por_cep():
    """
    Calcular distância entre dois CEPs (ViaCEP + Nominatim + API Secundária) e persistir resultado
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
              type: string
              example: "01001-000"
            destino:
              type: string
              example: "20040-020"
            observacoes:
              type: string
              example: "Teste de demonstração"
    responses:
      200:
        description: Distância calculada e registrada com sucesso
        schema:
          type: object
          properties:
            id:
              type: integer
              example: 1
            cep_origem:
              type: string
            cep_destino:
              type: string
            origem:
              type: object
            destino:
              type: object
            distancia_km:
              type: number
            criado_em:
              type: string
            observacoes:
              type: string
      400:
        description: Erro de validação ou CEP inválido
      502:
        description: Falha ao consultar serviços externos
    """
    try:
        body = request.get_json(silent=True) or {}
        cep_origem = str(body.get("origem", "")).strip()
        cep_destino = str(body.get("destino", "")).strip()
        observacoes = body.get("observacoes")

        if not cep_origem or not cep_destino:
            return {"erro": "Informe 'origem' e 'destino' (CEPs)."}, 400

        # 1) Consulta ViaCEP
        end_origem = via_cep(cep_origem)
        end_destino = via_cep(cep_destino)

        # 2) Geocodificação (Nominatim)
        q_origem = endereco_para_query(end_origem)
        q_destino = endereco_para_query(end_destino)
        lat1, lon1 = geocode_osm(q_origem)
        lat2, lon2 = geocode_osm(q_destino)

        # 3) Chama API Secundária (Haversine)
        payload = {
            "origem": {"lat": lat1, "lon": lon1},
            "destino": {"lat": lat2, "lon": lon2}
        }
        r = requests.post(f"{API_SECUNDARIA_URL.rstrip('/')}/calcular-distancia", json=payload, timeout=15)
        if r.status_code != 200:
            return {"erro": f"Falha ao calcular distância na API Secundária: HTTP {r.status_code}",
                    "detalhes": r.text}, 502
        resultado = r.json()
        distancia_km = float(resultado.get("distancia_km"))

        # 4) Persistência em SQLite
        criado_em = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        db = get_db()
        cur = db.execute(
            """
            INSERT INTO consultas (cep_origem, cep_destino, lat1, lon1, lat2, lon2, distancia_km, criado_em, observacoes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (cep_origem, cep_destino, lat1, lon1, lat2, lon2, distancia_km, criado_em, observacoes)
        )
        db.commit()
        novo_id = cur.lastrowid

        # 5) Resposta enriquecida
        return {
            "id": novo_id,
            "cep_origem": cep_origem,
            "cep_destino": cep_destino,
            "endereco_origem": end_origem,
            "endereco_destino": end_destino,
            "origem": {"lat": lat1, "lon": lon1},
            "destino": {"lat": lat2, "lon": lon2},
            "distancia_km": round(distancia_km, 3),
            "criado_em": criado_em,
            "observacoes": observacoes
        }, 200

    except ValueError as ve:
        return {"erro": str(ve)}, 400
    except requests.RequestException as re:
        return {"erro": "Falha ao consultar serviços externos.", "detalhes": str(re)}, 502
    except Exception as e:
        return {"erro": f"Erro inesperado: {str(e)}"}, 500

@app.route("/consultas", methods=["GET"])
def listar_consultas():
    """
    Listar consultas registradas (mais recentes primeiro)
    ---
    tags:
      - Consultas
    parameters:
      - in: query
        name: limit
        type: integer
        required: false
        description: "Quantidade de registros (padrão: 50, máx: 200)"
      - in: query
        name: offset
        type: integer
        required: false
        description: "Deslocamento (paginador simples)"
    responses:
      200:
        description: Lista de consultas
    """
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        db = get_db()
        rows = db.execute(
            """
            SELECT id, cep_origem, cep_destino, lat1, lon1, lat2, lon2, distancia_km, criado_em, observacoes
            FROM consultas
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset)
        ).fetchall()

        dados = [dict(r) for r in rows]
        return {"total": len(dados), "items": dados}, 200
    except Exception as e:
        return {"erro": f"Erro inesperado: {str(e)}"}, 500

@app.route("/consultas/<int:consulta_id>", methods=["GET"])
def obter_consulta(consulta_id: int):
    """
    Obter detalhes de uma consulta
    ---
    tags:
      - Consultas
    parameters:
      - in: path
        name: consulta_id
        type: integer
        required: true
    responses:
      200:
        description: Consulta encontrado
      404:
        description: Não encontrada
    """
    db = get_db()
    row = db.execute(
        "SELECT * FROM consultas WHERE id = ?", (consulta_id,)
    ).fetchone()
    if not row:
        return {"erro": "Consulta não encontrada."}, 404
    return dict(row), 200

@app.route("/consultas/<int:consulta_id>", methods=["PUT"])
def atualizar_consulta(consulta_id: int):
    """
    Atualizar observações de uma consulta
    ---
    tags:
      - Consultas
    consumes:
      - application/json
    parameters:
      - in: path
        name: consulta_id
        type: integer
        required: true
      - in: body
        name: payload
        required: true
        schema:
          type: object
          properties:
            observacoes:
              type: string
              example: "Consulta feita durante o vídeo de apresentação."
    responses:
      200:
        description: Consulta atualizada
      404:
        description: Não encontrada
    """
    body = request.get_json(silent=True) or {}
    observacoes = body.get("observacoes")

    db = get_db()
    cur = db.execute("UPDATE consultas SET observacoes = ? WHERE id = ?", (observacoes, consulta_id))
    db.commit()

    if cur.rowcount == 0:
        return {"erro": "Consulta não encontrada."}, 404

    row = db.execute("SELECT * FROM consultas WHERE id = ?", (consulta_id,)).fetchone()
    return dict(row), 200

@app.route("/consultas/<int:consulta_id>", methods=["DELETE"])
def excluir_consulta(consulta_id: int):
    """
    Excluir uma consulta
    ---
    tags:
      - Consultas
    parameters:
      - in: path
        name: consulta_id
        type: integer
        required: true
    responses:
      200:
        description: Consulta excluída
      404:
        description: Não encontrada
    """
    db = get_db()
    cur = db.execute("DELETE FROM consultas WHERE id = ?", (consulta_id,))
    db.commit()
    if cur.rowcount == 0:
        return {"erro": "Consulta não encontrada."}, 404
    return {"status": "excluída", "id": consulta_id}, 200

# Execução local (sem Docker)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
