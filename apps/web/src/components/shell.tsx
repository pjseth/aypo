"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { GROUP_LABELS, NAV, type NavItem } from "@/lib/nav";
import { cn } from "@/lib/utils";

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavLink({ item, pathname }: { item: NavItem; pathname: string }) {
  const active = isActive(pathname, item.href);
  return (
    <Link
      href={item.href}
      title={item.question}
      aria-current={active ? "page" : undefined}
      className={cn(
        "block rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
        active
          ? "bg-accent-subtle font-medium text-accent"
          : "text-text-secondary hover:bg-elevated hover:text-text",
      )}
    >
      {item.label}
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const groups = ["intelligence", "explore", "audit"] as const;
  return (
    <nav
      aria-label="Main"
      className="flex w-56 shrink-0 flex-col gap-6 border-r border-border-subtle bg-panel px-3 py-5"
    >
      <Link href="/" className="px-2.5">
        <span className="text-sm font-semibold tracking-tight text-text">AYPO</span>
        <span className="mt-0.5 block text-[11px] leading-tight text-text-faint">
          Social · cultural · commerce intelligence
        </span>
      </Link>

      {groups.map((group) => (
        <div key={group} className="flex flex-col gap-0.5">
          <p className="px-2.5 pb-1 text-[10px] font-semibold tracking-wider text-text-faint uppercase">
            {GROUP_LABELS[group]}
          </p>
          {NAV.filter((n) => n.group === group).map((item) => (
            <NavLink key={item.href} item={item} pathname={pathname} />
          ))}
        </div>
      ))}

      <div className="mt-auto px-2.5">
        {/* Evidence-first is the product's whole claim, so the standard is
            stated in the chrome rather than buried in a help page. */}
        <p className="text-[11px] leading-relaxed text-text-faint">
          Every claim links to the content that supports it, with its sample
          size.
        </p>
      </div>
    </nav>
  );
}

export function PageHeader({
  title,
  question,
  right,
}: {
  title: string;
  question?: string;
  right?: React.ReactNode;
}) {
  return (
    <header className="flex items-start justify-between gap-6 border-b border-border-subtle px-8 py-5">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-text">{title}</h1>
        {question ? (
          <p className="mt-0.5 text-[13px] text-text-muted">{question}</p>
        ) : null}
      </div>
      {right ? <div className="flex shrink-0 items-center gap-2">{right}</div> : null}
    </header>
  );
}
