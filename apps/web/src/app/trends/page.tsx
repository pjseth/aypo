import { PageHeader } from "@/components/shell";
import {
  Card,
  CardBody,
  CardHeader,
  InsufficientData,
  Pill,
  SampleSize,
  SectionTitle,
  TrendBadge,
  type TrendState,
} from "@/components/ui/primitives";
import { serverFetch } from "@/lib/api";
import { NAV } from "@/lib/nav";

const nav = NAV.find((n) => n.href === "/trends")!;

export const dynamic = "force-dynamic";

type ClusterRow = {
  id: string;
  cluster_key: string;
  label: string | null;
  description: string | null;
  status: string;
  size_core: number;
  size_secondary: number;
  current_classification: string | null;
  current_refusal_reason: string | null;
  trend_eligible: boolean;
};

type Trends = {
  clusters: ClusterRow[];
  latest_cluster_run: {
    n_items_clustered: number;
    n_clusters: number;
    noise_fraction: number | string;
  } | null;
  caveat: string;
};

export default async function TrendsPage() {
  const data = await serverFetch<Trends | null>("/api/trends", null);
  const clusters = data?.clusters ?? [];
  const run = data?.latest_cluster_run ?? null;

  return (
    <>
      <PageHeader
        title="Trends & Opportunities"
        question={nav.question}
        right={run ? <SampleSize n={run.n_items_clustered} unit="items clustered" /> : null}
      />
      <div className="space-y-6 px-8 py-6">
        {clusters.length === 0 ? (
          <InsufficientData reason="Clustering has not produced any themes yet. It needs at least 60 analysed items, because a theme claimed from fewer would not be reportable — and growth or decline additionally needs enough temporal coverage to rule out an artefact of when the content was collected." />
        ) : (
          <>
            <p className="max-w-3xl text-[12px] leading-relaxed text-text-muted">
              {data!.caveat}
            </p>

            <section className="space-y-3">
              <SectionTitle
                hint={
                  run
                    ? `${run.n_clusters} themes · ${Math.round(Number(run.noise_fraction) * 100)}% unclustered`
                    : undefined
                }
              >
                Discovered themes
              </SectionTitle>
              <div className="grid gap-3 lg:grid-cols-2">
                {clusters.map((c) => (
                  <Card key={c.id}>
                    <CardHeader>
                      <div className="min-w-0 space-y-1">
                        <p className="text-[13px] font-medium text-text">
                          {/* A cluster the labeller judged incoherent is shown as
                              such rather than given a name the members do not
                              support. */}
                          {c.label ?? (
                            <span className="text-text-muted italic">
                              unlabelled — members share nothing nameable
                            </span>
                          )}
                        </p>
                        {c.description ? (
                          <p className="text-[12px] leading-relaxed text-text-muted">
                            {c.description}
                          </p>
                        ) : null}
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1.5">
                        <TrendBadge
                          state={
                            (c.current_classification as TrendState) ?? "weak_signal"
                          }
                        />
                        <Pill>{c.size_core} items</Pill>
                      </div>
                    </CardHeader>
                    {!c.trend_eligible ? (
                      <CardBody className="pt-0">
                        <p className="text-[11px] leading-relaxed text-text-faint">
                          Not yet eligible for a growth or decline claim: that needs
                          enough publication periods, items and distinct creators to
                          separate a real shift from collection-time sampling bias.
                        </p>
                      </CardBody>
                    ) : null}
                  </Card>
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </>
  );
}
