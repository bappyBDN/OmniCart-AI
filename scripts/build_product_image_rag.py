"""Validate the image-reviewed catalog and export portable multilingual RAG JSON.

This does not modify the application's SQL database, vector index or old catalog.
Run with --check to verify that the committed RAG export is up to date.
"""

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "Data" / "product_image_catalog.json"
OUTPUT = ROOT / "Data" / "product_image_rag.json"
LANGUAGES = ("en", "bn", "banglish")
LABELS = {"en": "English", "bn": "বাংলা", "banglish": "Banglish"}


def localized_lines(values):
    return "\n".join(f"{LABELS[language]}: {values[language]}" for language in LANGUAGES)


def known(value):
    if value is None or value == []:
        return "Unknown / নিশ্চিত নয় / nishchit noy"
    if isinstance(value, list):
        return ", ".join(str(part) for part in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def validate_catalog(catalog, root=ROOT):
    products = catalog["products"]
    if len(products) != catalog["product_count"]:
        raise ValueError("Product count does not match catalog metadata")
    ids = [product["product_id"] for product in products]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate product IDs")
    paths = []
    numbers = []
    for product in products:
        product_id = product["product_id"]
        numbers.append(product["source_product_number"])
        for field in ("name", "description", "how_to_use", "care", "notes"):
            for language in LANGUAGES:
                value = product[field].get(language)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{product_id}: missing {field}.{language}")
        for language in LANGUAGES:
            if not product["category"].get(language):
                raise ValueError(f"{product_id}: missing category.{language}")
            aliases = product["search_aliases"].get(language)
            if not isinstance(aliases, list) or not aliases:
                raise ValueError(f"{product_id}: missing aliases.{language}")
        if sum(image["is_primary"] for image in product["images"]) != 1:
            raise ValueError(f"{product_id}: exactly one primary image is required")
        for image in product["images"]:
            path = image["path"]
            resolved = (root / path).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                raise ValueError(f"{product_id}: invalid image path {path}")
            if any(not image["alt"].get(language) for language in LANGUAGES):
                raise ValueError(f"{product_id}: incomplete image alt text")
            paths.append(path)
        for field in ("price", "discounted_price", "stock_count"):
            value = product[field]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            ):
                raise ValueError(f"{product_id}: invalid {field}")
        if product["stock_count"] is not None and not isinstance(product["stock_count"], int):
            raise ValueError(f"{product_id}: stock_count must be an integer or null")
        if product["price"] is not None and not (product["currency"] or catalog["currency"]):
            raise ValueError(f"{product_id}: a confirmed price needs a currency")
        if product["discounted_price"] is not None and (
            product["price"] is None or product["discounted_price"] > product["price"]
        ):
            raise ValueError(f"{product_id}: invalid discount relative to base price")
        if product["availability"] not in {
            "needs_confirmation", "in_stock", "out_of_stock", "limited_stock", "preorder"
        }:
            raise ValueError(f"{product_id}: invalid availability")
        if product["availability"] in {"in_stock", "limited_stock"} and product["stock_count"] == 0:
            raise ValueError(f"{product_id}: stock contradicts availability")
        if product["availability"] == "out_of_stock" and product["stock_count"] not in (None, 0):
            raise ValueError(f"{product_id}: stock contradicts out-of-stock status")
        if product["custom_design"]["available"] is not None and not isinstance(
            product["custom_design"]["available"], bool
        ):
            raise ValueError(f"{product_id}: custom_design.available must be boolean or null")
        if not set(product["related_product_ids"]).issubset(ids):
            raise ValueError(f"{product_id}: related product does not exist")
    if len(numbers) != len(set(numbers)):
        raise ValueError("Duplicate source product numbers")
    if len(paths) != catalog["image_count"] or len(paths) != len(set(paths)):
        raise ValueError("Image count mismatch or image path reused between products")
    source_paths = {
        path.relative_to(root).as_posix()
        for path in (root / catalog["source_directory"]).glob("*.jpg")
    }
    if set(paths) != source_paths:
        raise ValueError("The catalog must reference every source JPG exactly once")
    missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
    if missing != catalog["source_product_numbers_missing"]:
        raise ValueError("Missing product-number metadata is incorrect")


