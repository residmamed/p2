import { SITE_LABELS, SITE_COLORS, isLensSite, isRetailSite } from "../sites";
import { useI18n } from "../i18n";

function renderContact(product, t) {
  const { contact_type, contact_value } = product;

  if (!contact_type || !contact_value) {
    return <span className="contact-unavailable">{t("noContactInfo")}</span>;
  }

  if (contact_type === "form") {
    return (
      <a className="contact-button" href={contact_value} target="_blank" rel="noreferrer">
        {t("contactSeller")}
      </a>
    );
  }

  if (contact_value.includes("@")) {
    return (
      <a className="contact-button" href={`mailto:${contact_value}`}>
        {t("email")} {contact_value}
      </a>
    );
  }

  if (contact_value.startsWith("http")) {
    return (
      <a className="contact-button" href={contact_value} target="_blank" rel="noreferrer">
        {t("contactSeller")}
      </a>
    );
  }

  return (
    <a className="contact-button" href={`tel:${contact_value}`}>
      {t("call")} {contact_value}
    </a>
  );
}

function Rating({ product }) {
  if (product.rating == null) return null;
  return (
    <div className="product-rating">
      ★ {product.rating.toFixed(1)}
      {product.review_count != null && (
        <span className="product-review-count"> ({product.review_count.toLocaleString()})</span>
      )}
    </div>
  );
}

function SellerRow({ seller, t, hideContact }) {
  const isLens = isLensSite(seller.site);
  return (
    <div className="seller-row">
      <div className="seller-row-head">
        <span
          className="site-badge site-badge-inline"
          style={{ background: SITE_COLORS[seller.site] || "#888" }}
        >
          {SITE_LABELS[seller.site] || seller.site}
        </span>
        {!isLens &&
          seller.seller_name &&
          (seller.seller_url ? (
            <a className="product-seller" href={seller.seller_url} target="_blank" rel="noreferrer">
              {seller.seller_name}
            </a>
          ) : (
            <span className="product-seller">{seller.seller_name}</span>
          ))}
        <span className="seller-row-price">{seller.price_text || t("priceOnRequest")}</span>
      </div>
      {!isLens && !hideContact && <div className="product-contact">{renderContact(seller, t)}</div>}
    </div>
  );
}

export default function ProductCard({ product, selectable = false, selected = false, onToggleSelect }) {
  const { t } = useI18n();
  const sellers = product.sellers || [];
  const hasMultipleSellers = sellers.length > 1;
  const isLens = isLensSite(product.site);
  // Retail product cards (Product Search) show rating and no inquiry/contact
  // form; manufacturer/sourcing cards show seller name + contact instead.
  const isRetail = isRetailSite(product.site);
  // No rank badge on the card: results are grouped by store and ordered
  // best-selling first within each, so a card's position already conveys its
  // rank. A number stamped on the image only repeated that, and read as a
  // cross-store claim the grouped ordering doesn't make. rank_basis still
  // travels on the product for the toolbar and Excel export.

  return (
    <div className={`product-card ${selectable && selected ? "product-card-selected" : ""}`}>
      {selectable && (
        <label className="product-select" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={selected} onChange={() => onToggleSelect?.(product)} />
        </label>
      )}
      <a href={product.product_url} target="_blank" rel="noreferrer" className="product-image-link">
        {product.image_url ? (
          <img src={product.image_url} alt={product.title} loading="lazy" />
        ) : (
          <div className="product-image-placeholder">{t("noImage")}</div>
        )}
        <span
          className="site-badge"
          style={{ background: SITE_COLORS[product.site] || "#888" }}
        >
          {SITE_LABELS[product.site] || product.site}
        </span>
        {product.detected_item && (
          <span className="provenance-badge">via {product.detected_item} · Pinterest</span>
        )}
        {product.image_match != null && (
          <span className={`match-badge ${product.exact_match ? "match-badge-exact" : "match-badge-similar"}`}>
            {product.exact_match
              ? t("exactMatch")
              : `${Math.round(product.image_match * 100)}% ${t("match")}`}
          </span>
        )}
      </a>
      <div className="product-body">
        <a href={product.product_url} target="_blank" rel="noreferrer" className="product-title">
          {product.title}
        </a>
        {product.price_text ? (
          <div className="product-price">
            {product.price_text}
            {product.moq && <span className="product-moq"> · {product.moq}</span>}
          </div>
        ) : isRetail ? (
          // The store returned this row without a price — out of stock, or a
          // price it only shows in the cart. Said plainly, because the blank
          // gap that used to sit here read as a rendering bug, and the $0.00
          // before it read as a free product.
          <div className="product-price product-price-missing">{t("priceNotListed")}</div>
        ) : (
          <div className="product-price">{t("priceOnRequest")}</div>
        )}
        {isRetail && (
          <div className="product-meta-row">
            <Rating product={product} />
          </div>
        )}
        {hasMultipleSellers ? (
          <div className="product-sellers">
            <div className="product-sellers-heading">{t("availableFrom", sellers.length)}</div>
            {sellers.map((seller, i) => (
              <SellerRow key={`${seller.product_url}-${i}`} seller={seller} t={t} hideContact={isRetail} />
            ))}
          </div>
        ) : (
          <>
            {!isLens && !isRetail && product.seller_name && (
              <div className="product-seller">
                {product.seller_url ? (
                  <a href={product.seller_url} target="_blank" rel="noreferrer">
                    {product.seller_name}
                  </a>
                ) : (
                  product.seller_name
                )}
              </div>
            )}
            {!isLens && !isRetail && <div className="product-contact">{renderContact(product, t)}</div>}
          </>
        )}
      </div>
    </div>
  );
}
