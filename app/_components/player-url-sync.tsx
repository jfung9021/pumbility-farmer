"use client";

import { useEffect } from "react";
import { usePathname, useSearchParams } from "next/navigation";

export function PlayerUrlSync({ onChange }: { onChange: (pathname: string, playerKey: string | null) => void }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const playerKey = searchParams.get("player");
  useEffect(() => { onChange(pathname, playerKey); }, [onChange, pathname, playerKey]);
  return null;
}
