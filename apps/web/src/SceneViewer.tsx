import { Canvas } from "@react-three/fiber";
import { useMemo, useState } from "react";
import type { SceneDocument, SceneEntity } from "./api";

function dimensions(entity: SceneEntity): [number, number, number] {
  const geometry = entity.geometry ?? {};
  const raw =
    (geometry["dimensions_mm"] as number[] | undefined) ??
    (geometry["size_mm"] as number[] | undefined);
  if (raw?.length === 3) {
    return [raw[0] / 1000, raw[1] / 1000, raw[2] / 1000];
  }
  return entity.kind === "wall" ? [4, 0.12, 2.8] : [0.8, 0.8, 0.8];
}

function EntityMesh({
  entity,
  selected,
  onSelect
}: {
  entity: SceneEntity;
  selected: boolean;
  onSelect: () => void;
}) {
  const size = dimensions(entity);
  const p = entity.transform?.translation_mm ?? [0, 0, 0];
  const r = entity.transform?.rotation_deg ?? [0, 0, 0];
  const color =
    (entity.metadata?.["color"] as string | undefined) ??
    (selected ? "#d8b37a" : entity.locks?.geometry ? "#8b93a1" : "#b7b9bd");

  return (
    <mesh
      position={[p[0] / 1000, p[2] / 1000, -p[1] / 1000]}
      rotation={[
        (r[0] * Math.PI) / 180,
        (r[2] * Math.PI) / 180,
        (-r[1] * Math.PI) / 180
      ]}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
    >
      <boxGeometry args={size} />
      <meshStandardMaterial color={color} transparent opacity={entity.kind === "wall" ? 0.72 : 1} />
    </mesh>
  );
}

export function SceneViewer({ scene }: { scene: SceneDocument | null }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const entities = useMemo(() => scene?.entities ?? [], [scene]);

  return (
    <div className="viewer">
      <Canvas camera={{ position: [6, 5, 6], fov: 45 }}>
        <ambientLight intensity={1.4} />
        <directionalLight position={[4, 8, 4]} intensity={2} />
        <gridHelper args={[12, 24]} />
        {entities.map((entity) => (
          <EntityMesh
            key={entity.id}
            entity={entity}
            selected={selectedId === entity.id}
            onSelect={() => setSelectedId(entity.id)}
          />
        ))}
      </Canvas>
      <div className="viewer-caption">
        {selectedId ? `selected: ${selectedId}` : "click an entity to inspect its semantic ID"}
      </div>
    </div>
  );
}
