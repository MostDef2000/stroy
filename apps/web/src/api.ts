export type Project = {
  id: string;
  name: string;
  created_at: string;
};

export type Worker = {
  id: string;
  display_name: string | null;
  online: boolean;
  capabilities: string[];
  models: string[];
  last_heartbeat: string;
};

export type AssetRole = "apartment" | "reference" | "derived";

export type Asset = {
  id: string;
  original_name: string | null;
  media_type: string;
  size_bytes: number;
  sha256: string;
  provenance: string;
  role: AssetRole;
  metadata: Record<string, unknown>;
  source_asset_id: string | null;
  source_asset_ids: string[];
  duplicate_of_asset_id: string | null;
  created_at: string;
};

export type Job = {
  id: string;
  project_id: string | null;
  job_type: string;
  status: string;
  attempt: number;
  idempotency_key: string | null;
  progress: Record<string, unknown>;
  correlation_id: string | null;
  runtime_provenance: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  leased_to: string | null;
  lease_expires_at: string | null;
  created_at: string;
};

export type Generation = {
  id: string;
  job_id: string;
  scene_revision_id: string;
  design_revision_id: string;
  camera_id: string;
  created_at: string;
  manifest: {
    schema_version: "0.1.0";
    generation_id: string;
    scene_revision_id: string;
    design_revision_id: string;
    camera_id: string;
    workflow: { id: string; version: string };
    model_profile: string;
    seed?: number | null;
    input_asset_ids: string[];
    output_asset_ids: string[];
    structured_conditioning?: Record<string, unknown>;
  };
};

export type RevisionSummary = {
  revision_id: string;
  parent_revision_id: string | null;
  command_id: string | null;
  content_hash: string;
  created_at: string;
};

export type SceneEntity = {
  id: string;
  kind: string;
  display_name?: string | null;
  transform?: {
    translation_mm?: [number, number, number];
    rotation_deg?: [number, number, number];
    scale?: [number, number, number];
  };
  geometry?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  locks?: {
    geometry?: boolean;
    transform?: boolean;
    material?: boolean;
  };
};

export type SceneCamera = {
  id: string;
  width_px: number;
  height_px: number;
  intrinsics: {
    fx: number;
    fy: number;
    cx: number;
    cy: number;
  };
  transform: {
    translation_mm: [number, number, number];
    rotation_deg: [number, number, number];
  };
  source_asset_id?: string | null;
  provenance?: {
    source: "user" | "imported" | "measured" | "estimated" | "model_inferred";
    asset_ids?: string[];
    note?: string | null;
  } | null;
  calibration?: {
    quality?: number | null;
    residual?: number | null;
    method?: "manual" | "correspondences" | "imported";
    observations?: Array<{
      world_mm: [number, number, number];
      image_px: [number, number];
      label?: string | null;
    }>;
  } | null;
};

export type SceneDocument = {
  scene_id: string;
  project_id: string;
  entities: SceneEntity[];
  cameras: SceneCamera[];
};

