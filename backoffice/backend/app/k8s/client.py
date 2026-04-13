from kubernetes import client, config

from app.config import settings

_api_client: client.ApiClient | None = None


def _get_api_client() -> client.ApiClient:
    global _api_client
    if _api_client is None:
        if settings.k8s_in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()
        _api_client = client.ApiClient()
    return _api_client


def core_v1() -> client.CoreV1Api:
    return client.CoreV1Api(_get_api_client())


def apps_v1() -> client.AppsV1Api:
    return client.AppsV1Api(_get_api_client())


def networking_v1() -> client.NetworkingV1Api:
    return client.NetworkingV1Api(_get_api_client())


def custom_objects() -> client.CustomObjectsApi:
    return client.CustomObjectsApi(_get_api_client())
