import Link from "next/link";
import { PageHeader } from "@/components/shell";
import {
  Card,
  CardBody,
  CardHeader,
  ClaimTypeBadge,
  ConfidenceBadge,
  EmptyState,
  InsufficientData,
  Pill,
  SampleSize,
  SectionTitle,
} from "@/components/ui/primitives";
import { serverFetch } from "@/lib/api";
import { sharePercent } from "@/lib/utils";

export const dynamic = "force-dynamic";

type Overview = {
  counts: Record<string, number>;
  latest_run: {
    id: string;
    objective: string;
    subject_brand: string;
    market_code: string;
    state: string;
    stop_reason: string | null;
  } | null;
  brands: {
    canonical_name: string;
    observations: number;
    variants: string[];
    seeded: boolean;
  }[];
  brand_surface_forms: { surface_form: string; observations: number }[];
  facets: { facet: string; n: number }[];
  labels: { facet: string; value: string; cue: string }[];
  consumption_states: { consumption_state: string; n: number }[];
};

function Stat({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <Card>
      <CardBody className="pt-4">
        <p className="font-mono text-2xl text-text tabular-nums">{value}</p>
        <p className="mt-0.5 text-[12px] text-text-secondary">{label}</p>
        {hint ? <p className="mt-1 text-[11px] text-text-faint">{hint}</p> : null}
      </CardBody>
    </Card>
  );
}

export default async function OverviewPage() {
  const data = await serverFetch<Overview | null>("/api/overview", null);
  const counts = data?.counts ?? {};
  const hasData = (counts.content ?? 0) > 0;

  if (!data || !hasData) {
    return (
      <>
        <PageHeader
          title="Cultural Pulse"
          question="What is happening right now, and what changed?"
          right={<Pill>No research run yet</Pill>}
        />
        <div className="px-8 py-6">
          <EmptyState
            title="No findings yet"
            body="Findings are discovered from collected content, never pre-written. Start a research run and this surface fills with whatever the data actually supports."
          />
        </div>
      </>
    );
  }

  const totalStates = data.consumption_states.reduce((sum, s) => sum + s.n, 0);
  const incidental =
    data.consumption_states.find((s) => s.consumption_state === "present")?.n ?? 0;

  return (
    <>
      <PageHeader
        title="Cultural Pulse"
        question="What is happening right now, and what changed?"
        right={
          data.latest_run ? (
            <>
              <Pill>{data.latest_run.subject_brand}</Pill>
              <Pill>{data.latest_run.market_code}</Pill>
              <Link href={`/runs/${data.latest_run.id}`} className="text-[11px] text-accent hover:underline">
                research run ↗
              </Link>
            </>
          ) : null
        }
      />

      <div className="space-y-8 px-8 py-6">
        <section className="space-y-3">
          <SectionTitle hint="everything below is scoped to this sample, not the market">
            Dataset
          </SectionTitle>
          <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
            <Stat label="videos collected" value={counts.content ?? 0} />
            <Stat label="analysed" value={counts.analyses ?? 0} />
            <Stat label="distinct creators" value={counts.creators ?? 0} hint="independence unit" />
            <Stat label="claims" value={counts.claims ?? 0} hint="each anchored" />
            <Stat label="marketplace listings" value={counts.commerce ?? 0} />
            <Stat label="findings" value={counts.findings ?? 0} hint="needs clustering" />
          </div>
        </section>

        {/* Brief §28: incidental appearance must never read as active use. */}
        <section className="space-y-3">
          <SectionTitle hint="appearance is not use">Product involvement</SectionTitle>
          <Card>
            <CardHeader>
              <div className="space-y-1">
                <p className="text-[13px] font-medium text-text">
                  How products actually appear
                </p>
                <p className="text-xs text-text-muted">
                  {sharePercent(incidental, totalStates)} of product observations are
                  incidental presence — the object is in frame but nobody is using it.
                </p>
              </div>
              <SampleSize n={totalStates} unit="observations" />
            </CardHeader>
            <CardBody className="space-y-2">
              {data.consumption_states.map((s) => (
                <div key={s.consumption_state} className="flex items-center gap-3">
                  <span className="w-44 shrink-0 font-mono text-[11px] text-text-secondary">
                    {s.consumption_state.replace(/_/g, " ")}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-elevated">
                    <div
                      className="h-full rounded-full bg-accent"
                      style={{ width: `${(s.n / totalStates) * 100}%` }}
                    />
                  </div>
                  <span className="w-8 text-right font-mono text-[11px] text-text-muted">
                    {s.n}
                  </span>
                </div>
              ))}
            </CardBody>
          </Card>
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-3">
            <SectionTitle hint="discovered, never from a seed list">
              Brands observed
            </SectionTitle>
            <Card>
              <CardBody className="space-y-2.5 pt-4">
                {data.brands.map((b) => (
                  <div key={b.canonical_name} className="space-y-0.5">
                    <div className="flex items-center justify-between gap-3">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate text-[13px] text-text">
                          {b.canonical_name}
                        </span>
                        {/* Brief §23: a seeded brand must be distinguishable from
                            one the data actually found, or the discovery claim is
                            not credible. */}
                        {b.seeded ? <Pill>seeded</Pill> : null}
                      </span>
                      <span className="font-mono text-[11px] text-text-muted">
                        {b.observations}
                      </span>
                    </div>
                    {b.variants.length > 1 ? (
                      <p className="font-mono text-[10px] text-text-faint">
                        read as {b.variants.join(" · ")}
                      </p>
                    ) : null}
                  </div>
                ))}
                <p className="border-t border-border-subtle pt-2.5 text-[11px] leading-relaxed text-text-faint">
                  Names are stored exactly as the analyst read them off packaging and
                  resolved into entities afterwards, so the original reading stays
                  available as evidence. {data.brand_surface_forms.length} surface forms
                  resolved to {data.brands.length} brands.
                </p>
              </CardBody>
            </Card>
          </div>

          <div className="space-y-3">
            <SectionTitle hint="composed by the analyst, never from a fixed list">
              Discovered vocabulary
            </SectionTitle>
            <Card>
              <CardBody className="space-y-3 pt-4">
                <div className="flex flex-wrap gap-1.5">
                  {data.facets.map((f) => (
                    <Pill key={f.facet}>
                      {f.facet.replace(/_/g, " ")} · {f.n}
                    </Pill>
                  ))}
                </div>
                <div className="space-y-2 border-t border-border-subtle pt-3">
                  {data.labels.slice(0, 8).map((l, i) => (
                    <div key={i} className="space-y-0.5">
                      <p className="text-[13px] leading-snug text-text">{l.value}</p>
                      <p className="font-mono text-[10px] text-text-faint">
                        {l.facet.replace(/_/g, " ")} · {l.cue.slice(0, 72)}
                      </p>
                    </div>
                  ))}
                </div>
              </CardBody>
            </Card>
          </div>
        </section>

        <section className="space-y-3">
          <SectionTitle>Themes and trends</SectionTitle>
          <InsufficientData
            reason={`Clustering has not run: ${counts.content ?? 0} analysed items is below the 60-item floor, and growth or decline cannot be claimed without enough temporal coverage to rule out a collection artefact. Collect more and this fills in.`}
          />
          <div className="flex flex-wrap gap-2 pt-1">
            <ClaimTypeBadge type="observed" />
            <ClaimTypeBadge type="computed" />
            <ClaimTypeBadge type="interpreted" />
            <ClaimTypeBadge type="hypothesis" />
            <ConfidenceBadge band="insufficient" />
          </div>
        </section>
      </div>
    </>
  );
}
