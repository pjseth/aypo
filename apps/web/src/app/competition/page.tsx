import { PageHeader } from "@/components/shell";
import {
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Pill,
  SectionTitle,
} from "@/components/ui/primitives";
import { serverFetch } from "@/lib/api";
import { NAV } from "@/lib/nav";

const nav = NAV.find((n) => n.href === "/competition")!;

export const dynamic = "force-dynamic";

type Brand = {
  canonical_name: string;
  variants: string[];
  observations: number;
  distinct_items: number;
  distinct_creators: number;
  seeded: boolean;
};

type Competition = {
  brands: Brand[];
  co_appearance: { a: string; b: string; items: number }[];
  caveat: string;
};

export default async function CompetitionPage() {
  const data = await serverFetch<Competition | null>("/api/competition", null);
  const brands = data?.brands ?? [];

  if (brands.length === 0) {
    return (
      <>
        <PageHeader title="Competitive Landscape" question={nav.question} />
        <div className="px-8 py-6">
          <EmptyState
            title="No competitors discovered yet"
            body="Competitors are not supplied in advance. They emerge from brands actually seen in the collected content, and each one shows the evidence that caused it to be detected."
          />
        </div>
      </>
    );
  }

  const maxItems = Math.max(...brands.map((b) => b.distinct_items), 1);

  return (
    <>
      <PageHeader
        title="Competitive Landscape"
        question={nav.question}
        right={<Pill>{brands.length} brands discovered</Pill>}
      />
      <div className="space-y-8 px-8 py-6">
        <p className="max-w-3xl text-[12px] leading-relaxed text-text-muted">
          {data!.caveat}
        </p>

        <section className="space-y-3">
          <SectionTitle hint="ranked by how many distinct items they appeared in">
            Brands observed
          </SectionTitle>
          {/* Brief §24 forbids an overall competitor score, so there is none:
              what is shown is observed presence and its independence. */}
          <Card className="overflow-hidden">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-border-subtle text-left text-[11px] tracking-wide text-text-faint uppercase">
                  <th className="px-4 py-2.5 font-medium">Brand</th>
                  <th className="px-4 py-2.5 font-medium">Presence in sample</th>
                  <th className="px-4 py-2.5 text-right font-medium">Items</th>
                  <th className="px-4 py-2.5 text-right font-medium">Creators</th>
                  <th className="px-4 py-2.5 font-medium">Read as</th>
                </tr>
              </thead>
              <tbody>
                {brands.map((b) => (
                  <tr
                    key={b.canonical_name}
                    className="border-b border-border-subtle/60 last:border-0"
                  >
                    <td className="px-4 py-2.5">
                      <span className="flex items-center gap-2">
                        <span className="text-text">{b.canonical_name}</span>
                        {b.seeded ? <Pill>seeded</Pill> : null}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="h-2 w-40 overflow-hidden rounded-full bg-elevated">
                        <div
                          className="h-full rounded-full bg-accent"
                          style={{ width: `${(b.distinct_items / maxItems) * 100}%` }}
                        />
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
                      {b.distinct_items}
                    </td>
                    <td
                      className={`px-4 py-2.5 text-right font-mono ${
                        b.distinct_creators < 2 ? "text-warning" : "text-text-secondary"
                      }`}
                      title={
                        b.distinct_creators < 2
                          ? "Seen from a single creator, so this is one person's content rather than a pattern"
                          : undefined
                      }
                    >
                      {b.distinct_creators}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-[10px] text-text-faint">
                      {b.variants.length > 1 ? b.variants.join(" · ") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </section>

        {data!.co_appearance.length > 0 ? (
          <section className="space-y-3">
            <SectionTitle hint="seen in the same item">Co-appearance</SectionTitle>
            <Card>
              <CardHeader>
                <p className="text-[12px] leading-relaxed text-text-muted">
                  Two brands appearing together is an observation, not a relationship.
                  Whether it means substitution, co-consumption or a shelf shot needs
                  evidence this sample does not yet carry.
                </p>
              </CardHeader>
              <CardBody className="space-y-1.5">
                {data!.co_appearance.map((pair, i) => (
                  <div key={i} className="flex items-center justify-between gap-3">
                    <span className="text-[13px] text-text">
                      {pair.a} <span className="text-text-faint">+</span> {pair.b}
                    </span>
                    <span className="font-mono text-[11px] text-text-muted">
                      {pair.items} {pair.items === 1 ? "item" : "items"}
                    </span>
                  </div>
                ))}
              </CardBody>
            </Card>
          </section>
        ) : null}
      </div>
    </>
  );
}