def build_documents(catalog):
    documents = []
    for index, product in enumerate(catalog["products"]):
        product_id = product["product_id"]
        primary = next(image for image in product["images"] if image["is_primary"])
        header = f"Product ID: {product_id}\n{localized_lines(product['name'])}"
        aliases = "\n".join(
            f"Aliases ({language}): {', '.join(product['search_aliases'][language])}"
            for language in LANGUAGES
        )
        category = localized_lines({language: product["category"][language] for language in LANGUAGES})
        attributes = "\n".join(
            f"{key}: {known(value)}" for key, value in product["visual_attributes"].items()
        )
        measurements = "; ".join(
            f"{key}: {known(value)}" for key, value in product["measurements"].items()
        )
        currency = product["currency"] or catalog["currency"]
        custom = product["custom_design"]
        purchase = (
            f"Price / দাম / dam: {known(product['price'])}\n"
            f"Discounted price / ছাড়ের দাম / charer dam: {known(product['discounted_price'])}\n"
            f"Currency / মুদ্রা / mudra: {known(currency)}\n"
            f"Availability / স্টক আছে কি / stock ache ki: {product['availability']}\n"
            f"Stock quantity / স্টকের সংখ্যা / stock shongkha: {known(product['stock_count'])}\n"
            f"Package contents / প্যাকেজে কী আছে / package-e ki ache: {known(product['package_contents'])}\n"
            f"Custom design / কাস্টম ডিজাইন / custom noksha available: {known(custom['available'])}\n"
            f"Custom design status: {custom['status']}\n"
            f"Custom design additional price: {known(custom['additional_price'])}\n"
            f"Custom design lead time (days): {known(custom['lead_time_days'])}\n"
            "English: Unconfirmed commercial fields require seller confirmation. A design request is not an accepted custom order.\n"
            "বাংলা: অনিশ্চিত বাণিজ্যিক তথ্য বিক্রেতার কাছে নিশ্চিত করতে হবে। ডিজাইনের অনুরোধ মানেই কাস্টম অর্ডার গ্রহণ নয়।\n"
            "Banglish: Onishchit banijjik tottho bikretar kachhe nishchit korte hobe. Design-er onurodh manei custom order grohon noy."
        )
        sections = {
            "identity": (
                f"Category:\n{category}\nDescription:\n{localized_lines(product['description'])}\n{aliases}"
            ),
            "appearance_and_size": (
                f"Visual observations (not confirmed available variants):\n{attributes}\n"
                f"Available sizes / সাইজ / size: {known(product['sizes'])}\n"
                f"Measurements / মাপ / map: {measurements}\n"
                f"Confirmed available colors / পাওয়া যাবে এমন রং / available rong: {known(product['available_colors'])}\n"
                f"Material / কাপড় / kapor: {known(product['material'])}\n"
                f"Notes:\n{localized_lines(product['notes'])}"
            ),
            "use_and_care": (
                f"How to use / কীভাবে ব্যবহার করবেন / kibhabe porbo:\n{localized_lines(product['how_to_use'])}\n"
                f"Care / যত্ন / jotno:\n{localized_lines(product['care'])}"
            ),
            "purchase_and_custom_design": purchase,
        }
        metadata = {
            "catalog_id": catalog["catalog_id"],
            "product_id": product_id,
            "source_product_number": product["source_product_number"],
            "title": product["name"]["en"],
            "title_bn": product["name"]["bn"],
            "title_banglish": product["name"]["banglish"],
            "category": product["category"]["code"],
            "source_file": "Data/product_image_catalog.json",
            "source_json_pointer": f"/products/{index}",
            "primary_image_path": primary["path"],
            "languages": ",".join(LANGUAGES),
            "evidence": "image_observation_not_merchant_verified",
            "identity_confidence": product["review"]["identity_confidence"],
            "requires_review": product["review"]["required"],
            "unidentified_item": product["category"]["code"] == "unidentified",
            "availability": product["availability"],
            "price_known": product["price"] is not None,
            "sizes_known": bool(product["sizes"]),
            "available_colors_known": bool(product["available_colors"]),
            "custom_design_known": custom["available"] is not None,
        }
        if catalog["shop_id"]:
            metadata["shop_id"] = catalog["shop_id"]
        if product["price"] is not None:
            metadata["price"] = product["price"]
            metadata["currency"] = currency
        for section, body in sections.items():
            documents.append({
                "id": f"{product_id}:{section}:v1",
                "page_content": f"{header}\nSection: {section}\n{body}",
                "metadata": {**metadata, "section": section},
            })
    return documents


