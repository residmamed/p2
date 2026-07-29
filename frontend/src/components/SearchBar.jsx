import { useRef, useState } from "react";

export default function SearchBar({ onTextSearch, onImageSearch, disabled }) {
  const [query, setQuery] = useState("");
  const fileInputRef = useRef(null);

  function handleSubmit(e) {
    e.preventDefault();
    if (query.trim()) {
      onTextSearch(query.trim());
    }
  }

  function handleFileChange(e) {
    const file = e.target.files?.[0];
    if (file) {
      onImageSearch(file);
    }
    e.target.value = "";
  }

  return (
    <form className="search-bar" onSubmit={handleSubmit}>
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search for a product..."
        disabled={disabled}
      />
      <button
        type="button"
        className="camera-button"
        title="Search by image"
        onClick={() => fileInputRef.current?.click()}
        disabled={disabled}
      >
        📷
      </button>
      <input
        type="file"
        accept="image/jpeg,image/png,image/webp,image/bmp,image/avif"
        ref={fileInputRef}
        onChange={handleFileChange}
        style={{ display: "none" }}
      />
      <button type="submit" disabled={disabled || !query.trim()}>
        Search
      </button>
    </form>
  );
}
