import { describe, expect, it, vi } from "vitest";
import {
  LANGUAGE_STORAGE_KEY,
  createI18nStore,
  htmlLanguage,
  labelForStage,
  labelForStatus,
  normalizeLanguage,
  translate,
  translations,
} from "./i18n";

function memoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    value: (key: string) => values.get(key),
  };
}

describe("EgoAgentOS i18n", () => {
  it("keeps the English and Chinese catalogs structurally identical", () => {
    expect(Object.keys(translations.zh).sort()).toEqual(Object.keys(translations.en).sort());
    expect(Object.keys(translations.en).length).toBeGreaterThan(120);
  });

  it("translates core Research Composer copy and interpolates values", () => {
    expect(translate("zh", "composer.titleLine1")).toBe("从一个研究问题出发");
    expect(translate("en", "composer.chain.cells", { count: 165 })).toBe("165 deterministic cells");
    expect(translate("zh", "evidence.artifactsPresent", { present: 4, required: 7 })).toBe(
      "已具备 4 / 7 份证据",
    );
  });

  it("normalizes browser locale variants without accepting unrelated values", () => {
    expect(normalizeLanguage("zh-Hans-CN")).toBe("zh");
    expect(normalizeLanguage("EN_us")).toBe("en");
    expect(normalizeLanguage("fr-FR")).toBeNull();
    expect(htmlLanguage("zh")).toBe("zh-CN");
  });

  it("persists language, updates html lang, and notifies subscribers", () => {
    const storage = memoryStorage();
    const targetDocument = { documentElement: { lang: "" } };
    const store = createI18nStore({ storage, document: targetDocument });
    const subscriber = vi.fn();
    const unsubscribe = store.subscribe(subscriber);

    store.setLanguage("zh");

    expect(store.getLanguage()).toBe("zh");
    expect(storage.value(LANGUAGE_STORAGE_KEY)).toBe("zh");
    expect(targetDocument.documentElement.lang).toBe("zh-CN");
    expect(store.t("action.run")).toBe("运行到下一门禁");
    expect(subscriber).toHaveBeenCalledOnce();

    unsubscribe();
    store.toggleLanguage();
    expect(subscriber).toHaveBeenCalledOnce();
    expect(targetDocument.documentElement.lang).toBe("en");
  });

  it("restores a saved language and degrades safely when persistence is blocked", () => {
    const saved = memoryStorage({ [LANGUAGE_STORAGE_KEY]: "zh-CN" });
    const restored = createI18nStore({ storage: saved, document: null });
    expect(restored.getLanguage()).toBe("zh");

    const blocked = createI18nStore({
      storage: {
        getItem: () => { throw new Error("blocked"); },
        setItem: () => { throw new Error("blocked"); },
      },
      document: null,
    });
    expect(() => blocked.setLanguage("zh")).not.toThrow();
    expect(blocked.t("nav.compose")).toBe("研究编排器");
  });

  it("translates known dynamic states and preserves unknown server values", () => {
    expect(labelForStage("zh", "APPROVAL")).toBe("审批");
    expect(labelForStatus("zh", "waiting_for_human")).toBe("等待人工操作");
    expect(labelForStatus("en", "provider_specific_state")).toBe("provider specific state");
  });
});
