import yaml
from fastapi import APIRouter

from app.k8s.client import apps_v1, core_v1, custom_objects, networking_v1

router = APIRouter(prefix="/api", tags=["cluster"])

SYSTEM_NAMESPACES = {
    "kube-system", "kube-public", "kube-node-lease",
    "cattle-system", "cattle-fleet-system", "cattle-fleet-local-system",
    "cattle-impersonation-system",
}


def _parse_container(c) -> dict:
    return {
        "name": c.name,
        "image": c.image,
        "ports": [
            {"name": p.name, "port": p.container_port, "protocol": p.protocol}
            for p in (c.ports or [])
        ],
        "resources": {
            "requests": dict(c.resources.requests) if c.resources and c.resources.requests else {},
            "limits": dict(c.resources.limits) if c.resources and c.resources.limits else {},
        },
    }


def _detect_authentik(ingress) -> bool:
    annotations = ingress.metadata.annotations or {}
    for rule in ingress.spec.rules or []:
        for path in (rule.http.paths if rule.http else []):
            if path.backend.service and path.backend.service.name == "authentik-proxy":
                return True
    if annotations.get("nginx.ingress.kubernetes.io/proxy-ssl-name", "").startswith("authentik"):
        return True
    return False


# ── Overview ──────────────────────────────────────────────────────────


def _count_authentik_httproutes() -> int:
    """Count HTTPRoutes whose backend is authentik-server."""
    try:
        routes = custom_objects().list_cluster_custom_object(
            group="gateway.networking.k8s.io",
            version="v1",
            plural="httproutes",
        )
        count = 0
        for r in routes.get("items", []):
            for rule in r.get("spec", {}).get("rules", []):
                for ref in rule.get("backendRefs", []):
                    if ref.get("name") == "authentik-server":
                        count += 1
                        break
        return count
    except Exception:
        return 0


@router.get("/overview")
def get_overview():
    namespaces = core_v1().list_namespace()
    deployments = apps_v1().list_deployment_for_all_namespaces()
    services = core_v1().list_service_for_all_namespaces()
    ingresses = networking_v1().list_ingress_for_all_namespaces()
    pods = core_v1().list_pod_for_all_namespaces()
    nodes = core_v1().list_node()

    user_ns = [n for n in namespaces.items if n.metadata.name not in SYSTEM_NAMESPACES]
    ingress_authentik = sum(1 for i in ingresses.items if _detect_authentik(i))
    httproute_authentik = _count_authentik_httproutes()
    authentik_count = ingress_authentik + httproute_authentik
    running_pods = sum(1 for p in pods.items if p.status.phase == "Running")

    return {
        "namespaces": len(user_ns),
        "deployments": len(deployments.items),
        "services": len(services.items),
        "ingresses": len(ingresses.items),
        "pods": {"total": len(pods.items), "running": running_pods},
        "nodes": len(nodes.items),
        "authentik_protected": authentik_count,
    }


# ── Applications / Deployments ────────────────────────────────────────


