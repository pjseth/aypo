import { PageHeader } from "@/components/shell";
import { EmptyState } from "@/components/ui/primitives";
import { NAV } from "@/lib/nav";

const nav = NAV.find((n) => n.href === "/ask")!;

export default function Page() {
  return (
    <>
      <PageHeader title="Ask the Data" question={nav.question} />
      <div className="px-8 py-6">
        <EmptyState
          title="Nothing to query yet"
          body="This answers only from the collected dataset. It cites the content behind every claim, reports the sample size, and says the data is insufficient rather than answering from general knowledge."
        />
      </div>
    </>
  );
}
