"""MC-1178 Claude rollover inputs; prices are dollars per million tokens.

Values follow the reviewed v1 plan (2026-11-16), not a live price feed.
Unknown legacy prices stay None; their reviewed write/read ratio is explicit.
Growth is measured per call, borrowed from the newest measured family member,
or provisional for Haiku. This table makes no rollover decisions.
"""
import re
from typing import Literal, TypedDict

from mc.api_credits_offload import MODEL_ALIASES


class ModelInputs(TypedDict):
    window_tokens: int
    input_price: float | None
    cache_read_price: float | None
    cache_write_price: float | None
    write_read_ratio: float
    mean_growth_tokens: int
    growth_source: Literal['measured', 'family', 'provisional']
    price_step_tokens: int | None


MODEL_TABLE: dict[str, ModelInputs] = {
    'claude-fable-5-2': {
        'window_tokens': 1_000_000, 'input_price': 8, 'cache_read_price': 0.20,
        'cache_write_price': 10, 'write_read_ratio': 50,
        'mean_growth_tokens': 3900, 'growth_source': 'measured', 'price_step_tokens': None,
    },
    'claude-fable-5-1': {
        'window_tokens': 1_000_000, 'input_price': 10, 'cache_read_price': 0.25,
        'cache_write_price': 12.5, 'write_read_ratio': 50,
        'mean_growth_tokens': 3900, 'growth_source': 'family', 'price_step_tokens': None,
    },
    'claude-fable-5': {
        'window_tokens': 1_000_000, 'input_price': None, 'cache_read_price': None,
        'cache_write_price': None, 'write_read_ratio': 12.5,
        'mean_growth_tokens': 3900, 'growth_source': 'family', 'price_step_tokens': None,
    },
    'claude-opus-5-5': {
        'window_tokens': 1_000_000, 'input_price': 4, 'cache_read_price': 0.20,
        'cache_write_price': 5, 'write_read_ratio': 25,
        'mean_growth_tokens': 1600, 'growth_source': 'measured', 'price_step_tokens': None,
    },
    'claude-opus-5': {
        'window_tokens': 1_000_000, 'input_price': None, 'cache_read_price': None,
        'cache_write_price': None, 'write_read_ratio': 12.5,
        'mean_growth_tokens': 1600, 'growth_source': 'family', 'price_step_tokens': None,
    },
    'claude-sonnet-5-5': {
        'window_tokens': 1_000_000, 'input_price': 2, 'cache_read_price': 0.10,
        'cache_write_price': 2.5, 'write_read_ratio': 25,
        'mean_growth_tokens': 2600, 'growth_source': 'measured', 'price_step_tokens': None,
    },
    'claude-sonnet-5': {
        'window_tokens': 1_000_000, 'input_price': None, 'cache_read_price': None,
        'cache_write_price': None, 'write_read_ratio': 12.5,
        'mean_growth_tokens': 2600, 'growth_source': 'family', 'price_step_tokens': None,
    },
    'claude-haiku-5-5': {
        'window_tokens': 1_000_000, 'input_price': 0.10, 'cache_read_price': 0.01,
        'cache_write_price': 0.125, 'write_read_ratio': 12.5,
        'mean_growth_tokens': 2600, 'growth_source': 'provisional', 'price_step_tokens': 100_000,
    },
    'claude-haiku-4-5': {
        'window_tokens': 200_000, 'input_price': 1, 'cache_read_price': 0.10,
        'cache_write_price': 1.25, 'write_read_ratio': 12.5,
        'mean_growth_tokens': 2600, 'growth_source': 'provisional', 'price_step_tokens': None,
    },
}


def canonical_model_id(model: object) -> str | None:
    """Normalize Claude dates, context-window suffixes and reviewed aliases."""
    if not isinstance(model, str):
        return None
    canonical = re.sub(r'\[1m\]$', '', model.strip().lower()).rstrip()
    return (MODEL_ALIASES.get(canonical) or re.sub(r'-[0-9]{8}$', '', canonical)) or None


def model_inputs(model: object) -> ModelInputs | None:
    canonical = canonical_model_id(model)
    return MODEL_TABLE.get(canonical) if canonical else None
