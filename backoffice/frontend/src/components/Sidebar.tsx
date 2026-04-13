"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";
import {
  LayoutDashboard,
  Boxes,
  Globe,
  Network,
  Server,
  ShieldCheck,
} from "lucide-react";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/applications", label: "Applications", icon: Boxes },
  { href: "/ingresses", label: "Ingresses", icon: Globe },
  { href: "/services", label: "Services", icon: Network },
  { href: "/nodes", label: "Nodes", icon: Server },
  { href: "/authentik", label: "Authentik", icon: ShieldCheck },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-30 w-56 bg-gray-900 border-r border-gray-800 flex flex-col">
      <div className="h-16 flex items-center px-5 border-b border-gray-800">
        <span className="text-lg font-semibold tracking-tight text-white">
          Backoffice
        </span>
      </div>
      <nav className="flex-1 py-4 px-3 space-y-1">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-blue-600/20 text-blue-400"
                  : "text-gray-400 hover:bg-gray-800 hover:text-gray-200"
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="px-5 py-4 border-t border-gray-800 text-xs text-gray-500">
        whitehouse-rke2
      </div>
    </aside>
  );
}
