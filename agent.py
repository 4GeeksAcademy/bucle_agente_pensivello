"""Manual tool-calling conversational agent for Carla's inventory."""

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import httpx
from dotenv import load_dotenv
from openai import OpenAI


ROOT = Path(__file__).resolve().parent
API_BASE_URL = os.getenv("INVENTORY_API_URL", "http://localhost:8000").rstrip("/")
LOG_FILE = Path(os.getenv("CONVERSATION_LOG_CSV", ROOT / "conversation_log.csv"))
DEFAULT_MODEL = "openai/gpt-oss-120b"
LOG_FIELDS = ["actor", "message", "tool_call", "timestamp"]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_inventory",
            "description": "Lista todos los productos y su stock actual. Úsala para encontrar el id de un producto antes de modificarlo.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_product",
            "description": "Añade un nuevo producto al inventario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre del producto"},
                    "quantity": {"type": "number", "minimum": 0, "description": "Cantidad inicial"},
                    "unit": {"type": "string", "description": "Unidad, por ejemplo unidades, kg, litros o bolsas"},
                },
                "required": ["name", "quantity", "unit"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_stock",
            "description": "Ajusta el stock de un producto existente. Requiere el id encontrado con get_inventory y delta puede ser positivo o negativo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "Id exacto del producto en inventario"},
                    "delta": {"type": "number", "description": "Cambio de stock; negativo para salidas"},
                },
                "required": ["product_id", "delta"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inventory_alerts",
            "description": "Lista productos por debajo de una cantidad límite configurable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold": {"type": "number", "minimum": 0, "description": "Umbral de stock (por defecto 10)"}
                },
                "additionalProperties": False,
            },
        },
    },
]

SYSTEM_MESSAGE = """Eres Carla, una asistente amable y precisa para gestionar el inventario de una tienda de suministros de cafetería. Responde en español, confirma las operaciones usando los resultados de la API y nunca inventes cantidades ni ids. Para ajustar stock por nombre, primero llama a get_inventory, identifica el producto correcto y usa su id exacto con update_stock. Si hay nombres ambiguos, pregunta antes de cambiar existencias. Las ventas son deltas negativos y las entradas son positivos. Informa con claridad si la API rechaza una operación."""


def log_event(actor: str, message: str, tool_call: str = "") -> None:
    """Append one event to the persistent conversation log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0
    with LOG_FILE.open("a", newline="", encoding="utf-8") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=LOG_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow(
            {
                "actor": actor,
                "message": message,
                "tool_call": tool_call,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


def call_api_tool(name: str, arguments: Dict[str, Any], client: httpx.Client) -> Any:
    """Map an LLM function request to the corresponding REST endpoint."""
    if name == "get_inventory":
        response = client.get(f"{API_BASE_URL}/inventory")
    elif name == "add_product":
        response = client.post(f"{API_BASE_URL}/inventory", json=arguments)
    elif name == "update_stock":
        product_id = arguments.get("product_id")
        if not isinstance(product_id, str) or not product_id:
            raise ValueError("Se necesita un product_id válido para actualizar el stock.")
        response = client.patch(
            f"{API_BASE_URL}/inventory/{product_id}",
            json={"delta": arguments["delta"]},
        )
    elif name == "get_inventory_alerts":
        response = client.get(
            f"{API_BASE_URL}/inventory/alerts",
            params={key: value for key, value in arguments.items() if key == "threshold"},
        )
    else:
        raise ValueError(f"Herramienta desconocida: {name}")

    try:
        result = response.json()
    except ValueError:
        result = {"detail": response.text or "La API devolvió una respuesta no válida."}
    if not response.is_success:
        return {"error": True, "status_code": response.status_code, "detail": result}
    return result


def run_turn(user_text: str, messages: list, client: OpenAI, http_client: httpx.Client) -> str:
    """Execute the manual observe-think-act-update loop for a user turn."""
    messages.append({"role": "user", "content": user_text})
    log_event("user", user_text)

    while True:
        completion = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", DEFAULT_MODEL),
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        assistant_message = completion.choices[0].message
        assistant_entry: Dict[str, Any] = {"role": "assistant", "content": assistant_message.content}

        if assistant_message.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in assistant_message.tool_calls
            ]
        messages.append(assistant_entry)

        if not assistant_message.tool_calls:
            final_answer = assistant_message.content or "No tengo una respuesta para mostrar."
            log_event("agent", final_answer)
            return final_answer

        for tool_call in assistant_message.tool_calls:
            name = tool_call.function.name
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("Los argumentos de la herramienta deben ser un objeto JSON.")
                result = call_api_tool(name, arguments, http_client)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                result = {"error": True, "detail": str(exc)}
            except httpx.HTTPError as exc:
                result = {
                    "error": True,
                    "detail": f"No se pudo conectar con la API ({API_BASE_URL}): {exc}",
                }

            result_text = json.dumps(result, ensure_ascii=False)
            log_event("tool", result_text, name)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text,
                }
            )


def main() -> None:
    load_dotenv(ROOT / ".env")
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        print("Falta GROQ_API_KEY. Configúrala en el entorno o en un archivo .env.", file=sys.stderr)
        raise SystemExit(1)

    ai_client = OpenAI(
        api_key=groq_api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    conversation = [{"role": "system", "content": SYSTEM_MESSAGE}]
    print("Carla está lista para ayudarte con el inventario. Escribe 'salir' para terminar.")

    with httpx.Client(timeout=20.0) as http_client:
        while True:
            try:
                user_text = input("Tú: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nHasta luego.")
                break
            if not user_text:
                continue
            if user_text.casefold() in {"salir", "exit", "quit"}:
                print("Hasta luego.")
                break
            try:
                answer = run_turn(user_text, conversation, ai_client, http_client)
                print(f"Carla: {answer}")
            except Exception as exc:
                print(f"Carla: No pude completar la solicitud: {exc}", file=sys.stderr)
                # Persist runtime/API errors as an agent event as well.
                log_event("agent", f"Error al procesar la solicitud: {exc}")


if __name__ == "__main__":
    main()
