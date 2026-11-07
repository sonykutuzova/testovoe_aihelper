"""Input validation and deterministic, source-preserving product facts."""
import json
import re
from dataclasses import dataclass

MAX_BODY = 64 * 1024
MISSING = "данных нет"
EXPECTED = {
    "диапазон измерений": {"диапазон измерения", "диапазон", "диапазон измерений"},
    "точность": {"точность", "класс точности", "погрешность", "основная погрешность"},
    "питание": {"питание", "напряжение питания"},
    "корпус": {"корпус", "тип корпуса", "материал корпуса"},
    "интерфейсы": {"интерфейс", "интерфейсы"},
}

class AppError(Exception):
    def __init__(self, code, message, status=400, details=None):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details or []

    def as_dict(self):
        return {"error": {"code": self.code, "message": str(self), "details": self.details}}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Повторяющийся ключ: {key}")
        result[key] = value
    return result


def parse_json(raw):
    if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_BODY:
        raise AppError("input_too_large", "JSON превышает 64 КБ.", 413)
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AppError("invalid_json", "Некорректный JSON: проверьте синтаксис, кодировку и повторяющиеся ключи.") from exc


def clean_text(value, field, limit, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise AppError("invalid_input", f"{field}: ожидается строка.")
    value = value.strip()
    if required and not value:
        raise AppError("missing_name", "Укажите название товара.", 422, ["name"])
    if len(value) > limit:
        raise AppError("invalid_input", f"{field}: максимум {limit} символов.")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise AppError("invalid_input", f"{field}: недопустимый управляющий символ.")
    return value


@dataclass
class Product:
    name: str
    manufacturer: str
    specs: dict
    description: str

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise AppError("invalid_input", "На входе нужен JSON-объект одного товара.")
        unknown = set(data) - {"name", "manufacturer", "specs", "description"}
        if unknown:
            raise AppError("invalid_input", "Неизвестные поля.", details=sorted(unknown))
        name = clean_text(data.get("name"), "name", 200, required=True)
        manufacturer = clean_text(data.get("manufacturer"), "manufacturer", 200)
        description = clean_text(data.get("description"), "description", 12000)
        specs = data.get("specs")
        if specs is None:
            specs = {}
        if not isinstance(specs, dict) or len(specs) > 50:
            raise AppError("invalid_input", "specs: нужен объект, максимум 50 характеристик.")
        normalized = {}
        for key, value in specs.items():
            key = clean_text(key, "название характеристики", 100, required=True)
            if key.casefold() in {k.casefold() for k in normalized}:
                raise AppError("invalid_input", "Повторяющееся название характеристики.")
            normalized[key] = clean_text(value, f"specs.{key}", 500)
        return cls(name, manufacturer, normalized, description)

    def as_dict(self):
        return {"name": self.name, "manufacturer": self.manufacturer,
                "specs": self.specs, "description": self.description}

    def facts(self):
        # LLM cannot overwrite these values or infer missing units.
        rows = [{"name": "Производитель", "value": self.manufacturer or MISSING,
                 "source": "manufacturer" if self.manufacturer else None}]
        rows += [{"name": k, "value": v or MISSING, "source": f"specs.{k}" if v else None}
                 for k, v in self.specs.items()]
        names = {k.casefold() for k in self.specs}
        for label, aliases in EXPECTED.items():
            if not names.intersection(aliases):
                rows.append({"name": label, "value": MISSING, "source": None})
        return rows

    def missing_fields(self):
        result = [row["name"] for row in self.facts() if row["source"] is None]
        if not self.description:
            result.append("description: назначение / фрагмент технического паспорта")
        return result


def numeric_tokens(text):
    # Keeps decimal, signed values, fractions, and ranges intact: 0–1,6 != 0–16.
    return set(re.findall(r"(?<!\w)[+±−-]?\d+(?:[.,/]\d+)*(?:[–−…-][+−-]?\d+(?:[.,/]\d+)*)?", text))
