from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    input_name: str = Field(min_length=1)


class WorkflowManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1.0"] = "0.1.0"
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model_profile: str = Field(min_length=1)
    required_inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(min_length=1)
    bindings: dict[str, InputBinding] = Field(default_factory=dict)
    graph: dict[str, Any]
    notes: str | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> "WorkflowManifest":
        if len(self.required_inputs) != len(set(self.required_inputs)):
            raise ValueError("required_inputs must be unique")
        if len(self.outputs) != len(set(self.outputs)):
            raise ValueError("outputs must be unique")
        missing_bindings = [
            name for name in self.required_inputs if name not in self.bindings
        ]
        if missing_bindings:
            raise ValueError(
                "required semantic inputs have no graph binding: "
                + ", ".join(sorted(missing_bindings))
            )
        return self

    def materialize(self, inputs: dict[str, Any]) -> dict[str, Any]:
        missing = [name for name in self.required_inputs if name not in inputs]
        if missing:
            raise ValueError(
                "missing required workflow inputs: " + ", ".join(sorted(missing))
            )

        graph = deepcopy(self.graph)
        for semantic_name, value in inputs.items():
            binding = self.bindings.get(semantic_name)
            if binding is None:
                continue
            try:
                node = graph[binding.node_id]
            except KeyError as exc:
                raise ValueError(
                    f"workflow binding references missing node {binding.node_id}"
                ) from exc
            if not isinstance(node, dict):
                raise ValueError(
                    f"workflow node {binding.node_id} must be an object"
                )
            node_inputs = node.setdefault("inputs", {})
            if not isinstance(node_inputs, dict):
                raise ValueError(
                    f"workflow node {binding.node_id}.inputs must be an object"
                )
            node_inputs[binding.input_name] = value
        return graph

    def provenance(self) -> dict[str, str]:
        return {
            "workflow_id": self.id,
            "workflow_version": self.version,
            "model_profile": self.model_profile,
        }
