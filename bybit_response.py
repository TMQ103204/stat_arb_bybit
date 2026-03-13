from typing import Any


def as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        return dict(value)
    except Exception:
        return {}


def get_ret_code(response: Any) -> int | None:
    payload = as_dict(response)
    code = payload.get("retCode")
    if isinstance(code, int):
        return code
    if isinstance(code, str):
        try:
            return int(code)
        except ValueError:
            return None
    return None


def get_result_dict(response: Any) -> dict[str, Any]:
    payload = as_dict(response)
    return as_dict(payload.get("result"))


def get_result_list(response: Any) -> list[Any]:
    result = get_result_dict(response)
    items = result.get("list")
    if isinstance(items, list):
        return items
    if isinstance(items, tuple):
        return list(items)
    return []
