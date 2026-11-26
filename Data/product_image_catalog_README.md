# Image-based product catalog

- **`product_image_catalog.json`** — 17 product groups, all 33 source image paths, localized names, descriptions, notes, use/care guidance and search aliases in English, বাংলা and Banglish. Every product has price, size, color, availability and custom-design fields.
- **`product_image_rag.json`** — 68 multilingual retrieval documents with stable IDs, scalar vector-store metadata, retrieval guidance, query-expansion hints and 30 evaluation cases. This is an indexing-ready JSON export, **not an installed vector database**.

The existing `Data/products.json`, SQL database and Chroma index were not changed. These new files are **not automatically connected to the running chatbot**. Do not pass the catalog envelope directly to the existing legacy import script: it expects a flat list and does not support these localized/draft fields.

## What needs your confirmation

Prices, currency, exact measurements, available sizes/colors, material, stock, package contents and custom-design terms are not recoverable from the images. They are deliberately `null`, empty lists or `needs_confirmation`. A `null` price is **not free**. Observed image colors are **not confirmed stocked variants**. No currency is assumed.

Important review items:

- No `product16` image exists; no replacement product was invented.
- `IMG_004` includes visibly different decorative layouts; confirm whether these are sides, variants or different products before publishing.
- `IMG_005` and `IMG_006` might be styled together; neither a bundle nor inclusion is confirmed.
- `IMG_012` has cropped detail photos only; full garment type and sleeves need confirmation.
- `IMG_015` needs confirmation of garment type, closure and whether the square motif is a pocket.
- `IMG_018` cannot be reliably identified from its folded low-resolution photos. Do not advertise it as a specific product type or give specific use instructions yet.

Image references are relative to the repository root. Use the one image marked `is_primary` for the product card and the remaining images as its gallery. Upload/serve the source files and fill `public_url` to display images in web or Messenger clients; a local path is not a public URL. Localized `alt` text is provided for each image.

## Preparing retrieval

1. Complete and verify merchant fields, including a real `shop_id` for multi-shop use. Keep `product_id` stable. Change `review` and `publication_status` only after actual verification.
2. Rebuild and validate from the repository root:

   ```powershell
   python scripts/build_product_image_rag.py
   python scripts/build_product_image_rag.py --check
   ```

3. Index each RAG `documents` entry using its `id`, `page_content` and `metadata`. Use a Bengali-capable multilingual embedding model, plus lexical search over catalog names and aliases. This repository's current ingestion script does **not** automatically read this export.
4. Filter every retrieval and catalog lookup by the correct shop. Resolve exact product IDs/image filenames directly; keep `product_id` for follow-up questions such as `dam koto` or `size ache`.
5. Group discovery hits by `product_id` and join back to `products` for the image gallery and localized display fields. Route price/stock/custom questions to purchase chunks, size/color questions to appearance chunks, and care questions to use/care chunks.
6. Recheck current merchant-confirmed price/stock/custom terms before answering or checkout. Never invent unknown facts or apply budget filters to `null` prices.
7. Run the included evaluation queries against the implemented retriever and measure product-level recall, incorrect-match rate and unknown-fact abstention. The provided cases are expectations, **not measured retrieval results**. Add real customer queries and tune ranking before production use.

For example, a product card uses `products[0].images` for the gallery, `products[0].name.bn` for the Bangla name, and `products[0].description.banglish` for Banglish details. When a customer asks for a custom design, collect their reference, requested size/color, quantity and deadline; get seller approval before promising availability, cost or delivery.
