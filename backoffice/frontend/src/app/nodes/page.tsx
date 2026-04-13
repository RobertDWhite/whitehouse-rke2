import { fetchApi, type Node } from "@/lib/api";
import Badge from "@/components/Badge";

export const dynamic = "force-dynamic";

export default async function NodesPage() {
  const nodes = await fetchApi<Node[]>("/api/nodes");

  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">Nodes</h1>
      <p className="mt-1 text-sm text-gray-500">{nodes.length} nodes</p>

      <div className="mt-8 grid gap-5 lg:grid-cols-2">
        {nodes.map((node) => (
          <div
            key={node.name}
            className="rounded-xl border border-gray-800 bg-gray-900 p-5"
          >
            <div className="flex items-center gap-3">
              <h3 className="font-semibold text-white">{node.name}</h3>
              <Badge variant={node.ready ? "green" : "red"}>
                {node.ready ? "Ready" : "NotReady"}
              </Badge>
              {node.capacity.gpu && (
                <Badge variant="blue">GPU: {node.capacity.gpu}</Badge>
              )}
            </div>

            <div className="mt-4 grid grid-cols-2 gap-y-2 gap-x-8 text-sm">
              <div className="text-gray-400">
                IP:{" "}
                <span className="text-gray-200 font-mono">
                  {node.internalIP}
                </span>
              </div>
              <div className="text-gray-400">
                Kubelet:{" "}
                <span className="text-gray-200">{node.kubelet_version}</span>
              </div>
              <div className="text-gray-400">
                OS: <span className="text-gray-200">{node.os_image}</span>
              </div>
              <div className="text-gray-400">
                Pods:{" "}
                <span className="text-gray-200">
                  {node.allocatable.pods} allocatable
                </span>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-4">
              <div className="rounded-lg bg-gray-800/50 p-3">
                <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">
                  CPU
                </p>
                <p className="text-sm text-gray-200">
                  {node.allocatable.cpu} / {node.capacity.cpu}
                </p>
              </div>
              <div className="rounded-lg bg-gray-800/50 p-3">
                <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">
                  Memory
                </p>
                <p className="text-sm text-gray-200">
                  {node.allocatable.memory} / {node.capacity.memory}
                </p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
