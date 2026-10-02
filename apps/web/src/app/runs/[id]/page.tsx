import { PageHeader } from "@/components/shell";
import {
  Card,
  CardBody,
  CardHeader,
  Pill,
  SectionTitle,
} from "@/components/ui/primitives";
import { type Coverage, type ResearchQueryRow, serverFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

type RunDetail = {
  id: string;
  objective: string;
  subject_brand: string;
  market_code: string;
  state: string;
  stop_reason: string | null;
  saturation_basis: string | null;
  coverage: Coverage[];
  iterations: {
    iteration_no: number;
    phase: string;
    queries_proposed: number | null;
    queries_admitted: number | null;
    items_admitted: number | null;
    items_analysed: number | null;
    degraded: boolean | null;
  }[];
};

const STRATEGY_STYLE: Record<string, string> = {
  category: "border-claim-observed/40 text-claim-observed bg-claim-observed/8",
  brand: "border-claim-computed/40 text-claim-computed bg-claim-computed/8",
  competitor: "border-claim-interpreted/40 text-claim-interpreted bg-claim-interpreted/8",
  theme: "border-claim-hypothesis/40 text-claim-hypothesis bg-claim-hypothesis/8",
};

export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const run = await serverFetch<RunDetail | null>(`/api/runs/${id}`, null);
  const queries = await serverFetch<ResearchQueryRow[]>(`/api/runs/${id}/queries`, []);

  if (!run) {
    return (
      <>
        <PageHeader title="Research run" />
        <div className="px-8 py-6 text-[13px] text-text-muted">Run not found.</div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={run.objective}
        question={`${run.subject_brand} · ${run.market_code}`}
        right={
          <>
            {run.stop_reason ? <Pill>{run.stop_reason}</Pill> : null}
            <Pill>{run.state}</Pill>
          </>
        }
      />
      <div className="space-y-8 px-8 py-6">
        {/* Brief §7/§28: a surface that shows results without showing which
            sources were unavailable misrepresents the sample. */}
        <section className="space-y-3">
          <SectionTitle hint="which sources this run could actually use">
            Coverage
          </SectionTitle>
          <Card className="overflow-hidden">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-border-subtle text-left text-[11px] tracking-wide text-text-faint uppercase">
                  <th className="px-4 py-2.5 font-medium">Source</th>
                  <th className="px-4 py-2.5 font-medium">Status</th>
                  <th className="px-4 py-2.5 text-right font-medium">Queries</th>
                  <th className="px-4 py-2.5 text-right font-medium">Admitted</th>
                  <th className="px-4 py-2.5 font-medium">Note</th>
                </tr>
              </thead>
              <tbody>
                {run.coverage.map((c) => (
                  <tr key={c.platform} className="border-b border-border-subtle/60 last:border-0">
                    <td className="px-4 py-2.5 font-mono text-text">{c.platform}</td>
                    <td className="px-4 py-2.5">
                      <Pill
                        className={
                          c.available
                            ? "border-confidence-high/40 text-confidence-high"
                            : "border-confidence-insufficient/40 text-confidence-insufficient"
                        }
                      >
                        {c.available ? "available" : "unavailable"}
                      </Pill>
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
                      {c.queries_run}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
                      {c.items_admitted}
                    </td>
                    <td className="max-w-[320px] px-4 py-2.5 text-[12px] text-text-muted">
                      {c.unavailable_reason ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </section>

        <section className="space-y-3">
          <SectionTitle hint="every query records why it was generated">
            Queries
          </SectionTitle>
          <div className="space-y-2">
            {queries.map((q) => (
              <Card key={q.id}>
                <CardHeader>
                  <div className="min-w-0 space-y-1">
                    <p className="truncate text-[13px] font-medium text-text">
                      {q.query_text}
                    </p>
                    <p className="text-[12px] leading-relaxed text-text-muted">
                      {q.rationale}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {q.is_disconfirming ? (
                      <Pill
                        className="border-warning/40 text-warning"
                        // A loop that only looks for confirmation finds it
                        // whether or not it is there.
                      >
                        disconfirming
                      </Pill>
                    ) : null}
                    <Pill className={STRATEGY_STYLE[q.strategy] ?? ""}>{q.strategy}</Pill>
                  </div>
                </CardHeader>
                <CardBody className="pt-0">
                  <p className="font-mono text-[11px] text-text-faint">
                    {q.platform} · {q.results_admitted ?? 0} admitted of{" "}
                    {q.results_total ?? 0} returned
                  </p>
                </CardBody>
              </Card>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}
