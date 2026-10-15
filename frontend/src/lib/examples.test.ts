import { describe, expect, it } from "vitest";
import { DEMO_EXAMPLES, exampleQuestions } from "./examples";
import type { Project } from "./types";

const base: Project = { path: "/p", name: "billing-service", indexed: true, chunks: 10, files: 4, model: null };

describe("exampleQuestions", () => {
  it("uses the 401/JWT questions only for the demo project", () => {
    expect(exampleQuestions({ ...base, name: "fastapi-auth-demo" })).toEqual(DEMO_EXAMPLES);
    const generic = exampleQuestions({ ...base, file_list: ["payments/api.py", "payments/models.py", "README.md", "settings.py"] });
    expect(generic.join(" ")).not.toMatch(/401|JWT|bearer/i);
  });

  it("builds questions from the project's real file names", () => {
    const questions = exampleQuestions({ ...base, file_list: ["payments/api.py", "payments/models.py", "ledger/core.py", "config/settings.py", "README.md"] });
    expect(questions[0]).toBe("How does payments work?");
    expect(questions.some((q) => q.includes("config/settings.py"))).toBe(true);
    expect(questions.some((q) => q.includes("ledger"))).toBe(true);
    expect(questions.length).toBeLessThanOrEqual(4);
  });

  it("returns nothing without a project", () => {
    expect(exampleQuestions(null)).toEqual([]);
  });
});
