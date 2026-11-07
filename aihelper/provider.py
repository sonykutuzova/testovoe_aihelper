"""OpenAI Responses API over HTTPS, using only Python's standard library."""
import json
import os
import socket
import time
import urllib.error
import urllib.request

from .domain import AppError
from .prompts import DRAFT_SCHEMA, SYSTEM_PROMPT


class OpenAIProvider:
    def __init__(self, api_key=None, model=None, opener=None, sleep=time.sleep):
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep

    def generate(self, product, feedback=None):
        if not self.api_key.strip():
            raise AppError("missing_api_key", "Задайте OPENAI_API_KEY в окружении сервера.", 503)
        content = "PRODUCT_DATA\n" + json.dumps(product.as_dict(), ensure_ascii=False)
        if feedback:
            content += "\nИсправь ошибки предыдущей попытки: " + json.dumps(feedback, ensure_ascii=False)
        payload = {
            "model": self.model, "store": False,
            "instructions": SYSTEM_PROMPT,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "product_card",
                                  "strict": True, "schema": DRAFT_SCHEMA}},
            "max_output_tokens": 3000,
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(3):
            try:
                with self.opener(request, timeout=40) as response:
                    raw = response.read(512 * 1024 + 1)
                if len(raw) > 512 * 1024:
                    raise AppError("provider_response", "Ответ API слишком большой.", 502)
                return self._extract(raw)
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                if (status == 429 or status >= 500) and attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                if status in (401, 403):
                    raise AppError("api_auth", "API отклонил доступ. Проверьте ключ и доступ к выбранной модели.", 502) from exc
                if status == 429:
                    raise AppError("api_rate_limit", "Лимит API исчерпан. Проверьте квоту и повторите позже.", 503) from exc
                raise AppError("api_error", f"Ошибка LLM API (HTTP {status}). Проверьте модель и повторите позже.", 502) from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                # Do not retry ambiguous timeouts: the server may already have billed the request.
                raise AppError("api_connection", "Не удалось получить ответ API: проверьте соединение и повторите позже.", 504) from exc

    @staticmethod
    def _extract(raw):
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            if data.get("status") != "completed":
                raise AppError("incomplete_response", "Модель не завершила ответ. Повторите запрос.", 502)
            texts = []
            for item in data.get("output", []):
                if item.get("type") != "message":
                    continue
                for part in item.get("content", []):
                    if part.get("type") == "refusal":
                        raise AppError("model_refusal", "Модель отказалась создавать карточку. Проверьте исходные данные.", 422)
                    if part.get("type") == "output_text":
                        texts.append(part["text"])
            return json.loads("".join(texts))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise AppError("provider_response", "API вернул некорректный ответ.", 502) from exc
