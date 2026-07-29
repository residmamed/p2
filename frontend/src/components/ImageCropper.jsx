import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";

// Lets the user drag a rectangle over the image to select the exact item to
// search for, instead of sending the whole photo. Percent-based selection
// (0-100 of the displayed image) is mapped to natural pixel coordinates when
// cropping, so it works regardless of how large the image is displayed.
export default function ImageCropper({ file, onConfirm, onCancel, busy }) {
  const { t } = useI18n();
  const [imageUrl, setImageUrl] = useState(null);
  const [selection, setSelection] = useState(null); // {x1,y1,x2,y2} in % of container
  const [dragStart, setDragStart] = useState(null);
  const containerRef = useRef(null);
  const imgRef = useRef(null);

  useEffect(() => {
    const url = URL.createObjectURL(file);
    setImageUrl(url);
    setSelection(null);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // Mouse drags are tracked on the window, not the cropper element: the
  // element-scoped mousemove/mouseup used to stop working the moment the
  // cursor left the image bounds mid-drag (a very common case when dragging
  // toward an edge/corner), which discarded the in-progress selection.
  useEffect(() => {
    if (!dragStart) return;

    function onMove(e) {
      const p = pointToPercent(e);
      setSelection({
        x1: Math.min(dragStart.x, p.x),
        y1: Math.min(dragStart.y, p.y),
        x2: Math.max(dragStart.x, p.x),
        y2: Math.max(dragStart.y, p.y),
      });
    }

    function onUp() {
      setDragStart(null);
      setSelection((prev) => finalizeSelection(prev));
    }

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragStart]);

  function finalizeSelection(sel) {
    if (sel && sel.x2 - sel.x1 < 2 && sel.y2 - sel.y1 < 2) {
      return null; // treat as a click, not a real selection
    }
    return sel;
  }

  function pointToPercent(e) {
    const rect = containerRef.current.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    const x = Math.min(100, Math.max(0, ((clientX - rect.left) / rect.width) * 100));
    const y = Math.min(100, Math.max(0, ((clientY - rect.top) / rect.height) * 100));
    return { x, y };
  }

  function handleStart(e) {
    if (busy) return;
    const p = pointToPercent(e);
    setDragStart(p);
    setSelection({ x1: p.x, y1: p.y, x2: p.x, y2: p.y });
  }

  function handleTouchMove(e) {
    if (!dragStart) return;
    const p = pointToPercent(e);
    setSelection({
      x1: Math.min(dragStart.x, p.x),
      y1: Math.min(dragStart.y, p.y),
      x2: Math.max(dragStart.x, p.x),
      y2: Math.max(dragStart.y, p.y),
    });
  }

  function handleTouchEnd() {
    setDragStart(null);
    setSelection((prev) => finalizeSelection(prev));
  }

  async function handleConfirm() {
    const img = imgRef.current;
    if (!img) return;

    const canvas = document.createElement("canvas");
    let sx = 0, sy = 0, sw = img.naturalWidth, sh = img.naturalHeight;

    if (selection) {
      sx = (selection.x1 / 100) * img.naturalWidth;
      sy = (selection.y1 / 100) * img.naturalHeight;
      sw = ((selection.x2 - selection.x1) / 100) * img.naturalWidth;
      sh = ((selection.y2 - selection.y1) / 100) * img.naturalHeight;
    }

    canvas.width = Math.max(1, Math.round(sw));
    canvas.height = Math.max(1, Math.round(sh));
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);

    canvas.toBlob(
      (blob) => {
        const cropped = new File([blob], file.name || "crop.jpg", { type: "image/jpeg" });
        onConfirm(cropped);
      },
      "image/jpeg",
      0.9
    );
  }

  if (!imageUrl) return null;

  const dims = selection
    ? {
        top: { top: 0, left: 0, right: 0, height: `${selection.y1}%` },
        bottom: { top: `${selection.y2}%`, left: 0, right: 0, bottom: 0 },
        left: { top: `${selection.y1}%`, left: 0, width: `${selection.x1}%`, height: `${selection.y2 - selection.y1}%` },
        right: { top: `${selection.y1}%`, left: `${selection.x2}%`, right: 0, height: `${selection.y2 - selection.y1}%` },
        box: {
          top: `${selection.y1}%`,
          left: `${selection.x1}%`,
          width: `${selection.x2 - selection.x1}%`,
          height: `${selection.y2 - selection.y1}%`,
        },
      }
    : null;

  return (
    <div>
      <div
        className="cropper"
        ref={containerRef}
        onMouseDown={handleStart}
        onTouchStart={handleStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
      >
        <img ref={imgRef} src={imageUrl} alt="To crop" draggable={false} />
        {dims && (
          <>
            <div className="cropper-dim" style={dims.top} />
            <div className="cropper-dim" style={dims.bottom} />
            <div className="cropper-dim" style={dims.left} />
            <div className="cropper-dim" style={dims.right} />
            <div className="cropper-selection" style={dims.box} />
          </>
        )}
      </div>
      <div className="cropper-actions">
        <button type="button" className="secondary-button" onClick={onCancel} disabled={busy}>
          {t("cancel")}
        </button>
        <button type="button" className="secondary-button" onClick={() => setSelection(null)} disabled={busy}>
          {t("resetCrop")}
        </button>
        <button type="button" className="primary-button" onClick={handleConfirm} disabled={busy}>
          {selection ? t("searchThisCrop") : t("searchFullPhoto")}
        </button>
      </div>
    </div>
  );
}
