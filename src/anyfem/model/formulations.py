"""Persisted shell-formulation policy for solver model construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from anymesher import S3_QUALITY_CONTRACT_ID
from anysolver import QUALIFIED_Q4_FORMULATION_ID, QUALIFIED_S3_FORMULATION_ID

__all__ = ["ShellFormulationPolicy"]


_Q4 = {"e4-pl", "legacy"}
_S3 = {"e4-pl-s3", "legacy-s3"}
_HIGHER = {"legacy"}
_Q4_IDS = {
    "e4-pl": QUALIFIED_Q4_FORMULATION_ID,
    "legacy": "LEGACY_SHELL_ELEMENT_Q4",
}
_S3_IDS = {
    "e4-pl-s3": QUALIFIED_S3_FORMULATION_ID,
    "legacy-s3": "LEGACY_SHELL_ELEMENT_TRI3",
}


def _token(value: object, *, field: str, allowed: set[str]) -> str:
    if not isinstance(value, str):
        raise ValueError(f"shell formulation {field} must be text")
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in allowed:
        raise ValueError(
            f"unsupported shell formulation {field}={value!r}; "
            f"expected one of {sorted(allowed)}"
        )
    return normalized


@dataclass(frozen=True)
class ShellFormulationPolicy:
    """Topology-specific shell identities used when a project is built.

    New format-8 projects use the qualified S3 companion.  Files without this
    record are migrated to an explicit legacy-S3 policy by the project codec,
    so reopening an older project cannot silently change its triangular
    mechanics.
    """

    q4: str = "e4-pl"
    s3: str = "e4-pl-s3"
    higher_order: str = "legacy"

    def __post_init__(self) -> None:
        object.__setattr__(self, "q4", _token(self.q4, field="q4", allowed=_Q4))
        object.__setattr__(self, "s3", _token(self.s3, field="s3", allowed=_S3))
        object.__setattr__(
            self,
            "higher_order",
            _token(self.higher_order, field="higher_order", allowed=_HIGHER),
        )

    @classmethod
    def qualified_s3_candidate(cls) -> "ShellFormulationPolicy":
        return cls.current_default()

    @classmethod
    def current_default(cls) -> "ShellFormulationPolicy":
        return cls(q4="e4-pl", s3="e4-pl-s3")

    @classmethod
    def migrated_legacy_s3(cls) -> "ShellFormulationPolicy":
        """Keep historical TRI3 mechanics while retaining qualified Q4."""

        return cls(q4="e4-pl", s3="legacy-s3")

    @classmethod
    def legacy_compatible(cls) -> "ShellFormulationPolicy":
        return cls(q4="legacy", s3="legacy-s3")

    def for_node_count(self, node_count: int) -> str:
        if node_count == 3:
            return self.s3
        if node_count == 4:
            return self.q4
        if node_count in {6, 8}:
            return self.higher_order
        raise ValueError(f"unsupported shell topology with {node_count} nodes")

    def formulation_id_for_node_count(self, node_count: int) -> str:
        token = self.for_node_count(node_count)
        if node_count == 3:
            return _S3_IDS[token]
        if node_count == 4:
            return _Q4_IDS[token]
        return "LEGACY_SHELL_ELEMENT_HIGHER_ORDER"

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": "anyfem.shell-formulation-policy-v2",
            "q4": self.q4,
            "s3": self.s3,
            "higher_order": self.higher_order,
            "q4_formulation_id": _Q4_IDS[self.q4],
            "s3_formulation_id": _S3_IDS[self.s3],
            "higher_order_formulation_id": "LEGACY_SHELL_ELEMENT_HIGHER_ORDER",
            "s3_quality_contract_id": (
                S3_QUALITY_CONTRACT_ID
                if self.s3 == "e4-pl-s3"
                else "NOT_APPLICABLE"
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "ShellFormulationPolicy":
        if not isinstance(data, Mapping):
            raise ValueError("shell_formulations must be an object")
        expected = {
            "schema",
            "q4",
            "s3",
            "higher_order",
            "q4_formulation_id",
            "s3_formulation_id",
            "higher_order_formulation_id",
            "s3_quality_contract_id",
        }
        if set(data) != expected:
            raise ValueError(
                "shell_formulations must contain exactly " + ", ".join(sorted(expected))
            )
        if data["schema"] != "anyfem.shell-formulation-policy-v2":
            raise ValueError("unsupported shell formulation policy schema")
        made = cls(
            q4=data["q4"],  # type: ignore[arg-type]
            s3=data["s3"],  # type: ignore[arg-type]
            higher_order=data["higher_order"],  # type: ignore[arg-type]
        )
        expected_ids = made.to_dict()
        for key in (
            "q4_formulation_id",
            "s3_formulation_id",
            "higher_order_formulation_id",
            "s3_quality_contract_id",
        ):
            if data[key] != expected_ids[key]:
                raise ValueError(f"shell formulation identity mismatch for {key}")
        return made
