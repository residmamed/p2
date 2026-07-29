// ⌘K quick-action palette. Pure presentation + keyboard plumbing: the parent
// supplies actions [{ id, label, hint?, section?, run }] and rebuilds the list
// every render, so nothing here is memoized on action identity. The navbar
// owns the visible trigger button; this component only listens for the
// shortcut and renders the overlay while open.
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import "./CommandPalette.css";

export default function CommandPalette({ actions }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef(null);

  useEffect(() => {
    function onKey(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        // Don't open behind an already-open modal (compare / margin / message)
        // — it would mount hidden under the backdrop and steal keyboard focus.
        if (!open && document.querySelector(".modal-backdrop")) return;
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Fresh query + cursor every time the palette opens.
  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
    }
  }, [open]);

  const q = query.trim().toLowerCase();
  const all = actions || [];
  const filtered = q
    ? all.filter(
        (a) =>
          String(a.label || "").toLowerCase().includes(q) ||
          String(a.hint || "").toLowerCase().includes(q)
      )
    : all;

  // Group by section (values arrive already translated), preserving the
  // order sections first appear in.
  const groups = [];
  const bySection = new Map();
  for (const a of filtered) {
    const key = a.section || "";
    let group = bySection.get(key);
    if (!group) {
      group = { section: key, items: [] };
      bySection.set(key, group);
      groups.push(group);
    }
    group.items.push(a);
  }
  const flat = groups.flatMap((g) => g.items);
  const cursor = flat.length ? Math.min(active, flat.length - 1) : -1;

  useEffect(() => {
    listRef.current
      ?.querySelector('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor, open]);

  function runAction(action) {
    action.run?.();
    setOpen(false);
  }

  function onInputKeyDown(e) {
    if (!flat.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((cursor + 1) % flat.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((cursor - 1 + flat.length) % flat.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      runAction(flat[cursor]);
    }
  }

  if (!open) return null;

  let rowIndex = -1;

  return (
    <div
      className="cp-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) setOpen(false);
      }}
    >
      <div className="cp-palette" role="dialog" aria-modal="true" aria-label={t("cpTitle")}>
        <input
          className="cp-input"
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onInputKeyDown}
          placeholder={t("cpPlaceholder")}
          aria-label={t("cpSearchLabel")}
          autoFocus
        />
        <div className="cp-list" role="listbox" aria-label={t("cpTitle")} ref={listRef}>
          {flat.length === 0 ? (
            <div className="cp-empty">{t("cpNoResults")}</div>
          ) : (
            groups.map((group) => (
              <div key={group.section || "cp-ungrouped"}>
                {group.section && <div className="cp-section">{group.section}</div>}
                {group.items.map((a) => {
                  rowIndex += 1;
                  const i = rowIndex;
                  return (
                    <button
                      key={a.id}
                      type="button"
                      role="option"
                      aria-selected={i === cursor}
                      data-active={i === cursor}
                      className="cp-row"
                      onMouseEnter={() => setActive(i)}
                      onClick={() => runAction(a)}
                    >
                      <span className="cp-row-label">{a.label}</span>
                      {a.hint && <span className="cp-row-hint">{a.hint}</span>}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
        <div className="cp-footer">{t("cpHints")}</div>
      </div>
    </div>
  );
}
