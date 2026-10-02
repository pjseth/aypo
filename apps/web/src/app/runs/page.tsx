import Link from "next/link";
import { PageHeader } from "@/components/shell";
import {
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Pill,
  SectionTitle,
} from "@/components/ui/primitives";
import { type RunSummary, serverFetch } from "@/lib/api";
import { NAV } from "@/lib/nav";

const nav = NAV.find((n) => n.href === "/runs")!;

export const dynamic = "force-dynamic";

const STATE_STYLE: Record<string, string> = {
  completed: "border-confidence-high/40 text-confidence-high bg-confidence-high/8",
  iterating: "border-accent/40 text-accent bg-accent/10",
  failed: "border-danger/40 text-danger bg-danger/8",
  cancelled: "border-text-muted/40 text-text-muted bg-text-muted/8",
};

export default async function RunsPage() {
  const runs = await serverFetch<RunSummary[]>("/api/runs?limit=20", []);

  if (runs.length === 0) {
    return (
      <>
        <PageHeader title="Research Runs" question={nav.question} />
        <div className="px-8 py-6">
          <EmptyState
            title="No research runs yet"
            body="Each run records its objective, the queries it generated and why, the sources it searched, what it found and why it stopped — so any conclusion can be traced back through the reasoning that reached it."
          />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Research Runs" question={nav.question} />
      <div className="space-y-4 px-8 py-6">
        <SectionTitle hint="click a run to replay how it reached its conclusions">
          Runs
        </SectionTitle>
        <div className="space-y-3">
          {runs.map((run) => (
            <Link key={run.id} href={`/runs/${run.id}`} className="block">
              <Card className="transition-colors hover:border-border-strong">
                <CardHeader>
                  <div className="space-y-1">
                    <p className="text-[13px] font-medium text-text">{run.objective}</p>
                    <p className="font-mono text-[11px] text-text-faint">
                      {run.subject_brand} · {run.market_code} · iteration {run.iteration_no}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {run.degraded ? (
                      <Pill className="border-warning/40 text-warning">degraded</Pill>
                    ) : null}
                    <Pill className={STATE_STYLE[run.state] ?? ""}>{run.state}</Pill>
                  </div>
                </CardHeader>
                {run.stop_reason ? (
                  <CardBody className="pt-0">
                    <p className="text-[12px] text-text-muted">
                      {/* Brief §26 requires the reason a run stopped be recorded,
                          so it is shown rather than left as "finished". */}
                      stopped: <span className="font-mono">{run.stop_reason}</span>
                    </p>
                  </CardBody>
                ) : null}
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </>
  );
}
