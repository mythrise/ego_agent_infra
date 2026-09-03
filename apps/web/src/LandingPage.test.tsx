import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { i18n } from "./i18n";
import { LandingPage } from "./LandingPage";

describe("EgoAgentOS landing experience", () => {
  beforeEach(() => i18n.setLanguage("en"));
  afterEach(() => {
    cleanup();
    i18n.setLanguage("en");
    document.body.classList.remove("landing-menu-open");
  });

  it("switches the public surface to Chinese and updates the document language", () => {
    render(<LandingPage />);

    fireEvent.click(screen.getByRole("button", { name: "中" }));

    expect(screen.getByRole("heading", { name: /让 AI 实验 成为可复核证据/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "进入科研控制台" })).toHaveAttribute("href", "#acceptance-environment");
    expect(document.documentElement.lang).toBe("zh-CN");
  });

  it("opens and closes the accessible mobile navigation", () => {
    render(<LandingPage />);
    const trigger = screen.getByRole("button", { name: "Open menu" });

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(document.body).toHaveClass("landing-menu-open");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });
});
