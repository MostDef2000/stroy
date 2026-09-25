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

export type SceneDocument = {
  scene_id: string;
  project_id: string;
  entities: SceneEntity[];
  cameras: unknown[];
};

export type SceneRevision = {
  revision_id: string;
  content_hash: string;
  scene: SceneDocument;
};

let csrfToken = "";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (init.method && !["GET", "HEAD"].includes(init.method.toUpperCase()) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(path, {
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
    const response = await fetch(`/api/v1/projects/${projectId}/scene`, {
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

  workers() {
    return request<Worker[]>("/api/v1/workers");
  },

  upload(projectId: string, file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<{ id: string; sha256: string }>(`/api/v1/projects/${projectId}/assets`, {
      method: "POST",
      body: form
    });
  }
};