@router.get("/applications")
def list_applications():
    deployments = apps_v1().list_deployment_for_all_namespaces()
    pods = core_v1().list_pod_for_all_namespaces()
    ingresses = networking_v1().list_ingress_for_all_namespaces()

    pod_map: dict[str, list] = {}
    for p in pods.items:
        key = f"{p.metadata.namespace}"
        pod_map.setdefault(key, []).append(p)

    ingress_map: dict[str, list] = {}
    for i in ingresses.items:
        ingress_map.setdefault(i.metadata.namespace, []).append(i)

    results = []
    for d in deployments.items:
        ns = d.metadata.namespace
        labels = d.spec.selector.match_labels or {}

        dep_pods = []
        for p in pod_map.get(ns, []):
            pod_labels = p.metadata.labels or {}
            if all(pod_labels.get(k) == v for k, v in labels.items()):
                dep_pods.append({
                    "name": p.metadata.name,
                    "phase": p.status.phase,
                    "ip": p.status.pod_ip,
                    "node": p.spec.node_name,
                    "restarts": sum(
                        cs.restart_count
                        for cs in (p.status.container_statuses or [])
                    ),
                })

        ns_ingresses = ingress_map.get(ns, [])
        hosts = []
        authentik = False
        for i in ns_ingresses:
            if _detect_authentik(i):
                authentik = True
            for rule in i.spec.rules or []:
                if rule.host:
                    hosts.append(rule.host)

        results.append({
            "name": d.metadata.name,
            "namespace": ns,
            "replicas": {
                "desired": d.spec.replicas,
                "ready": d.status.ready_replicas or 0,
                "available": d.status.available_replicas or 0,
            },
            "containers": [_parse_container(c) for c in d.spec.template.spec.containers],
            "pods": dep_pods,
            "hosts": hosts,
            "authentik_protected": authentik,
            "created": d.metadata.creation_timestamp.isoformat() if d.metadata.creation_timestamp else None,
        })

    results.sort(key=lambda x: (x["namespace"], x["name"]))
    return results


# ── Ingresses ─────────────────────────────────────────────────────────


@router.get("/ingresses")
def list_ingresses():
    ingresses = networking_v1().list_ingress_for_all_namespaces()
    results = []
    for i in ingresses.items:
        annotations = i.metadata.annotations or {}
        tls_hosts = []
        for t in (i.spec.tls or []):
            tls_hosts.extend(t.hosts or [])

        rules = []
        for r in (i.spec.rules or []):
            paths = []
            for p in (r.http.paths if r.http else []):
                paths.append({
                    "path": p.path,
                    "pathType": p.path_type,
                    "service": p.backend.service.name if p.backend.service else None,
                    "port": (
                        p.backend.service.port.number
                        if p.backend.service and p.backend.service.port
                        else None
                    ),
                })
            rules.append({"host": r.host, "paths": paths})

        results.append({
            "name": i.metadata.name,
            "namespace": i.metadata.namespace,
            "ingressClass": i.spec.ingress_class_name,
            "tls_hosts": tls_hosts,
            "rules": rules,
            "authentik_protected": _detect_authentik(i),
            "annotations": {
                k: v for k, v in annotations.items()
                if k.startswith("nginx.ingress.kubernetes.io/") or k.startswith("cert-manager")
            },
        })

    results.sort(key=lambda x: (x["namespace"], x["name"]))
    return results


# ── Services ──────────────────────────────────────────────────────────


@router.get("/services")
def list_services():
    services = core_v1().list_service_for_all_namespaces()
    results = []
    for s in services.items:
        ports = []
        for p in (s.spec.ports or []):
            ports.append({
                "name": p.name,
                "port": p.port,
                "targetPort": str(p.target_port),
                "protocol": p.protocol,
                "nodePort": p.node_port,
            })

        results.append({
            "name": s.metadata.name,
            "namespace": s.metadata.namespace,
            "type": s.spec.type,
            "clusterIP": s.spec.cluster_ip,
            "externalIPs": s.spec.external_i_ps,
            "externalName": s.spec.external_name,
            "loadBalancerIP": (
                s.status.load_balancer.ingress[0].ip
                if s.status and s.status.load_balancer and s.status.load_balancer.ingress
                else None
            ),
            "ports": ports,
            "selector": dict(s.spec.selector) if s.spec.selector else None,
        })

    results.sort(key=lambda x: (x["namespace"], x["name"]))
    return results


# ── Nodes ─────────────────────────────────────────────────────────────


