from __future__ import annotations

from stroy.domain.models import CommandOperation, DesignCommand, Scene, SceneEntity, Transform


class CommandRejected(ValueError):
    pass


class CommandConflict(CommandRejected):
    pass


def _entity(scene: Scene, target_id: str) -> SceneEntity:
    for entity in scene.entities:
        if entity.id == target_id:
            return entity
    raise CommandRejected(f"unknown target entity: {target_id}")


def apply_command(scene: Scene, command: DesignCommand) -> Scene:
    result = scene.model_copy(deep=True)
    op = command.operation

    if op is CommandOperation.ADD_OBJECT:
        raw = command.parameters.get("entity")
        if not isinstance(raw, dict):
            raise CommandRejected("add_object requires parameters.entity")
        entity = SceneEntity.model_validate(raw)
        if entity.id != command.target_id:
            raise CommandRejected("target_id must equal added entity.id")
        if any(existing.id == entity.id for existing in result.entities):
            raise CommandConflict(f"entity already exists: {entity.id}")
        result.entities.append(entity)
        return result

    target = _entity(result, command.target_id)

    if op in {CommandOperation.SET_MATERIAL, CommandOperation.SET_COLOR}:
        if target.locks.material:
            raise CommandRejected(f"material is locked: {target.id}")
        if op is CommandOperation.SET_MATERIAL:
            material_ref = command.parameters.get("material_ref")
            if not isinstance(material_ref, str) or not material_ref:
                raise CommandRejected("set_material requires parameters.material_ref")
            target.material_ref = material_ref
        else:
            color = command.parameters.get("color")
            if not isinstance(color, str) or not color:
                raise CommandRejected("set_color requires parameters.color")
            target.metadata["color"] = color
        return result

    if op is CommandOperation.MOVE_OBJECT:
        if target.locks.transform or target.locks.geometry:
            raise CommandRejected(f"transform is locked: {target.id}")
        current = target.transform.model_dump()
        for key in ("translation_mm", "rotation_deg", "scale"):
            if key in command.parameters:
                current[key] = command.parameters[key]
        target.transform = Transform.model_validate(current)
        return result

    if op is CommandOperation.REMOVE_OBJECT:
        if target.locks.geometry:
            raise CommandRejected(f"geometry is locked: {target.id}")
        result.entities = [entity for entity in result.entities if entity.id != target.id]
        return result

    if op is CommandOperation.REPLACE_OBJECT_FROM_REFERENCE:
        if target.locks.geometry:
            raise CommandRejected(f"geometry is locked: {target.id}")
        if not command.reference_asset_ids:
            raise CommandRejected("replacement requires at least one reference asset")
        target.metadata["replacement_reference_asset_id"] = command.reference_asset_ids[0]
        return result

    if op is CommandOperation.SET_LIGHT_INTENT:
        intent = command.parameters.get("intent")
        if not intent:
            raise CommandRejected("set_light_intent requires parameters.intent")
        target.metadata["light_intent"] = intent
        if "temperature_k" in command.parameters:
            target.metadata["temperature_k"] = command.parameters["temperature_k"]
        return result

    raise CommandRejected(f"unsupported operation: {op}")
