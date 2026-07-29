export default function InspirationGrid({ images, selectedImageUrl, onSelect, disabled }) {
  if (images.length === 0) return null;

  return (
    <div className="inspiration-grid">
      {images.map((img) => (
        <button
          key={img.image_url}
          type="button"
          className={`inspiration-tile ${selectedImageUrl === img.image_url ? "selected" : ""}`}
          onClick={() => onSelect(img)}
          disabled={disabled}
          title={img.title || ""}
        >
          <img src={img.image_url} alt={img.title || "Pinterest inspiration"} loading="lazy" />
        </button>
      ))}
    </div>
  );
}
