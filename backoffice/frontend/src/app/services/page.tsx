import { fetchApi, type Service } from "@/lib/api";
import Badge from "@/components/Badge";

export const dynamic = "force-dynamic";

function typeVariant(type: string) {
  if (type === "LoadBalancer") return "blue" as const;
  if (type === "NodePort") return "yellow" as const;
  if (type === "ExternalName") return "green" as const;
  return "gray" as const;
}

export default async function ServicesPage() {
  const services = await fetchApi<Service[]>("/api/services");

  const grouped = services.reduce<Record<string, Service[]>>((acc, svc) => {
    (acc[svc.namespace] ??= []).push(svc);
    return acc;
  }, {});

  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">Services</h1>
      <p className="mt-1 text-sm text-gray-500">{services.length} services</p>

      <div className="mt-8 space-y-8">
        {Object.entries(grouped)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([ns, svcs]) => (
            <section key={ns}>
              <h2 className="text-lg font-semibold text-gray-300 mb-3">{ns}</h2>
              <div className="overflow-x-auto rounded-xl border border-gray-800">
                <table className="w-full text-sm">
                  <thead className="bg-gray-900">
                    <tr className="text-left text-gray-500 text-xs uppercase tracking-wider">
                      <th className="px-4 py-3">Name</th>
                      <th className="px-4 py-3">Type</th>
                      <th className="px-4 py-3">Cluster IP</th>
                      <th className="px-4 py-3">External</th>
                      <th className="px-4 py-3">Ports</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-800">
                    {svcs.map((s) => (
                      <tr key={s.name} className="text-gray-300">
                        <td className="px-4 py-3 font-medium text-white">
                          {s.name}
                        </td>
                        <td className="px-4 py-3">
                          <Badge variant={typeVariant(s.type)}>{s.type}</Badge>
                        </td>
                        <td className="px-4 py-3 font-mono text-xs">
                          {s.clusterIP || "-"}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs">
                          {s.loadBalancerIP ||
                            s.externalName ||
                            (s.externalIPs && s.externalIPs.join(", ")) ||
                            "-"}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs">
                          {s.ports
                            .map(
                              (p) =>
                                `${p.port}${p.nodePort ? `:${p.nodePort}` : ""}/${p.protocol}`
                            )
                            .join(", ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
      </div>
    </>
  );
}
