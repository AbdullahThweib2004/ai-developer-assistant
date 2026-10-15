import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Step } from "../lib/types";
import { StepTimeline } from "./StepTimeline";

const steps: Step[] = [
  { callId: "1", name: "search_project", args: { query: "bearer 401" }, status: "ok", durationMs: 55, resultCount: 5 },
  { callId: "2", name: "read_project_file", args: { file_path: "app/auth.py" }, status: "ok", durationMs: 4 },
  { callId: "3", name: "search_github_issues", args: { query: "HTTPBearer" }, status: "running" },
];

describe("StepTimeline", () => {
  it("lists live steps with descriptions, results and a progress row while running", () => {
    render(<StepTimeline steps={steps} running repo="fastapi/fastapi" sourceCount={0} elapsedMs={1200} writing={false} />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent("Searching project for “bearer 401” · Found 5 matches");
    expect(items[1]).toHaveTextContent("Reading app/auth.py");
    expect(items[2]).toHaveTextContent("Searching fastapi/fastapi issues for “HTTPBearer”");
    expect(items[3]).toHaveTextContent("Thinking…");
    expect(screen.queryByTestId("step-summary")).toBeNull();
  });

  it("collapses to a one-line summary when finished and expands on click", () => {
    const done = steps.map((s) => ({ ...s, status: "ok" as const, durationMs: s.durationMs ?? 7 }));
    render(<StepTimeline steps={done} running={false} sourceCount={1} elapsedMs={18_200} writing={false} />);
    expect(screen.getByTestId("step-summary")).toHaveTextContent("3 steps · 1 source · 18.2s");
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("shows error categories on failed steps", () => {
    render(<StepTimeline steps={[{ callId: "9", name: "get_github_issue", args: { number: 7 }, status: "error", errorCategory: "rate_limited" }]}
      running={false} sourceCount={0} elapsedMs={300} writing={false} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("listitem")).toHaveTextContent("rate limited");
  });
});
