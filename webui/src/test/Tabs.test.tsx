import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Tabs } from "../components/Tabs";

afterEach(cleanup);

const TABS = [
  { id: "a", label: "一覧", content: <p>一覧の中身</p> },
  { id: "b", label: "設定", content: <p>設定の中身</p> },
  { id: "c", label: "履歴", content: <p>履歴の中身</p> },
];

describe("the tabs", () => {
  it("move with the arrow keys, Home and End, with one tab stop", () => {
    render(<Tabs tabs={TABS} />);
    const first = screen.getByRole("tab", { name: "一覧" });
    expect(first).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "設定" })).toHaveAttribute("tabindex", "-1");
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "設定" })).toHaveFocus();
    expect(screen.getByText("設定の中身")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("tab", { name: "設定" }), { key: "End" });
    expect(screen.getByRole("tab", { name: "履歴" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "履歴" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "一覧" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("tab", { name: "一覧" }), { key: "ArrowLeft" });
    expect(screen.getByText("履歴の中身")).toBeInTheDocument();
  });
});
