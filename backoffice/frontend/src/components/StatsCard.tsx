import type { LucideIcon } from "lucide-react";

interface Props {
  label: string;
  value: string | number;
  sub?: string;
  icon: LucideIcon;
}

export default function StatsCard({ label, value, sub, icon: Icon }: Props) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <div className="flex items-center gap-3">
        <div className="rounded-lg bg-gray-800 p-2">
          <Icon className="h-5 w-5 text-blue-400" />
        </div>
        <span className="text-sm text-gray-400">{label}</span>
      </div>
      <p className="mt-3 text-3xl font-semibold tracking-tight">{value}</p>
      {sub && <p className="mt-1 text-sm text-gray-500">{sub}</p>}
    </div>
  );
}