@router.get("/nodes")
def list_nodes():
    nodes = core_v1().list_node()
    results = []
    for n in nodes.items:
        addresses = {a.type: a.address for a in (n.status.addresses or [])}
        conditions = {
            c.type: c.status for c in (n.status.conditions or [])
        }
        allocatable = n.status.allocatable or {}
        capacity = n.status.capacity or {}

        results.append({
            "name": n.metadata.name,
            "internalIP": addresses.get("InternalIP"),
            "hostname": addresses.get("Hostname"),
            "conditions": conditions,
            "ready": conditions.get("Ready") == "True",
            "capacity": {
                "cpu": capacity.get("cpu"),
                "memory": capacity.get("memory"),
                "pods": capacity.get("pods"),
                "gpu": capacity.get("nvidia.com/gpu"),
            },
            "allocatable": {
                "cpu": allocatable.get("cpu"),
                "memory": allocatable.get("memory"),
                "pods": allocatable.get("pods"),
                "gpu": allocatable.get("nvidia.com/gpu"),
            },
            "labels": dict(n.metadata.labels or {}),
            "kubelet_version": n.status.node_info.kubelet_version if n.status.node_info else None,
            "os_image": n.status.node_info.os_image if n.status.node_info else None,
        })

    results.sort(key=lambda x: x["name"])
    return results


# ── Authentik ─────────────────────────────────────────────────────────


@router.get("/authentik")
def list_authentik_apps():
    ingresses = networking_v1().list_ingress_for_all_namespaces()
    services = core_v1().list_service_for_all_namespaces()

    proxy_services = {}
    for s in services.items:
        if s.spec.type == "ExternalName" and s.spec.external_name and "authentik" in s.spec.external_name:
            proxy_services[f"{s.metadata.namespace}/{s.metadata.name}"] = {
                "name": s.metadata.name,
                "namespace": s.metadata.namespace,
                "externalName": s.spec.external_name,
            }

    results = []
    for i in ingresses.items:
        if not _detect_authentik(i):
            continue

        hosts = []
        backend_service = None
        for rule in (i.spec.rules or []):
            if rule.host:
                hosts.append(rule.host)
            for path in (rule.http.paths if rule.http else []):
                if path.backend.service:
                    backend_service = path.backend.service.name

        proxy_key = f"{i.metadata.namespace}/{backend_service}" if backend_service else None
        proxy_info = proxy_services.get(proxy_key)

        results.append({
            "name": i.metadata.name,
            "namespace": i.metadata.namespace,
            "hosts": hosts,
            "proxy_service": proxy_info,
            "tls": bool(i.spec.tls),
            "annotations": {
                k: v for k, v in (i.metadata.annotations or {}).items()
                if "authentik" in k.lower() or "proxy-ssl" in k.lower() or "backend-protocol" in k.lower()
            },
        })

    results.sort(key=lambda x: (x["namespace"], x["name"]))
    return results


# ── Cloudflared / External Resources ─────────────────────────────────


ENVOY_PREFIX = "envoy-envoy-gateway-system-"


@router.get("/external-resources")
def list_external_resources():
    """Parse the cloudflared ConfigMap to find non-cluster ingress entries."""
    try:
        cm = core_v1().read_namespaced_config_map("cloudflared-config", "cloudflared")
    except Exception:
        return []

    config_yaml = cm.data.get("config.yaml", "")
    try:
        cfg = yaml.safe_load(config_yaml)
    except Exception:
        return []

    ingress_rules = cfg.get("ingress", [])

    results = []
    for rule in ingress_rules:
        hostname = rule.get("hostname", "")
        service = rule.get("service", "")

        # Skip catch-all, wildcard, and http_status rules
        if not hostname or hostname.startswith("*") or service.startswith("http_status"):
            continue

        # Skip entries routing through envoy gateway (those are cluster apps)
        if ENVOY_PREFIX in service:
            continue

        is_cluster = ".svc.cluster.local" in service
        origin = rule.get("originRequest", {})

        results.append({
            "hostname": hostname,
            "service": service,
            "is_cluster_svc": is_cluster,
            "http2": origin.get("http2Origin", False),
        })

    results.sort(key=lambda x: x["hostname"])
    return results