export type SceneRevision = {
  revision_id: string;
  parent_revision_id?: string | null;
  content_hash: string;
  scene: SceneDocument;
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
let csrfToken = "";

function apiPath(path: string) {
  return `${API_BASE}${path}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (init.method && !["GET", "HEAD"].includes(init.method.toUpperCase()) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(apiPath(path), {
    ...init,
    headers,
    credentials: "include"
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  async me() {
    const result = await request<{ authenticated: boolean; username: string; csrf_token: string }>(
      "/api/v1/auth/me"
    );
    csrfToken = result.csrf_token;
    return result;
  },

  async login(username: string, password: string) {
    const result = await request<{ authenticated: boolean; username: string; csrf_token: string }>(
      "/api/v1/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) }
    );
    csrfToken = result.csrf_token;
    return result;
  },

  async logout() {
    await request("/api/v1/auth/logout", { method: "POST" });
    csrfToken = "";
  },

  projects() {
    return request<Project[]>("/api/v1/projects");
  },

  createProject(name: string) {
    return request<Project>("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify({ name })
    });
  },

  async scene(projectId: string): Promise<SceneRevision | null> {
    const response = await fetch(apiPath(`/api/v1/projects/${projectId}/scene`), {
      credentials: "include"
    });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },

  createScene(projectId: string, scene: SceneDocument) {
    return request<SceneRevision>(`/api/v1/projects/${projectId}/scene`, {
      method: "POST",
      body: JSON.stringify(scene)
    });
  },

  revisions(projectId: string) {
    return request<RevisionSummary[]>(`/api/v1/projects/${projectId}/scene/revisions`);
  },

  revert(projectId: string, expectedBaseRevisionId: string, targetRevisionId: string) {
    return request<SceneRevision>(`/api/v1/projects/${projectId}/scene/revert`, {
      method: "POST",
      body: JSON.stringify({
        expected_base_revision_id: expectedBaseRevisionId,
        target_revision_id: targetRevisionId
      })
    });
  },

  upsertCamera(
    projectId: string,
    cameraId: string,
    baseRevisionId: string,
    camera: SceneCamera
  ) {
    return request<{
      revision_id: string;
      content_hash: string;
      camera: SceneCamera;
      scene: SceneDocument;
    }>(`/api/v1/projects/${projectId}/cameras/${cameraId}`, {
      method: "PUT",
      body: JSON.stringify({
        base_revision_id: baseRevisionId,
        camera
      })
    });
  },

  deleteCamera(projectId: string, cameraId: string, baseRevisionId: string) {
    return request<{
      revision_id: string;
      content_hash: string;
      scene: SceneDocument;
    }>(`/api/v1/projects/${projectId}/cameras/${cameraId}`, {
      method: "DELETE",
      body: JSON.stringify({ base_revision_id: baseRevisionId })
    });
  },

  workers() {
    return request<Worker[]>("/api/v1/workers");
  },

  assets(projectId: string) {
    return request<Asset[]>(`/api/v1/projects/${projectId}/assets`);
  },

  jobs(projectId: string) {
    return request<Job[]>(`/api/v1/projects/${projectId}/jobs`);
  },

  generations(projectId: string) {
    return request<Generation[]>(`/api/v1/projects/${projectId}/generations`);
  },

  createGeneration(
    projectId: string,
    designRevisionId: string,
    cameraId: string,
    prompt: string,
    referenceAssetIds: string[] = []
  ) {
    return request<Job>(`/api/v1/projects/${projectId}/generations`, {
      method: "POST",
      body: JSON.stringify({
        design_revision_id: designRevisionId,
        camera_id: cameraId,
        prompt,
        reference_asset_ids: referenceAssetIds,
        idempotency_key: `manual:${designRevisionId}:${cameraId}:${prompt}`
      })
    });
  },

  assetUrl(assetId: string) {
    return apiPath(`/api/v1/assets/${assetId}`);
  },

  cancelJob(jobId: string) {
    return request<Job>(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" });
  },

  designInstruction(projectId: string, text: string) {
    return request<Job>(`/api/v1/projects/${projectId}/design/instructions`, {
      method: "POST",
      body: JSON.stringify({ text })
    });
  },

  createJob(
    projectId: string,
    jobType: string,
    requiredCapabilities: string[],
    payload: Record<string, unknown> = {},
    idempotencyKey?: string
  ) {
    return request<Job>(`/api/v1/projects/${projectId}/jobs`, {
      method: "POST",
      body: JSON.stringify({
        job_type: jobType,
        required_capabilities: requiredCapabilities,
        payload,
        idempotency_key: idempotencyKey
      })
    });
  },

  upload(
    projectId: string,
    file: File,
    role: AssetRole,
    onProgress: (percent: number) => void
  ) {
    return new Promise<{ id: string; sha256: string; role: AssetRole }>((resolve, reject) => {
      const form = new FormData();
      form.append("role", role);
      form.append("file", file);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", apiPath(`/api/v1/projects/${projectId}/assets`));
      xhr.withCredentials = true;
      if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          onProgress(100);
          resolve(JSON.parse(xhr.responseText));
          return;
        }
        reject(new Error(`${xhr.status}: ${xhr.responseText}`));
      };
      xhr.onerror = () => reject(new Error("upload network error"));
      xhr.send(form);
    });
  }
};
