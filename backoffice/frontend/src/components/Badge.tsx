import { clsx } from "clsx";

interface Props {
  children: React.ReactNode;
  variant?: "green" | "red" | "yellow" | "blue" | "gray";
}

const COLORS = {
  green: "bg-emerald-900/50 text-emerald-400 border-emerald-800",
  red: "bg-red-900/50 text-red-400 border-red-800",
  yellow: "bg-amber-900/50 text-amber-400 border-amber-800",
  blue: "bg-blue-900/50 text-blue-400 border-blue-800",
  gray: "bg-gray-800 text-gray-400 border-gray-700",
};

export default function Badge({ children, variant = "gray" }: Props) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
        COLORS[variant]
      )}
    >
      {children}
    </span>
  );
}
