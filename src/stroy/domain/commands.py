from __future__ import annotations

from stroy.domain.models import (
    CommandOperation,
    DesignCommand,
    EntityIntent,
    EntityLocks,
    EntityState,
    Scene,
    SceneEntity,
    Transform,
)


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
        if "state" not in raw:
            # Old clients predate the state layers: everything they add is a
            # design object. Set explicitly (the SceneEntity default is asis).
            entity.state = EntityState.DESIGN
        result.entities.append(entity)
        return result

    target = _entity(result, command.target_id)

    if op is CommandOperation.SET_STATE:
        # State is bookkeeping, not geometry/material/transform: locks guard
        # their own mutations only and never block a state change.
        raw_state = command.parameters.get("state")
        try:
            next_state = EntityState(raw_state)
        except ValueError as exc:
            raise CommandRejected(
                f"set_state requires parameters.state to be one of "
                f"asis/structure/design, got: {raw_state}"
            ) from exc
        if (
            next_state is EntityState.STRUCTURE
            and target.intent in {EntityIntent.REMOVE, EntityIntent.REPLACE}
        ):
            # An object slated for removal/replacement cannot become part of
            # the construction shell: the combination is invalid.
            raise CommandRejected(f"entity is marked for {target.intent}: {target.id}")
        target.state = next_state
        return result

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
        if target.locks.existence:
            raise CommandRejected(f"existence is locked: {target.id}")
        if target.intent is EntityIntent.KEEP:
            raise CommandRejected(f"entity is pinned by keep intent: {target.id}")
        if target.locks.geometry:
            raise CommandRejected(f"geometry is locked: {target.id}")
        result.entities = [entity for entity in result.entities if entity.id != target.id]
        return result

    if op is CommandOperation.REPLACE_OBJECT_FROM_REFERENCE:
        if target.locks.existence:
            raise CommandRejected(f"existence is locked: {target.id}")
        if target.intent in {EntityIntent.KEEP, EntityIntent.REMOVE}:
            raise CommandRejected(f"entity is marked for {target.intent}: {target.id}")
        if target.locks.geometry:
            raise CommandRejected(f"geometry is locked: {target.id}")
        if not command.reference_asset_ids:
            raise CommandRejected("replacement requires at least one reference asset")
        target.metadata["replacement_reference_asset_id"] = command.reference_asset_ids[0]
        return result

    if op is CommandOperation.SET_INTENT:
        raw_intent = command.parameters.get("intent")
        if raw_intent is None:
            target.intent = None
            return result
        try:
            next_intent = EntityIntent(raw_intent)
        except ValueError as exc:
            raise CommandRejected(
                f"set_intent requires parameters.intent to be one of "
                f"keep/remove/replace or null, got: {raw_intent}"
            ) from exc
        if (
            target.state is EntityState.STRUCTURE
            and next_intent in {EntityIntent.REMOVE, EntityIntent.REPLACE}
        ):
            # Construction shell is not a removal/replacement candidate.
            raise CommandRejected(
                f"structure entity cannot receive {next_intent.value} intent: {target.id}"
            )
        target.intent = next_intent
        return result

    if op is CommandOperation.SET_LOCKS:
        # Lock bookkeeping is not guarded by the locks themselves: the owner
        # must always be able to re-lock or unlock an entity.
        raw_locks = command.parameters.get("locks")
        if not isinstance(raw_locks, dict):
            raise CommandRejected("set_locks requires parameters.locks object")
        current = target.locks.model_dump()
        for key, value in raw_locks.items():
            if key not in current:
                raise CommandRejected(f"unknown lock: {key}")
            if not isinstance(value, bool):
                raise CommandRejected(f"lock {key} requires a boolean value")
            current[key] = value
        target.locks = EntityLocks.model_validate(current)
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
