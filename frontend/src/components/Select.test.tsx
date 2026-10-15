import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Select } from "./Select";

const OPTIONS = [
  { value: "a", label: "octo-org/demo" },
  { value: "b", label: "octo-org/sandbox" },
  { value: "c", label: "disabled/repo", disabled: true },
];

describe("Select (custom dropdown, no native select)", () => {
  it("opens a listbox and selects with the mouse", () => {
    const onChange = vi.fn();
    render(<Select label="Repository" value="a" options={OPTIONS} onChange={onChange} />);
    expect(document.querySelector("select")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Repository" }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /octo-org\/demo/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("option", { name: /sandbox/ }));
    expect(onChange).toHaveBeenCalledWith("b");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("supports the keyboard: arrows, Enter, Escape", () => {
    const onChange = vi.fn();
    render(<Select label="Repository" value="a" options={OPTIONS} onChange={onChange} />);
    const button = screen.getByRole("button", { name: "Repository" });
    fireEvent.keyDown(button, { key: "ArrowDown" });
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("b");
    fireEvent.click(button);
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("ignores disabled options", () => {
    const onChange = vi.fn();
    render(<Select label="Repository" value="a" options={OPTIONS} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Repository" }));
    fireEvent.click(screen.getByRole("option", { name: /disabled/ }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
