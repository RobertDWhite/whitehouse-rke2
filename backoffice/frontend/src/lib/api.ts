const BASE = process.env.NEXT_PUBLIC_API_URL || "";

export async function fetchApi<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { next: { revalidate: 30 } });
  if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
  return res.json();
}

export interface Overview {
  namespaces: number;
  deployments: number;
  services: number;
  ingresses: number;
  pods: { total: number; running: number };
  nodes: number;
  authentik_protected: number;
}

export interface Container {
  name: string;
  image: string;
  ports: { name: string | null; port: number; protocol: string }[];
  resources: { requests: Record<string, string>; limits: Record<string, string> };
}

export interface Pod {
  name: string;
  phase: string;
  ip: string | null;
  node: string | null;
  restarts: number;
}

export interface Application {
  name: string;
  namespace: string;
  replicas: { desired: number; ready: number; available: number };
  containers: Container[];
  pods: Pod[];
  hosts: string[];
  authentik_protected: boolean;
  created: string | null;
}

export interface IngressRule {
  host: string | null;
  paths: { path: string; pathType: string; service: string | null; port: number | null }[];
}

export interface Ingress {
  name: string;
  namespace: string;
  ingressClass: string | null;
  tls_hosts: string[];
  rules: IngressRule[];
  authentik_protected: boolean;
  annotations: Record<string, string>;
}

export interface Service {
  name: string;
  namespace: string;
  type: string;
  clusterIP: string | null;
  externalIPs: string[] | null;
  externalName: string | null;
  loadBalancerIP: string | null;
  ports: { name: string | null; port: number; targetPort: string; protocol: string; nodePort: number | null }[];
  selector: Record<string, string> | null;
}

export interface Node {
  name: string;
  internalIP: string | null;
  hostname: string | null;
  conditions: Record<string, string>;
  ready: boolean;
  capacity: { cpu: string | null; memory: string | null; pods: string | null; gpu: string | null };
  allocatable: { cpu: string | null; memory: string | null; pods: string | null; gpu: string | null };
  labels: Record<string, string>;
  kubelet_version: string | null;
  os_image: string | null;
}

export interface AuthentikApp {
  name: string;
  namespace: string;
  hosts: string[];
  proxy_service: { name: string; namespace: string; externalName: string } | null;
  tls: boolean;
  annotations: Record<string, string>;
}
