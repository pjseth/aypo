import { PageHeader } from "@/components/shell";
import {
  Card,
  CardBody,
  EmptyState,
  Pill,
  SampleSize,
  SectionTitle,
} from "@/components/ui/primitives";
import { serverFetch } from "@/lib/api";
import { NAV } from "@/lib/nav";

const nav = NAV.find((n) => n.href === "/landscape")!;

export const dynamic = "force-dynamic";

type Label = {
  facet: string;
  value: string;
  cue: string;
  basis: string;
  observations: number;
  distinct_items: number;
  distinct_creators: number;
};

type Landscape = {
  facets: Record<string, Label[]>;
  total_creators: number;
  caveat: string;
};

const BASIS_STYLE: Record<string, string> = {
  seen: "border-claim-observed/40 text-claim-observed bg-claim-observed/8",
  heard: "border-claim-computed/40 text-claim-computed bg-claim-computed/8",
  written: "border-claim-interpreted/40 text-claim-interpreted bg-claim-interpreted/8",
  inferred: "border-claim-hypothesis/40 text-claim-hypothesis bg-claim-hypothesis/8",
};

export default async function LandscapePage() {
  const data = await serverFetch<Landscape | null>("/api/landscape", null);
  const facets = Object.entries(data?.facets ?? {});

  if (facets.length === 0) {
    return (
      <>
        <PageHeader title="Consumer & Cultural Landscape" question={nav.question} />
        <div className="px-8 py-6">
          <EmptyState
            title="Nothing discovered yet"
            body="Behaviours, occasions, social contexts, cultural references, slang and creator archetypes are composed by the analyst from what each item actually showed. Nothing is pre-defined, so this stays empty until a run has analysed enough material."
          />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Consumer & Cultural Landscape"
        question={nav.question}
        right={<SampleSize n={data!.total_creators} unit="creators" />}
      />
      <div className="space-y-8 px-8 py-6">
        <p className="max-w-3xl text-[12px] leading-relaxed text-text-muted">
          {data!.caveat}
        </p>

        {facets.map(([facet, labels]) => (
          <section key={facet} className="space-y-3">
            <SectionTitle hint={`${labels.length} discovered`}>
              {facet.replace(/_/g, " ")}
            </SectionTitle>
            <div className="grid gap-3 lg:grid-cols-2">
              {labels.map((label, i) => (
                <Card key={`${facet}-${i}`}>
                  <CardBody className="space-y-2 pt-4">
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-[13px] leading-snug text-text">{label.value}</p>
                      <Pill className={BASIS_STYLE[label.basis] ?? ""}>{label.basis}</Pill>
                    </div>
                    {/* The observable cue is what makes the label checkable:
                        a second reader can look for exactly this. */}
                    <p className="font-mono text-[10px] leading-relaxed text-text-faint">
                      {label.cue}
                    </p>
                    <div className="flex gap-3 border-t border-border-subtle pt-2 font-mono text-[10px] text-text-muted">
                      <span>{label.distinct_items} items</span>
                      <span
                        className={
                          label.distinct_creators < 2 ? "text-warning" : undefined
                        }
                        title={
                          label.distinct_creators < 2
                            ? "Seen from a single creator — describes that creator, not the category"
                            : undefined
                        }
                      >
                        {label.distinct_creators} creators
                      </span>
                    </div>
                  </CardBody>
                </Card>
              ))}
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