def build_export(catalog):
    documents = build_documents(catalog)
    return {
        "schema_version": "1.0.0",
        "catalog_id": catalog["catalog_id"],
        "source_file": "Data/product_image_catalog.json",
        "artifact_type": "rag_document_export_not_vector_index",
        "document_count": len(documents),
        "product_count": len(catalog["products"]),
        "languages": list(LANGUAGES),
        "retrieval_guidance": {
            "status": "Prepared documents only; indexing, application integration and retrieval-quality evaluation are still required. No perfect-retrieval guarantee.",
            "embedding": "Use a multilingual embedding model supporting Bengali and English. Index page_content; preserve id and metadata. Use the same model and its required query/document prefixes at indexing and query time.",
            "hybrid_search": "Combine dense semantic search with lexical search over names, aliases, colors and product IDs. Tune ranking on actual shop queries rather than assuming a fixed score threshold works.",
            "exact_product_lookup": "Resolve exact IMG_ IDs, source image filenames and source_product_number against the catalog first. Fetch the requested product directly instead of relying on embeddings.",
            "follow_up_questions": "For questions such as dam koto, size ache or custom kora jabe, keep the active product_id and filter all chunks to that product. Without context, ask which product the customer means.",
            "intent_sections": {
                "discovery": ["identity", "appearance_and_size"],
                "price_stock_bundle_customization": ["purchase_and_custom_design"],
                "size_color_material": ["appearance_and_size"],
                "usage_care": ["use_and_care"]
            },
            "discovery": "Retrieve identity and appearance chunks, then group by product_id before selecting products. Rerank candidates if available. Join catalog records to return images and all localized fields.",
            "unknown_identity": "Exclude category=unidentified from general category recommendations. Allow it for explicit product ID, image, source number or matching visual-attribute queries; disclose uncertainty.",
            "image_search": "This text export supports image captions, not image-vector similarity. Image-to-product matching requires a vision model or multimodal embeddings plus evaluation.",
            "structured_filters": "Use confirmed catalog values for budget, currency, size and availability filters. Never compare null prices as zero or treat observed image colors as stocked variants.",
            "live_facts": "After retrieval, hydrate price, stock, sizes, available colors and custom-design terms from the current merchant-confirmed source. Do not trust stale vector text for checkout.",
            "tenant_isolation": "Set a real shop_id before indexing in a multi-shop system. Filter by that shop_id in dense search, lexical search, catalog joins and live-fact lookups. Do not use an arbitrary default shop.",
            "citations": "Return product_id and source_json_pointer with answers. For product cards, use the catalog's primary image and localized name. Local image paths must be hosted before web or Messenger display.",
            "unknown_answer": {
                "en": "This information is not confirmed yet. Please check with the seller.",
                "bn": "এই তথ্য এখনো নিশ্চিত নয়। অনুগ্রহ করে বিক্রেতার সঙ্গে নিশ্চিত করুন।",
                "banglish": "Ei tottho ekhono nishchit noy. Onugroho kore bikretar shathe nishchit korun."
            },
            "custom_design_request_fields": ["product_id", "reference_image_or_design_description", "requested_color", "requested_size_and_measurements", "quantity", "needed_by_date"],
            "custom_design_request_rule": "Collect a request only; obtain seller approval of feasibility, price, delivery date and final proof before promising a custom design.",
            "write_strategy": "Upsert documents by stable id. Remove stale document IDs for deleted products or retired sections, scoped to the same shop and catalog. Rebuild this export after catalog edits."
        },
        "query_expansion_hints": {
            "rule": "These are optional intent/color hints, not an implemented tokenizer. Match whole phrases or tokens and preserve the original query. Banglish spelling varies; test with real customer queries.",
            "intents": {
                "price": ["price", "cost", "dam", "daam", "koto taka", "দাম", "কত টাকা"],
                "size": ["size", "saiz", "map", "maap", "সাইজ", "মাপ"],
                "color": ["color", "colour", "rong", "রং", "কালার"],
                "stock": ["available", "availability", "stock ache", "pawa jabe", "পাওয়া যাবে", "স্টক আছে"],
                "usage": ["how to use", "how to wear", "kibhabe porbo", "kivabe use korbo", "কীভাবে পরব", "ব্যবহার"],
                "care": ["wash", "care", "dhobo", "jotno", "ধোয়া", "যত্ন"],
                "custom_design": ["custom design", "custom desing", "customize", "custom kora jabe", "nijer design", "কাস্টম ডিজাইন", "নিজের ডিজাইন"]
            },
            "colors": {
                "green": ["green", "shobuj", "sobuj", "সবুজ"],
                "purple": ["purple", "violet", "beguni", "বেগুনি"],
                "white": ["white", "shada", "sada", "সাদা"],
                "red": ["red", "lal", "laal", "লাল"],
                "blue": ["blue", "nil", "neel", "নীল"],
                "brown": ["brown", "badami", "বাদামি"],
                "black": ["black", "kalo", "কালো"],
                "orange": ["orange", "komola", "কমলা"],
                "yellow": ["yellow", "holud", "হলুদ"]
            }
        },
        "evaluation_queries": evaluation_queries(),
        "documents": documents
    }


