/**
 * The eight product surfaces from brief §31 and §32.
 *
 * Each carries the question a marketer is trying to answer, which the page
 * renders as its subtitle. Nothing about *what* appears inside a section is
 * encoded here: brief §31 explicitly forbids hard-coding which cards show up,
 * because the content is discovered from data rather than pre-decided.
 */
export type NavItem = {
  href: string;
  label: string;
  question: string;
  group: "intelligence" | "explore" | "audit";
};

export const NAV: NavItem[] = [
  {
    href: "/",
    label: "Cultural Pulse",
    question: "What is happening right now, and what changed?",
    group: "intelligence",
  },
  {
    href: "/landscape",
    label: "Consumer & Cultural",
    question: "What behaviours, occasions and cultural signals appear in the data?",
    group: "intelligence",
  },
  {
    href: "/competition",
    label: "Competitive",
    question: "Which brands were discovered, and how do they differ?",
    group: "intelligence",
  },
  {
    href: "/trends",
    label: "Trends",
    question: "What is gaining momentum, and where is the gap?",
    group: "intelligence",
  },
  {
    href: "/content",
    label: "Content",
    question: "What is actually in each video?",
    group: "explore",
  },
  {
    href: "/commerce",
    label: "Commerce",
    question: "What does the marketplace look like for these themes?",
    group: "explore",
  },
  {
    href: "/ask",
    label: "Ask the Data",
    question: "Ask a question and get a cited answer.",
    group: "explore",
  },
  {
    href: "/runs",
    label: "Research Runs",
    question: "How did the system reach its conclusions?",
    group: "audit",
  },
];

export const GROUP_LABELS: Record<NavItem["group"], string> = {
  intelligence: "Intelligence",
  explore: "Explore",
  audit: "Audit",
};
