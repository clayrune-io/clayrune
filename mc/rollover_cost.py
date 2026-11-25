"""Cost and continuity policy for a measured fresh context floor."""

from math import ceil, floor, sqrt

GRID_TOKENS = 10_000
MAX_PREMIUM_TOKENS = 30_000
MAX_COST_PREMIUM = 0.02


def continuity_target(fresh: float, growth: float, ratio: float, minimum: int) -> int:
    # Relative cost per call = F + room/2 + ratio*F*growth/room.
    optimum_room = sqrt(2 * fresh * growth * ratio)
    optimum = fresh + optimum_room
    minimum_grid = ceil(minimum / GRID_TOKENS) * GRID_TOKENS
    first = ceil(optimum / GRID_TOKENS) * GRID_TOKENS
    last = floor((optimum + MAX_PREMIUM_TOKENS) / GRID_TOKENS) * GRID_TOKENS
    for target in range(last, first - 1, -GRID_TOKENS):
        room = target - fresh
        extra_cost = (room - optimum_room) ** 2 / (2 * room)
        if extra_cost <= MAX_COST_PREMIUM * optimum:
            return max(minimum_grid, target)
    return max(minimum_grid, round(optimum / GRID_TOKENS) * GRID_TOKENS)
