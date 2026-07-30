import ExcelJS from "exceljs";
import { isLensSite } from "./sites";

const IMAGE_FETCH_TIMEOUT_MS = 8000;
const IMAGE_CELL_SIZE = 90;
const ROW_HEIGHT = 72;

async function fetchImageAsBuffer(url) {
  if (!url) return null;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), IMAGE_FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) return null;
    const contentType = response.headers.get("content-type") || "";
    const extension = contentType.includes("png") ? "png" : "jpeg";
    const buffer = await response.arrayBuffer();
    return { buffer, extension };
  } catch {
    // Cross-origin image hosts that don't allow browser-side fetch, or a
    // dead link — skip the picture for this row rather than failing the
    // whole export.
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

function sellerLabel(product) {
  if (isLensSite(product.site)) return "";
  if (product.sellers?.length > 1) {
    return product.sellers
      .map((s) => s.seller_name)
      .filter(Boolean)
      .join(", ");
  }
  return product.seller_name || "";
}

export async function exportProductsToExcel(products, filename = "products.xlsx") {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Products");

  // No phone column: supplier phone numbers aren't shown in the UI, and an
  // export that carried them would put back on a spreadsheet exactly what was
  // taken off the screen.
  sheet.columns = [
    { header: "Image", key: "image", width: 18 },
    { header: "Name", key: "name", width: 50 },
    { header: "Company", key: "seller", width: 34 },
    { header: "Price", key: "price", width: 18 },
    { header: "MOQ", key: "moq", width: 16 },
  ];
  sheet.getRow(1).font = { bold: true };

  const images = await Promise.all(products.map((p) => fetchImageAsBuffer(p.image_url)));

  products.forEach((product, i) => {
    const rowNumber = i + 2;
    const row = sheet.addRow({
      name: product.title || "",
      seller: sellerLabel(product),
      price: product.price_text || "",
      moq: product.moq || "",
    });
    row.height = ROW_HEIGHT;

    const imageData = images[i];
    if (imageData) {
      const imageId = workbook.addImage({ buffer: imageData.buffer, extension: imageData.extension });
      sheet.addImage(imageId, {
        tl: { col: 0.1, row: rowNumber - 1 + 0.1 },
        ext: { width: IMAGE_CELL_SIZE, height: IMAGE_CELL_SIZE },
      });
    }
  });

  const buffer = await workbook.xlsx.writeBuffer();
  const blob = new Blob([buffer], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
