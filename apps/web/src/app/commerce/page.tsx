import { PageHeader } from "@/components/shell";
import {
  Card,
  EmptyState,
  Pill,
  SampleSize,
  SectionTitle,
} from "@/components/ui/primitives";
import { type CommerceRow, serverFetch } from "@/lib/api";
import { compactNumber, formatPrice, num } from "@/lib/utils";
import { NAV } from "@/lib/nav";

const nav = NAV.find((n) => n.href === "/commerce")!;

export const dynamic = "force-dynamic";

export default async function CommercePage() {
  const data = await serverFetch<{
    items: CommerceRow[];
    total: number;
    sold_count_caveat: string;
  }>("/api/commerce?limit=60", { items: [], total: 0, sold_count_caveat: "" });

  if (data.items.length === 0) {
    return (
      <>
        <PageHeader title="Commerce Explorer" question={nav.question} />
        <div className="px-8 py-6">
          <EmptyState
            title="No marketplace records collected yet"
            body="Shopee and Lazada listings appear here with price, discount, rating, review count and seller, alongside the social themes they were matched to."
          />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Commerce Explorer"
        question={nav.question}
        right={<SampleSize n={data.total} unit="listings" />}
      />
      <div className="space-y-4 px-8 py-6">
        <SectionTitle>Marketplace listings</SectionTitle>

        <Card className="overflow-hidden">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-border-subtle text-left text-[11px] tracking-wide text-text-faint uppercase">
                <th className="px-4 py-2.5 font-medium">Product</th>
                <th className="px-4 py-2.5 font-medium">Brand</th>
                <th className="px-4 py-2.5 font-medium">Seller</th>
                <th className="px-4 py-2.5 text-right font-medium">Price</th>
                <th className="px-4 py-2.5 text-right font-medium">Rating</th>
                <th className="px-4 py-2.5 text-right font-medium">Reviews</th>
                <th className="px-4 py-2.5 text-right font-medium">Sold*</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr
                  key={row.id}
                  className="border-b border-border-subtle/60 last:border-0 hover:bg-elevated/60"
                >
                  <td className="max-w-[340px] px-4 py-2.5">
                    <a
                      href={row.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="line-clamp-2 text-text hover:text-accent hover:underline"
                    >
                      {row.title_raw}
                    </a>
                    <div className="mt-1 flex gap-1.5">
                      <Pill>{row.platform.replace("_ph", "")}</Pill>
                      {/* A bundle attributed to one brand would corrupt every
                          share-of-listings statistic, so the kind is shown. */}
                      {row.listing_kind && row.listing_kind !== "single" ? (
                        <Pill>{row.listing_kind.replace("_", " ")}</Pill>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-text-secondary">
                    {row.brand_surface_form ?? <span className="text-text-faint">—</span>}
                  </td>
                  <td className="max-w-[160px] truncate px-4 py-2.5 text-text-muted">
                    {row.seller_name ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-text">
                    {num(row.price_minor) !== null
                      ? formatPrice(num(row.price_minor)! / 100, row.currency)
                      : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
                    {num(row.rating_avg)?.toFixed(2) ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
                    {compactNumber(num(row.review_count))}
                  </td>
                  <td
                    className="px-4 py-2.5 text-right font-mono text-text-muted"
                    title={`Platform-reported as "${row.sold_count_text ?? "unknown"}" (semantics: ${row.sold_semantics ?? "unknown"})`}
                  >
                    {row.sold_count_text ?? compactNumber(num(row.sold_count_raw))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        {/* The platforms disagree about what this number means, so it travels
            with its caveat rather than reading as a sales figure (brief §28). */}
        <p className="text-[11px] leading-relaxed text-text-muted">
          <span className="font-mono">*</span> {data.sold_count_caveat}
        </p>
      </div>
    </>
  );
}
