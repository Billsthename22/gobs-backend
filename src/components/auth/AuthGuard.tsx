"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { getToken } from "@/lib/auth";

export default function AuthGuard({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (pathname === "/login") {
      setChecking(false);
      return;
    }

    const token = getToken();

    if (!token) {
      router.replace("/login");
      return;
    }

    setChecking(false);
  }, [pathname, router]);

  if (checking && pathname !== "/login") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-zinc-50">
        <div className="text-sm text-zinc-500">
          Checking authentication...
        </div>
      </main>
    );
  }

  return <>{children}</>;
}
