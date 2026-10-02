import { PageHeader } from "@/components/shell";
import {
  Badge,
  Card,
  EmptyState,
  Pill,
  SampleSize,
  SectionTitle,
} from "@/components/ui/primitives";
import { type ContentRow, serverFetch } from "@/lib/api";
import { compactNumber, num } from "@/lib/utils";
import { NAV } from "@/lib/nav";

const nav = NAV.find((n) => n.href === "/content")!;

export const dynamic = "force-dynamic";

function Engagement({ item }: { item: ContentRow }) {
  const stats: [string, number | null][] = [
    ["views", item.views],
    ["likes", item.likes],
    ["comments", item.comments_count],
    ["shares", item.shares],
    ["saves", item.saves],
  ];
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-text-muted">
      {stats
        .filter(([, v]) => v !== null)
        .map(([label, value]) => (
          <span key={label}>
            {compactNumber(num(value))} <span className="text-text-faint">{label}</span>
          </span>
        ))}
    </div>
  );
}

export default async function ContentPage() {
  const data = await serverFetch<{ items: ContentRow[]; total: number }>(
    "/api/content?limit=60",
    { items: [], total: 0 },
  );

  if (data.items.length === 0) {
    return (
      <>
        <PageHeader title="Content Explorer" question={nav.question} />
        <div className="px-8 py-6">
          <EmptyState
            title="No content collected yet"
            body="Start a research run and every analysed video appears here with its transcript, detected brands, cultural signals and the query that discovered it."
          />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Content Explorer"
        question={nav.question}
        right={<SampleSize n={data.total} unit="items" />}
      />
      <div className="space-y-4 px-8 py-6">
        <SectionTitle hint="every item links back to the original post">
          Collected content
        </SectionTitle>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.items.map((item) => (
            <Card key={item.id} className="flex flex-col overflow-hidden">
              {item.thumbnail_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={item.thumbnail_url}
                  alt=""
                  className="h-56 w-full bg-elevated object-cover"
                  loading="lazy"
                />
              ) : (
                <div className="flex h-56 w-full items-center justify-center bg-elevated text-xs text-text-faint">
                  no thumbnail
                </div>
              )}
              <div className="flex flex-1 flex-col gap-2 p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[11px] text-text-secondary">
                    @{item.creator_handle ?? "unknown"}
                  </span>
                  <Pill>{item.platform}</Pill>
                </div>
                <p className="line-clamp-3 text-[13px] leading-snug text-text">
                  {item.caption ?? "(no caption)"}
                </p>
                <Engagement item={item} />
                <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-2">
                  {/* Platform-stated, so this is observed rather than inferred. */}
                  {item.platform_is_sponsored ? (
                    <Badge
                      className="border-warning/40 bg-warning/8 text-warning"
                      title={`Declared by the platform (${item.sponsorship_source ?? "unknown"})`}
                    >
                      Sponsored
                    </Badge>
                  ) : null}
                  {item.language_hint ? <Pill>{item.language_hint}</Pill> : null}
                  {num(item.duration_seconds) ? (
                    <Pill>{Math.round(num(item.duration_seconds)!)}s</Pill>
                  ) : null}
                  <a
                    href={item.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="ml-auto text-[11px] text-accent hover:underline"
                  >
                    original ↗
                  </a>
                </div>
              </div>
            </Card>
          ))}
        </div>
      </div>
    </>
  );
}
