import { describe, expect, it } from "vitest";
import type { Message, Step } from "../lib/types";
import { stageStatus } from "./ContextPanel";

const msg = (over: Partial<Message> = {}): Message => ({
  id: "m1", question: "q", kind: "investigate", status: "running", steps: [], llmCalls: 0, tokens: { input: 0, output: 0 }, startedAt: 0, ...over,
});
const step = (status: Step["status"]): Step => ({ callId: "c", name: "search_project", args: {}, status });

describe("Investigation Context stage status (from live SSE steps)", () => {
  it("is Idle with no message, Waiting before the first call, Running during a call", () => {
    expect(stageStatus(null, [], true)).toBe("idle");
    expect(stageStatus(msg(), [], false)).toBe("waiting");
    expect(stageStatus(msg(), [step("running")], false)).toBe("running");
  });

  it("is Completed after calls, Error if all failed, Not used if the run ended without calls", () => {
    expect(stageStatus(msg(), [step("ok")], false)).toBe("completed");
    expect(stageStatus(msg({ status: "done" }), [step("error")], false)).toBe("error");
    expect(stageStatus(msg({ status: "done" }), [], false)).toBe("not_used");
  });

  it("treats a quick answer as retrieval only", () => {
    expect(stageStatus(msg({ kind: "quick" }), [], true)).toBe("running");
    expect(stageStatus(msg({ kind: "quick", status: "done", answer: {} as Message["answer"] }), [], true)).toBe("completed");
    expect(stageStatus(msg({ kind: "quick", status: "done" }), [], false)).toBe("not_used");
  });
});
