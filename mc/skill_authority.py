"""The learning authority rail at every external skill installation sink."""


def require_skill_authority(description: str, body: str) -> None:
    """Refuse before writing, including when an import scanner is forced."""
    # Lazy import: distiller and skills share installation paths.
    from mc.distiller import _authority_violation

    reason = _authority_violation(description + '\n' + body)
    if reason:
        raise ValueError(f'skill authority expansion refused: {reason}')
