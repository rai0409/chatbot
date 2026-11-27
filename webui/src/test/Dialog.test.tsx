import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dialog } from "../components/Dialog";
import { ConfirmDialog } from "../pages/ingestion/parts";

afterEach(cleanup);

function Host({ onConfirm, busy }: { onConfirm: () => void; busy?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        開く
      </button>
      {open && (
        <Dialog title="削除しますか" busy={busy} onCancel={() => setOpen(false)}>
          <button type="button" className="danger" onClick={onConfirm}>
            削除する
          </button>
          <button type="button" data-autofocus onClick={() => setOpen(false)}>
            キャンセル
          </button>
        </Dialog>
      )}
    </>
  );
}

describe("the modal dialog", () => {
  it("opens as a modal named by its title, with the focus on the safe button", () => {
    render(<Host onConfirm={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "開く" }));
    const dialog = screen.getByRole("dialog", { name: "削除しますか" });
    expect(dialog.tagName).toBe("DIALOG");
    expect(dialog).toHaveAttribute("open");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "キャンセル" })).toHaveFocus();
  });

  it("closes with Esc and with the cancel button, and gives the focus back", () => {
    render(<Host onConfirm={vi.fn()} />);
    const opener = screen.getByRole("button", { name: "開く" });
    opener.focus();
    fireEvent.click(opener);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();

    fireEvent.click(opener);
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true })); // the browser's own close request
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(opener);
    fireEvent.click(screen.getByRole("button", { name: "キャンセル" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("confirms with the danger button, and ignores Esc while busy", () => {
    const onConfirm = vi.fn();
    render(<Host onConfirm={onConfirm} busy />);
    fireEvent.click(screen.getByRole("button", { name: "開く" }));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "削除する" });
    expect(confirm).toHaveClass("danger");
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("gives two dialogs different title ids (useId), so each is named by its own title", () => {
    render(
      <>
        <ConfirmDialog title="一つ目" onConfirm={vi.fn()} onCancel={vi.fn()} />
        <ConfirmDialog title="二つ目" onConfirm={vi.fn()} onCancel={vi.fn()} />
      </>,
    );
    expect(screen.getByRole("dialog", { name: "一つ目" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "二つ目" })).toBeInTheDocument();
  });
});
