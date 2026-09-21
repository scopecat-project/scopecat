"""Stable pulse implementation identities shared by recipe binding styles."""

from urllib.parse import quote

from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash

from scopecat_quantum._ids import PulseImplementationId, QubitId
from scopecat_quantum.pulse_implementations import GatePulseImplementationKey


def _encoded_operands(operands: tuple[QubitId, ...]) -> str:
    return ",".join(quote(operand.value, safe="-._~") for operand in operands)


def gate_implementation_id(
    recipe_id: str,
    key: GatePulseImplementationKey,
) -> PulseImplementationId:
    suffix = f"[{_encoded_operands(key.operands)}]"
    if key.arguments:
        argument_hash = stable_content_hash(content_fingerprint(key.arguments))
        suffix = f"{suffix}[{argument_hash}]"
    if key.recipe_scope is not None:
        suffix = f"{suffix}[scope={quote(key.recipe_scope, safe='-._~')}]"
    return PulseImplementationId(f"{recipe_id}{suffix}")
