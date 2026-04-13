import { fetchApi, type AuthentikApp } from "@/lib/api";
import Badge from "@/components/Badge";

export const dynamic = "force-dynamic";

export default async function AuthentikPage() {
  const apps = await fetchApi<AuthentikApp[]>("/api/authentik");

  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">Authentik Integration</h1>
      <p className="mt-1 text-sm text-gray-500">
        {apps.length} applications behind Authentik proxy
      </p>

      <div className="mt-8 space-y-4">
        {apps.map((app) => (
          <div
            key={`${app.namespace}/${app.name}`}
            className="rounded-xl border border-gray-800 bg-gray-900 p-5"
          >
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="font-semibold text-white">{app.name}</h3>
              <Badge variant="gray">{app.namespace}</Badge>
              {app.tls && <Badge variant="green">TLS</Badge>}
              {app.hosts.map((h) => (
                <Badge key={h} variant="blue">
                  {h}
                </Badge>
              ))}
            </div>

            {app.proxy_service && (
              <div className="mt-3 text-sm text-gray-400">
                Proxy via{" "}
                <span className="font-mono text-gray-300">
                  {app.proxy_service.name}
                </span>{" "}
                &rarr;{" "}
                <span className="font-mono text-gray-300">
                  {app.proxy_service.externalName}
                </span>
              </div>
            )}

            {Object.keys(app.annotations).length > 0 && (
              <div className="mt-3 space-y-1">
                {Object.entries(app.annotations).map(([k, v]) => (
                  <div key={k} className="text-xs font-mono text-gray-500">
                    <span className="text-gray-400">{k}</span>: {v}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
