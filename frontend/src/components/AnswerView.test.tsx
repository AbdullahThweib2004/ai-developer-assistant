import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Answer, Source } from "../lib/types";
import { AnswerView, keyFindings } from "./AnswerView";
import { topSource } from "./MessageView";

const S1: Source = { cite_id: "S1", kind: "project", label: "app/auth.py", cited: true, file_path: "app/auth.py",
  location: "chars 683-1422", start_line: 24, end_line: 43, labels: [], excerpt: "def verify_token(token):" };
const S2: Source = { cite_id: "S2", kind: "project", label: "app/config.py", cited: false, file_path: "app/config.py",
  location: "chars 0-500", start_line: 1, end_line: 24, labels: [] };

const answer: Answer = {
  answer_markdown: "", causes: [], project_sources: [S1, S2], github_sources: [], invalid_citations: [], trace: [],
  sections: [
    { heading: "Summary", markdown: "The scheme check is case-sensitive [S1]." },
    { heading: "From the project", markdown: "- `parse_authorization_header` rejects `Bearer` [S1].\n- A note without sources." },
    { heading: "Open questions", markdown: "- None." },
  ],
};

describe("AnswerView", () => {
  it("shows the lead, then key findings with file chips and line ranges", () => {
    const onOpen = vi.fn();
    render(<AnswerView answer={answer} onOpenSource={onOpen} />);
    expect(screen.getByTestId("answer-lead")).toHaveTextContent("The scheme check is case-sensitive");
    const first = screen.getByTestId("finding-1");
    expect(first).toHaveTextContent("parse_authorization_header");
    expect(first).toHaveTextContent("app/auth.py");
    expect(first).toHaveTextContent("Lines 24–43");
    fireEvent.click(screen.getByRole("button", { name: /Open app\/auth.py Lines 24–43/ }));
    expect(onOpen).toHaveBeenCalledWith(S1);
    expect(screen.getByTestId("finding-2")).toHaveTextContent("A note without sources");
    expect(screen.getByText("Open questions")).toBeInTheDocument();
    expect(screen.queryByText("From the project")).toBeNull(); // replaced by Key findings
  });

  it("keeps the plain section when no finding cites a project source", () => {
    const plain = { ...answer, sections: [{ heading: "From the project", markdown: "- Nothing cited." }] };
    render(<AnswerView answer={plain} />);
    expect(screen.getByText("From the project")).toBeInTheDocument();
    expect(screen.queryByTestId("key-findings")).toBeNull();
  });

  it("parses findings and picks the top source to open", () => {
    const findings = keyFindings("- `load_settings` reads JWT_SECRET [S2] and [S1].\n  continued line", { S1, S2 });
    expect(findings).toHaveLength(1);
    expect(findings[0].title).toBe("load_settings");
    expect(findings[0].sources.map((s) => s.cite_id)).toEqual(["S2", "S1"]);
    expect(findings[0].text).toContain("continued line");
    expect(topSource([S2, S1])).toBe(S1); // cited first
    expect(topSource([S2])).toBe(S2);
    expect(topSource([])).toBeNull();
  });
});
