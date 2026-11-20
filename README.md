# Carla — Inventario conversacional de cafetería

Aplicación pequeña formada por una API REST en FastAPI y un agente de terminal en Python. El agente usa function calling del SDK oficial de OpenAI y `httpx` para operar sobre la API local. El inventario y el registro de actividad se guardan en archivos CSV en la raíz del proyecto.

## Requisitos

- Python 3.10 o posterior.
- Una clave de API de Groq. Puedes crearla desde la consola de Groq en <https://console.groq.com/keys>.

## 1. Preparar el entorno

Desde la raíz del proyecto, crea y activa un entorno virtual e instala las dependencias:

```bash
python -m venv .venv
source .venv/bin/activate       # Linux/macOS
# En Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Los archivos `products.csv` y `conversation_log.csv` ya incluyen sus cabeceras y datos iniciales. Si `products.csv` no existe al iniciar la API, se creará automáticamente con seis productos de ejemplo.

## 2. Levantar la API

En una terminal, desde la raíz del proyecto:

```bash
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000
```

La API queda disponible en `http://localhost:8000`. Documentación interactiva: <http://localhost:8000/docs>.

Endpoints:

- `GET /inventory` — lista el inventario.
- `POST /inventory` — añade producto con `name`, `quantity` y `unit`.
- `PATCH /inventory/{product_id}` — ajusta stock con `{"delta": 5}` o `{"delta": -2}`. No permite stock negativo.
- `GET /inventory/alerts?threshold=10` — lista productos por debajo del umbral.

## 3. Configurar el agente

Deja la API ejecutándose y abre una segunda terminal. Obtén una clave en [Groq Console](https://console.groq.com/keys) y crea un archivo `.env` en la raíz del proyecto copiando `.env.example`. Reemplaza el valor de ejemplo por tu clave:

```dotenv
GROQ_API_KEY=tu_clave_de_groq
# Opcional:
# GROQ_MODEL=openai/gpt-oss-120b
# INVENTORY_API_URL=http://localhost:8000
```

No subas `.env` al control de versiones: está incluido en `.gitignore`. El agente utiliza el SDK `openai` apuntando a la API compatible de Groq. Por defecto usa `openai/gpt-oss-120b`, que se puede cambiar con `GROQ_MODEL` si tu cuenta tiene acceso a otro modelo compatible con tool calling. Los modelos disponibles pueden variar por cuenta; consulta el catálogo en Groq Console.

## 4. Ejecutar el agente

Activa el entorno virtual en la segunda terminal y ejecuta:

```bash
python agent.py
```

Prueba, por ejemplo:

- `Acaban de llegar 30 unidades de leche de avena.`
- `Vendimos 12 bolsas de arábica hoy.`
- `¿Qué productos están por agotarse?`

Para salir escribe `salir`. Para modificar stock, el agente consulta primero el inventario y obtiene el id del producto. Los mensajes, respuestas y resultados de herramientas se agregan a `conversation_log.csv` (append-only); el archivo incluye actor, mensaje, herramienta y fecha/hora ISO 8601.

## Archivos de datos opcionales

Por defecto, `api/app.py` usa `products.csv` y `agent.py` escribe en `conversation_log.csv`, ambos en la raíz del proyecto. Se pueden cambiar con las variables `INVENTORY_CSV` y `CONVERSATION_LOG_CSV`, respectivamente. `INVENTORY_API_URL` permite cambiar la URL base consumida por el agente.
