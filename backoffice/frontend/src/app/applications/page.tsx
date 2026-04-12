import { fetchApi, type Application } from "@/lib/api";
import Badge from "@/components/Badge";

export const dynamic = "force-dynamic";

function phaseColor(phase: string) {
  if (phase === "Running") return "green" as const;
  if (phase === "Pending") return "yellow" as const;
  return "red" as const;
}

export default async function ApplicationsPage() {
  const apps = await fetchApi<Application[]>("/api/applications");

  const grouped = apps.reduce<Record<string, Application[]>>((acc, app) => {
    (acc[app.namespace] ??= []).push(app);
    return acc;
  }, {});

  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">Applications</h1>
      <p className="mt-1 text-sm text-gray-500">
        {apps.length} deployments across {Object.keys(grouped).length} namespaces
      </p>

      <div className="mt-8 space-y-8">
        {Object.entries(grouped)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([ns, nsApps]) => (
            <section key={ns}>
              <h2 className="text-lg font-semibold text-gray-300 mb-3">
                {ns}
              </h2>
              <div className="space-y-4">
                {nsApps.map((app) => (
                  <div
                    key={`${app.namespace}/${app.name}`}
                    className="rounded-xl border border-gray-800 bg-gray-900 p-5"
                  >
                    <div className="flex flex-wrap items-center gap-3">
                      <h3 className="font-semibold text-white">{app.name}</h3>
                      <Badge
                        variant={
                          app.replicas.ready === app.replicas.desired
                            ? "green"
                            : "yellow"
                        }
                      >
                        {app.replicas.ready}/{app.replicas.desired} ready
                      </Badge>
                      {app.authentik_protected && (
                        <Badge variant="blue">Authentik</Badge>
                      )}
                      {app.hosts.map((h) => (
                        <Badge key={h} variant="gray">
                          {h}
                        </Badge>
                      ))}
                    </div>

                    {/* Containers */}
                    <div className="mt-4 space-y-2">
                      {app.containers.map((c) => (
                        <div
                          key={c.name}
                          className="flex flex-wrap gap-x-6 gap-y-1 text-sm"
                        >
                          <span className="text-gray-400">
                            image:{" "}
                            <span className="text-gray-200 font-mono text-xs">
                              {c.image}
                            </span>
                          </span>
                          {c.ports.length > 0 && (
                            <span className="text-gray-400">
                              ports:{" "}
                              <span className="text-gray-200">
                                {c.ports.map((p) => p.port).join(", ")}
                              </span>
                            </span>
                          )}
                          {(c.resources.requests.cpu || c.resources.limits.cpu) && (
                            <span className="text-gray-400">
                              cpu:{" "}
                              <span className="text-gray-200">
                                {c.resources.requests.cpu || "?"}/
                                {c.resources.limits.cpu || "?"}
                              </span>
                            </span>
                          )}
                          {(c.resources.requests.memory ||
                            c.resources.limits.memory) && (
                            <span className="text-gray-400">
                              mem:{" "}
                              <span className="text-gray-200">
                                {c.resources.requests.memory || "?"}/
                                {c.resources.limits.memory || "?"}
                              </span>
                            </span>
                          )}
                        </div>
                      ))}
                    </div>

                    {/* Pods */}
                    {app.pods.length > 0 && (
                      <div className="mt-4 overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="text-left text-gray-500 text-xs uppercase tracking-wider">
                              <th className="pb-2 pr-4">Pod</th>
                              <th className="pb-2 pr-4">Status</th>
                              <th className="pb-2 pr-4">IP</th>
                              <th className="pb-2 pr-4">Node</th>
                              <th className="pb-2">Restarts</th>
                            </tr>
                          </thead>
                          <tbody className="text-gray-300">
                            {app.pods.map((p) => (
                              <tr key={p.name}>
                                <td className="py-1 pr-4 font-mono text-xs">
                                  {p.name}
                                </td>
                                <td className="py-1 pr-4">
                                  <Badge variant={phaseColor(p.phase)}>
                                    {p.phase}
                                  </Badge>
                                </td>
                                <td className="py-1 pr-4 font-mono text-xs">
                                  {p.ip || "-"}
                                </td>
                                <td className="py-1 pr-4 text-xs">
                                  {p.node || "-"}
                                </td>
                                <td className="py-1 text-xs">{p.restarts}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ))}
      </div>
    </>
  );
}
