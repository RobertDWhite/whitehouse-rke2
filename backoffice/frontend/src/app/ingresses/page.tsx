import { fetchApi, type Ingress } from "@/lib/api";
import Badge from "@/components/Badge";

export const dynamic = "force-dynamic";

export default async function IngressesPage() {
  const ingresses = await fetchApi<Ingress[]>("/api/ingresses");

  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">Ingresses</h1>
      <p className="mt-1 text-sm text-gray-500">{ingresses.length} ingress resources</p>

      <div className="mt-8 space-y-4">
        {ingresses.map((ing) => (
          <div
            key={`${ing.namespace}/${ing.name}`}
            className="rounded-xl border border-gray-800 bg-gray-900 p-5"
          >
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="font-semibold text-white">{ing.name}</h3>
              <Badge variant="gray">{ing.namespace}</Badge>
              {ing.authentik_protected && <Badge variant="blue">Authentik</Badge>}
              {ing.tls_hosts.length > 0 && <Badge variant="green">TLS</Badge>}
              {ing.ingressClass && <Badge variant="gray">{ing.ingressClass}</Badge>}
            </div>

            <div className="mt-4 space-y-3">
              {ing.rules.map((rule, ri) => (
                <div key={ri} className="text-sm">
                  <span className="text-blue-400 font-mono">
                    {rule.host || "*"}
                  </span>
                  <div className="ml-4 mt-1 space-y-1">
                    {rule.paths.map((p, pi) => (
                      <div key={pi} className="flex gap-4 text-gray-400">
                        <span className="font-mono text-xs text-gray-300">
                          {p.path}
                        </span>
                        <span>
                          &rarr; {p.service}:{p.port}
                        </span>
                        <span className="text-gray-600">{p.pathType}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {Object.keys(ing.annotations).length > 0 && (
              <details className="mt-4">
                <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-400">
                  Annotations ({Object.keys(ing.annotations).length})
                </summary>
                <div className="mt-2 space-y-1">
                  {Object.entries(ing.annotations).map(([k, v]) => (
                    <div key={k} className="text-xs font-mono text-gray-500">
                      <span className="text-gray-400">{k}</span>: {v}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