def evaluation_queries():
    cases = [
        ("green folk motif short sleeve tunic", ["IMG_001"], "discovery"),
        ("সবুজ লোকজ নকশার ছোট হাতার কুর্তি", ["IMG_001"], "discovery"),
        ("sobuj lokoj noksha top", ["IMG_001"], "discovery"),
        ("purple sleeveless white feather dress", ["IMG_002"], "discovery"),
        ("সাদা ফুলের ওয়াইড লেগ প্যান্ট", ["IMG_003"], "discovery"),
        ("shada fuler pant", ["IMG_003"], "discovery"),
        ("brown kurti white diamond motif", ["IMG_004"], "discovery_with_design_warning"),
        ("নীল মোটিফের লাল কুর্তি", ["IMG_005"], "discovery"),
        ("lal palazzo pant", ["IMG_006"], "discovery"),
        ("ম্যাজেন্টা শাড়ি", ["IMG_007"], "discovery"),
        ("nil collar shirt", ["IMG_008"], "discovery"),
        ("cream maroon yoke kurti", ["IMG_009"], "discovery"),
        ("ডোরাকাটা ফিতা গলার টপ", ["IMG_010"], "discovery"),
        ("teal colorful scenic panel tunic", ["IMG_011"], "discovery"),
        ("হালকা অ্যাকোয়া ঝুলন্ত নকশার গলা", ["IMG_012"], "discovery_with_identity_warning"),
        ("komola ganesh kurti", ["IMG_013"], "discovery"),
        ("কালো গোলাপি সবুজ প্যানেলের ট্যাসেল টপ", ["IMG_014"], "discovery"),
        ("shobuj open front jacket", ["IMG_015"], "discovery"),
        ("beguni shonkho kurti", ["IMG_017"], "discovery"),
        ("product18.jpg", ["IMG_018"], "exact_image_lookup_with_identity_warning"),
        ("ফ্যাকাশে হলুদ বাদামি লুপ নকশার পণ্য", ["IMG_018"], "visual_attribute_lookup_with_identity_warning"),
        ("IMG_005 dam koto", ["IMG_005"], "price_unknown_do_not_quote"),
        ("IMG_003 এর সাইজ ও কোমরের মাপ কত", ["IMG_003"], "size_unknown_ask_seller"),
        ("IMG_014 custom desing kora jabe", ["IMG_014"], "customization_unconfirmed_collect_request_only"),
        ("IMG_007 blouse piece included?", ["IMG_007"], "bundle_unknown_do_not_promise"),
        ("IMG_008 stock ache?", ["IMG_008"], "availability_unknown_ask_seller"),
        ("IMG_017 কীভাবে যত্ন নেব", ["IMG_017"], "usage_care"),
        ("product16", [], "missing_product_do_not_invent"),
        ("smart watch", [], "no_matching_product_abstain"),
        ("500 takar moddhe in stock kurti", [], "cannot_verify_budget_or_stock_from_current_catalog")
    ]
    return [
        {"query": query, "expected_product_ids": ids, "expected_behavior": behavior}
        for query, ids, behavior in cases
    ]


def validate_export(export, catalog):
    documents = export["documents"]
    if len(documents) != export["document_count"] or len(documents) != len(catalog["products"]) * 4:
        raise ValueError("Incorrect RAG document count")
    if len({document["id"] for document in documents}) != len(documents):
        raise ValueError("Duplicate RAG document IDs")
    product_ids = {product["product_id"] for product in catalog["products"]}
    for document in documents:
        if document["metadata"]["product_id"] not in product_ids:
            raise ValueError("RAG document references an unknown product")
        if any(not isinstance(value, (str, int, float, bool)) for value in document["metadata"].values()):
            raise ValueError("Vector metadata must contain scalar, non-null values only")
        if any(LABELS[language] + ":" not in document["page_content"] for language in LANGUAGES):
            raise ValueError("RAG document is missing a language")
    for case in export["evaluation_queries"]:
        if not set(case["expected_product_ids"]).issubset(product_ids):
            raise ValueError("Evaluation query references an unknown product")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate without writing any files")
    args = parser.parse_args()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    validate_catalog(catalog)
    export = build_export(catalog)
    validate_export(export, catalog)
    serialized = json.dumps(export, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != serialized:
            raise SystemExit("RAG export is missing or outdated; run this script without --check")
    else:
        OUTPUT.write_text(serialized, encoding="utf-8")
    print(
        f"Validated {catalog['product_count']} products, {catalog['image_count']} image paths, "
        f"{export['document_count']} multilingual RAG documents and "
        f"{len(export['evaluation_queries'])} evaluation cases."
    )


if __name__ == "__main__":
    main()
