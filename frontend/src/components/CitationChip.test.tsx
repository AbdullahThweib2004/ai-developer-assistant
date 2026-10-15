import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Source } from "../lib/types";
import { EvidenceContext } from "./evidence-context";
import { Markdown } from "./Markdown";

const S1: Source = {
  cite_id: "S1", kind: "project", label: "app/auth.py", cited: true, file_path: "app/auth.py",
  location: "lines 24-37", start_line: 24, language: "python", excerpt: "def parse_authorization_header(value):", labels: [],
};
const G1: Source = {
  cite_id: "G1", kind: "github", label: "fastapi/fastapi#10177", cited: true, repo: "fastapi/fastapi",
  number: 10177, title: "HTTPBearer returns 403", state: "closed", labels: [],
};

function renderAnswer(text: string, open = vi.fn(), invalid = new Set<string>()) {
  render(
    <EvidenceContext.Provider value={{ sources: { S1, G1 }, invalid, open }}>
      <Markdown>{text}</Markdown>
    </EvidenceContext.Provider>,
  );
  return open;
}

describe("citation chips", () => {
  it("renders [S1] and [G1] as chips that open the source", () => {
    const open = renderAnswer("The parser is strict [S1]. See also [G1].");
    expect(screen.getByTestId("citation-S1")).toHaveTextContent("S1");
    expect(screen.getByTestId("citation-G1")).toHaveAccessibleName("Open source G1: #10177");
    fireEvent.click(screen.getByTestId("citation-S1"));
    expect(open).toHaveBeenCalledWith("S1");
  });

  it("shows a hover preview with the file path and excerpt", async () => {
    renderAnswer("Strict [S1].");
    fireEvent.mouseEnter(screen.getByTestId("citation-S1").parentElement!);
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent("app/auth.py");
    expect(tip).toHaveTextContent("parse_authorization_header");
  });

  it("marks citations no tool returned as invalid instead of linking them", () => {
    const open = renderAnswer("Made up [S9] and flagged [S1].", vi.fn(), new Set(["S1"]));
    expect(screen.getByTestId("citation-invalid-S9")).toHaveAttribute("title", expect.stringContaining("Invalid citation"));
    expect(screen.getByTestId("citation-invalid-S1")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(open).not.toHaveBeenCalled();
  });

  it("never renders raw HTML from the answer", () => {
    renderAnswer("<img src=x onerror=alert(1)> text [S1]");
    expect(document.querySelector("img")).toBeNull();
  });
});
