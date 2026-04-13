import {
  Boxes,
  Globe,
  Network,
  Server,
  Container,
  ShieldCheck,
  Layers,
} from "lucide-react";
import StatsCard from "@/components/StatsCard";
import { fetchApi, type Overview } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const data = await fetchApi<Overview>("/api/overview");

  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">Cluster Overview</h1>
      <p className="mt-1 text-sm text-gray-500">whitehouse-rke2 at a glance</p>

      <div className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <StatsCard icon={Layers} label="Namespaces" value={data.namespaces} />
        <StatsCard icon={Boxes} label="Deployments" value={data.deployments} />
        <StatsCard
          icon={Container}
          label="Pods"
          value={data.pods.running}
          sub={`${data.pods.total} total`}
        />
        <StatsCard icon={Server} label="Nodes" value={data.nodes} />
        <StatsCard icon={Globe} label="Ingresses" value={data.ingresses} />
        <StatsCard icon={Network} label="Services" value={data.services} />
        <StatsCard
          icon={ShieldCheck}
          label="Authentik Protected"
          value={data.authentik_protected}
        />
      </div>
    </>
  );
}
