"""Generate, validate, and export a reviewable card."""
import re

from .domain import AppError, Product, numeric_tokens
from .prompts import PROMPT_VERSION


def validate_draft(draft, product):
    errors = []
    expected = {"seo_title", "seo_description", "search_queries", "commercial", "clarifications"}
    if not isinstance(draft, dict) or set(draft) != expected:
        return ["Неверный набор полей ответа."]
    for field, minimum, maximum in [("seo_title", 1, 70), ("seo_description", 800, 1200)]:
        value = draft[field]
        if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
            errors.append(f"{field}: требуется {minimum}–{maximum} символов.")
    queries = draft["search_queries"]
    if not isinstance(queries, list) or not 3 <= len(queries) <= 5:
        errors.append("search_queries: требуется от трех до пяти запросов.")
    elif any(not isinstance(q, str) or not q.strip() or len(q) > 200 for q in queries):
        errors.append("search_queries: нужны непустые строки до 200 символов.")
    elif len({q.strip().casefold() for q in queries}) != len(queries):
        errors.append("search_queries: запросы должны отличаться.")
    commercial = draft["commercial"]
    if not isinstance(commercial, dict) or set(commercial) != {"audience", "tasks"}:
        errors.append("commercial: требуются audience и tasks.")
    elif any(not isinstance(v, str) or not v.strip() or len(v) > 600 for v in commercial.values()):
        errors.append("commercial: нужны непустые строки до 600 символов.")
    clarifications = draft["clarifications"]
    if not isinstance(clarifications, list) or len(clarifications) > 20 or any(
        not isinstance(v, str) or not v.strip() or len(v) > 500 for v in clarifications
    ):
        errors.append("clarifications: нужен список вопросов, максимум двадцать.")
    if errors:
        return errors
    # This is a conservative guard, not a semantic proof of factual accuracy.
    generated = " ".join([draft["seo_title"], draft["seo_description"], *queries, *commercial.values()])
    source = " ".join([product.name, product.manufacturer, product.description,
                       *product.specs.keys(), *product.specs.values()])
    unsupported = numeric_tokens(generated) - numeric_tokens(source)
    if unsupported:
        errors.append("Новые числовые значения запрещены: " + ", ".join(sorted(unsupported)))
    # Detect common unsupported alphanumeric technical identifiers, e.g. IP65/RS-485.
    codes = lambda text: set(re.findall(r"\b[A-Za-zА-Яа-яЁё]+[-/]?\d+[A-Za-zА-Яа-яЁё\d/-]*\b", text))
    if codes(generated) - codes(source):
        errors.append("Обнаружены технические обозначения, отсутствующие в исходных данных.")
    return errors


def generate_card(data, provider):
    product = Product.from_dict(data)
    feedback = None
    for attempt in range(2):
        draft = provider.generate(product, feedback=feedback)
        feedback = validate_draft(draft, product)
        if not feedback:
            break
    else:
        raise AppError("invalid_generation", "Ответ модели не прошёл проверку. Карточка не сформирована.", 422, feedback)
    missing = product.missing_fields()
    warnings = ["Черновик: перед публикацией сверьте текст и назначение с паспортом прибора.",
                "Таблица отражает только структурированные specs; сведения из description требуют сверки.",
                "Поисковые запросы — смысловые предложения, их частотность не проверялась."]
    if missing:
        warnings.append("Применимость отсутствующих полей нужно уточнить: отсутствие сведений не означает отсутствие свойства.")
    return {
        "status": "draft", **draft,
        "characteristics": product.facts(),
        "missing_fields": missing, "warnings": warnings,
        "meta": {"model": provider.model, "prompt_version": PROMPT_VERSION,
                 "seo_title_chars": len(draft["seo_title"].strip()),
                 "seo_description_chars": len(draft["seo_description"].strip()),
                 "generation_attempts": attempt + 1},
    }

