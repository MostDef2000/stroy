import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useState } from "react";
import { PerspectiveCamera } from "three";
import type { SceneCamera, SceneDocument, SceneEntity } from "./api";

function dimensions(entity: SceneEntity): [number, number, number] {
  const geometry = entity.geometry ?? {};
  const raw =
    (geometry["dimensions_mm"] as number[] | undefined) ??
    (geometry["size_mm"] as number[] | undefined);
  if (raw?.length === 3) {
    return [raw[0] / 1000, raw[2] / 1000, raw[1] / 1000];
  }
  return entity.kind === "wall" ? [4, 2.8, 0.12] : [0.8, 0.8, 0.8];
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
      <meshStandardMaterial
        color={color}
        transparent
        opacity={entity.kind === "wall" ? 0.72 : 1}
      />
    </mesh>
  );
}

function CameraController({ calibrated }: { calibrated: SceneCamera | null }) {
  const { camera } = useThree();

  useEffect(() => {
    if (!(camera instanceof PerspectiveCamera)) return;

    if (!calibrated) {
      camera.position.set(6, 5, 6);
      camera.fov = 45;
      camera.aspect = 1.6;
      camera.lookAt(0, 0.8, 0);
      camera.updateProjectionMatrix();
      return;
    }

    const [x, y, z] = calibrated.transform.translation_mm;
    const [rx, ry, rz] = calibrated.transform.rotation_deg;
    camera.position.set(x / 1000, z / 1000, -y / 1000);
    camera.rotation.set(
      (rx * Math.PI) / 180,
      (rz * Math.PI) / 180,
      (-ry * Math.PI) / 180,
      "XYZ"
    );

    const verticalFov =
      2 * Math.atan(calibrated.height_px / (2 * calibrated.intrinsics.fy));
    camera.fov = (verticalFov * 180) / Math.PI;
    camera.aspect = calibrated.width_px / calibrated.height_px;
    camera.updateProjectionMatrix();
  }, [calibrated, camera]);

  return null;
}

export function SceneViewer({ scene }: { scene: SceneDocument | null }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [cameraId, setCameraId] = useState<string>("overview");
  const entities = useMemo(() => scene?.entities ?? [], [scene]);
  const calibrated =
    cameraId === "overview"
      ? null
      : scene?.cameras.find((camera) => camera.id === cameraId) ?? null;

  useEffect(() => {
    if (cameraId !== "overview" && !scene?.cameras.some((camera) => camera.id === cameraId)) {
      setCameraId("overview");
    }
  }, [cameraId, scene]);

  return (
    <div className="viewer-shell">
      <div className="viewer-toolbar">
        <button
          className={cameraId === "overview" ? "secondary active" : "secondary"}
          onClick={() => setCameraId("overview")}
        >
          Overview
        </button>
        {(scene?.cameras ?? []).map((camera) => (
          <button
            key={camera.id}
            className={cameraId === camera.id ? "secondary active" : "secondary"}
            onClick={() => setCameraId(camera.id)}
          >
            {camera.id}
          </button>
        ))}
      </div>

      <div
        className="viewer"
        style={
          calibrated
            ? { aspectRatio: `${calibrated.width_px} / ${calibrated.height_px}` }
            : undefined
        }
      >
        <Canvas camera={{ position: [6, 5, 6], fov: 45 }}>
          <CameraController calibrated={calibrated} />
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
          {selectedId
            ? `selected: ${selectedId}`
            : calibrated
              ? `camera: ${calibrated.id}`
              : "click an entity to inspect its semantic ID"}
        </div>
      </div>
    </div>
  );
}
