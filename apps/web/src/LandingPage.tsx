import { ArrowRight, Check, Database, FileCheck2, Menu, ShieldCheck, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useI18n } from "./i18n";

const sourceUrl = "https://github.com/mythrise/ego_agent_infra";

export function LandingPage() {
  const { language, setLanguage } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);
  const zh = language === "zh";

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    const desktop = typeof window.matchMedia === "function" ? window.matchMedia("(min-width: 901px)") : null;
    const onDesktop = () => desktop?.matches && setMenuOpen(false);
    window.addEventListener("keydown", onKeyDown);
    desktop?.addEventListener("change", onDesktop);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      desktop?.removeEventListener("change", onDesktop);
    };
  }, []);

  useEffect(() => {
    document.body.classList.toggle("landing-menu-open", menuOpen);
    return () => document.body.classList.remove("landing-menu-open");
  }, [menuOpen]);

  const close = () => setMenuOpen(false);

  return (
    <section className="landing-shell" id="top" aria-labelledby="landing-title">
      <div className="landing-grain" aria-hidden="true" />
      <div className="landing-orbit" aria-hidden="true">
        <svg viewBox="0 0 1400 700" preserveAspectRatio="xMidYMid slice">
          <path className="orbit-path orbit-path-soft" d="M-60 500 C230 610 340 185 650 345 S1030 610 1460 180" />
          <path className="orbit-path orbit-path-live" d="M-60 500 C230 610 340 185 650 345 S1030 610 1460 180" />
          {[180, 420, 700, 990, 1240].map((cx, index) => (
            <g key={cx} transform={`translate(${cx} ${index % 2 ? 365 : 430})`}>
              <circle r="11" className="orbit-node-ring" />
              <circle r="3" className="orbit-node-core" />
            </g>
          ))}
        </svg>
        <div className="orbit-labels">
          <span>INPUT</span><span>MATRIX</span><span>RXP</span><span>EVIDENCE</span><span>FOCUS.md</span>
        </div>
      </div>

      <div className="landing-page">
        <header className="landing-header">
          <a className="landing-logo landing-appear landing-scale" href="#top" aria-label="EgoAgentOS">
            <LogoMark />
            <span>EgoAgent<span className="logo-suffix">OS</span></span>
          </a>

          <nav id="landing-navigation" className={`landing-nav ${menuOpen ? "is-open" : ""}`} aria-label={zh ? "主导航" : "Primary navigation"}>
            <a href="#protocol" onClick={close}>{zh ? "确定性" : "Determinism"}</a>
            <a href="#memory" onClick={close}>{zh ? "新鲜记忆" : "Fresh memory"}</a>
            <a href="#evidence" onClick={close}>{zh ? "证据" : "Evidence"}</a>
            <a href={sourceUrl} target="_blank" rel="noreferrer" onClick={close}>{zh ? "源码" : "Source"}</a>
          </nav>

          <div className="landing-tools">
            <div className="locale-switch" role="group" aria-label={zh ? "语言" : "Language"}>
              <button className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")} aria-pressed={language === "en"}>EN</button>
              <button className={language === "zh" ? "active" : ""} onClick={() => setLanguage("zh")} aria-pressed={language === "zh"}>中</button>
            </div>
            <a className="liquid-button liquid-solid landing-header-cta" href="#cockpit">
              {zh ? "进入系统" : "Open cockpit"}
            </a>
            <button
              className="landing-burger"
              type="button"
              aria-expanded={menuOpen}
              aria-controls="landing-navigation"
              aria-label={menuOpen ? (zh ? "关闭菜单" : "Close menu") : (zh ? "打开菜单" : "Open menu")}
              onClick={() => setMenuOpen((value) => !value)}
            >
              {menuOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
          </div>
        </header>

        <main className="landing-hero">
          <div className="landing-copy">
            <div className="landing-badge landing-appear landing-pop">
              <ShieldCheck size={16} />
              {zh ? "确定性自动科研基础设施" : "DETERMINISTIC AUTORESEARCH INFRASTRUCTURE"}
            </div>
            <h1 id="landing-title">
              <span className="headline-line landing-appear landing-mask">
                {zh ? <>让 <em>AI 实验</em> 成为</> : <>Turn <em>AI experiments</em> into</>}
              </span>
              <span className="headline-line landing-appear landing-mask landing-mask-late">
                {zh ? "可复核证据。" : "replayable evidence."}
              </span>
            </h1>
            <p className="landing-lede landing-appear landing-soft">
              {zh
                ? "将任意基线或研究想法编译为有界实验、独立复核与持续新鲜的 Agent 记忆。"
                : "Compile any baseline or research idea into bounded runs, independent review, and fresh agent memory."}
            </p>
            <div className="landing-actions">
              <a className="liquid-button liquid-solid landing-appear landing-button-in" href="#cockpit">
                {zh ? "进入科研控制台" : "Open research cockpit"}<ArrowRight size={15} />
              </a>
              <a className="liquid-button liquid-ghost landing-appear landing-side" href="#evidence">
                {zh ? "查看证据链" : "Inspect evidence chain"}
              </a>
            </div>
          </div>
        </main>

        <footer className="landing-stats" aria-label={zh ? "已验证演示回执" : "Verified demo receipt"}>
          <LandingStat icon={<Check size={18} />} value="165" label={zh ? "个确定性实验单元" : "deterministic cells"} />
          <LandingStat icon={<FileCheck2 size={18} />} value="7/7" label={zh ? "证据门可闭合" : "evidence gates sealable"} />
          <LandingStat icon={<Database size={18} />} value="4/4" label={zh ? "Agent 记忆可压缩" : "agent memories compactable"} />
        </footer>
      </div>
    </section>
  );
}

function LandingStat({ icon, value, label }: { icon: React.ReactNode; value: string; label: string }) {
  return (
    <div className="landing-stat landing-appear landing-stat-in">
      <span className="landing-stat-icon">{icon}</span>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function LogoMark() {
  return (
    <svg className="landing-logo-mark" width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <g transform="rotate(-30 12 12)">
        <circle cx="7.3" cy="3.2" r="1.45" />
        <rect x="5.5" y="4.7" width="3.6" height="14.6" rx="1.8" />
        <rect x="14.9" y="4.7" width="3.6" height="14.6" rx="1.8" />
        <circle cx="16.7" cy="20.8" r="1.45" />
      </g>
    </svg>
  );
}
