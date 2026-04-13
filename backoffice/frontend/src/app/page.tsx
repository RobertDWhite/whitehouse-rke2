import {
  Boxes,
  Globe,
  Network,
  Server,
  Container,
  ShieldCheck,
  Layers,
  ExternalLink,
} from "lucide-react";
import StatsCard from "@/components/StatsCard";
import { fetchApi, type Overview, type ExternalResource } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const [data, externalResources] = await Promise.all([
    fetchApi<Overview>("/api/overview"),
    fetchApi<ExternalResource[]>("/api/external-resources").catch(() => []),
  ]);

  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">Cluster Overview</h1>
      <p className="mt-1 text-sm text-gray-500">whitehouse-rke2 at a glance</p>

      <div className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <StatsCard icon={Layers} label="Namespaces" value={data.namespaces} />
        <StatsCard icon={Boxes} label="Deployments" value={data.deployments} href="/applications" />
        <StatsCard
          icon={Container}
          label="Pods"
          value={data.pods.running}
          sub={`${data.pods.total} total`}
          href="/applications"
        />
        <StatsCard icon={Server} label="Nodes" value={data.nodes} href="/nodes" />
        <StatsCard icon={Globe} label="Ingresses" value={data.ingresses} href="/ingresses" />
        <StatsCard icon={Network} label="Services" value={data.services} href="/services" />
        <StatsCard
          icon={ShieldCheck}
          label="Authentik Protected"
          value={data.authentik_protected}
          href="/authentik"
        />
        {externalResources.length > 0 && (
          <StatsCard
            icon={ExternalLink}
            label="External Resources"
            value={externalResources.length}
            sub="via Cloudflared"
          />
        )}
      </div>

      {externalResources.length > 0 && (
        <div className="mt-10">
          <h2 className="text-lg font-semibold tracking-tight">External Resources</h2>
          <p className="mt-1 text-sm text-gray-500">
            Non-cluster services routed via Cloudflared tunnel
          </p>
          <div className="mt-4 overflow-hidden rounded-xl border border-gray-800">
            <table className="w-full text-sm">
              <thead className="bg-gray-900 text-left text-gray-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Hostname</th>
                  <th className="px-4 py-3 font-medium">Backend Service</th>
                  <th className="px-4 py-3 font-medium">Type</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {externalResources.map((r) => (
                  <tr key={r.hostname} className="hover:bg-gray-900/50">
                    <td className="px-4 py-3">
                      <a
                        href={`https://${r.hostname}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-400 hover:underline"
                      >
                        {r.hostname}
                      </a>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-400">
                      {r.service}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${r.is_cluster_svc ? "bg-blue-900/50 text-blue-300" : "bg-amber-900/50 text-amber-300"}`}>
                        {r.is_cluster_svc ? "Cluster SVC" : "External"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
