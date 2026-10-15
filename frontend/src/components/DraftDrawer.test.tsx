import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Draft } from "../lib/types";
import { DraftDrawer, type DraftApi } from "./DraftDrawer";

const REPO = "demo-org/sandbox";

function makeDraft(over: Partial<Draft> = {}): Draft {
  return {
    id: "d1", repo: REPO, title: "Login returns 401", body: "## Summary\nBearer is rejected.", labels: ["bug"],
    draft_hash: "a".repeat(64), errors: [], warnings: [], attempted: false, result: null, ...over,
  };
}

function makeClient(over: Partial<DraftApi> = {}): DraftApi {
  return {
    editDraft: vi.fn(async (_id: string, title: string, body: string, labels: string[]) =>
      makeDraft({ title, body, labels, draft_hash: "b".repeat(64) })),
    similar: vi.fn(async () => ({ similar: [], checked: true })),
    approve: vi.fn(),
    cancelDraft: vi.fn(async () => ({ ok: true })),
    ...over,
  } as unknown as DraftApi;
}

const confirmBox = () => screen.getByLabelText("Type the repository name to confirm");
const approveButton = () => screen.getByTestId("approve-button");

describe("DraftDrawer approval", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it("shows the destination repository prominently", () => {
    render(<DraftDrawer initial={makeDraft()} onClose={vi.fn()} client={makeClient()} />);
    expect(screen.getByTestId("draft-repo")).toHaveTextContent(REPO);
  });

  it("stays disabled until the exact repository is typed", async () => {
    render(<DraftDrawer initial={makeDraft()} onClose={vi.fn()} client={makeClient()} />);
    expect(approveButton()).toBeDisabled();
    for (const wrong of ["demo-org/sandbo", "Demo-Org/Sandbox", " demo-org/sandbox", "demo-org/sandbox "]) {
      fireEvent.change(confirmBox(), { target: { value: wrong } });
      expect(approveButton()).toBeDisabled();
    }
    fireEvent.change(confirmBox(), { target: { value: REPO } });
    await waitFor(() => expect(approveButton()).toBeEnabled());
  });

  it("stays disabled while the draft has blocking errors", () => {
    const draft = makeDraft({ errors: ["The body contains what looks like a GitHub token."] });
    render(<DraftDrawer initial={draft} onClose={vi.fn()} client={makeClient()} />);
    fireEvent.change(confirmBox(), { target: { value: REPO } });
    expect(approveButton()).toBeDisabled();
    expect(screen.getByTestId("draft-errors")).toHaveTextContent("looks like a GitHub token");
  });

  it("editing clears the typed confirmation and approves the new hash only", async () => {
    const approve = vi.fn(async () => ({
      draft: makeDraft({ draft_hash: "b".repeat(64), attempted: true }),
      result: { status: "created", number: 42, html_url: "https://github.com/demo-org/sandbox/issues/42", message: "Created.",
                similar_issues: [], possible_matches: [] },
    }));
    const client = makeClient({ approve: approve as unknown as DraftApi["approve"] });
    render(<DraftDrawer initial={makeDraft()} onClose={vi.fn()} client={client} />);
    fireEvent.change(confirmBox(), { target: { value: REPO } });
    await waitFor(() => expect(approveButton()).toBeEnabled());

    fireEvent.change(screen.getByLabelText("Issue title"), { target: { value: "Bearer scheme is case-sensitive" } });
    expect(confirmBox()).toHaveValue("");
    expect(approveButton()).toBeDisabled();

    await act(async () => { vi.advanceTimersByTime(500); });
    await waitFor(() => expect(client.editDraft).toHaveBeenCalledOnce());
    fireEvent.change(confirmBox(), { target: { value: REPO } });
    await waitFor(() => expect(approveButton()).toBeEnabled());
    fireEvent.click(approveButton());
    await waitFor(() => expect(approve).toHaveBeenCalledWith("d1", "b".repeat(64), REPO, false));
    expect(await screen.findByTestId("draft-result-created")).toHaveTextContent("Issue #42 created");
    expect(screen.queryByTestId("approve-button")).toBeNull(); // one attempt per draft
  });

  it("requires acknowledging similar open issues", async () => {
    const client = makeClient({
      similar: vi.fn(async () => ({ similar: [{ number: 7, title: "401 with bearer", html_url: "https://x/7", similarity: 0.8 }], checked: true })),
    } as Partial<DraftApi>);
    render(<DraftDrawer initial={makeDraft()} onClose={vi.fn()} client={client} />);
    expect(await screen.findByTestId("draft-similar")).toHaveTextContent("#7 401 with bearer");
    fireEvent.change(confirmBox(), { target: { value: REPO } });
    expect(approveButton()).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    await waitFor(() => expect(approveButton()).toBeEnabled());
  });

  it("cancel tells the server and never approves", async () => {
    const onClose = vi.fn();
    const client = makeClient();
    render(<DraftDrawer initial={makeDraft()} onClose={onClose} client={client} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith("cancelled"));
    expect(client.cancelDraft).toHaveBeenCalledWith("d1");
    expect(client.approve).not.toHaveBeenCalled();
  });
});
