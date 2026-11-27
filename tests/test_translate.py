"""Phase 1 regressions for the final language-localisation step.

The customer's reply must never silently lose a price, stock count, product ID
or citation when the provider translates it. A failed or lossy translation is
safer as the already-grounded English answer than as a fluent lie.
"""
from langchain_core.messages import AIMessage

from backend.translate import localize_answer, preserves_grounding


ANSWER = (
    "The Premium Cotton T-Shirt is ৳19.99, SKU_101 has 15 in stock "
    "[product:SKU_101]. Order ORD-1001 is ready."
)
TRANSLATED = (
    "প্রিমিয়াম কটন টি-শার্টটির দাম ৳১৯.৯৯, SKU_101-এ ১৫টি স্টকে আছে "
    "[product:SKU_101]। অর্ডার ORD-1001 প্রস্তুত।"
)


class Model:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return self.reply


def test_bangla_input_is_returned_unchanged_without_a_model_call():
    model = Model(AIMessage(content="অন্য কিছু"))
    assert localize_answer("ইতিমধ্যে বাংলা উত্তর।", "bn", model) == "ইতিমধ্যে বাংলা উত্তর।"
    assert model.calls == 0


def test_banglish_customer_gets_bengali_script_reply():
    model = Model(AIMessage(content="আপনার কুর্তিটা সুন্দর।"))
    assert localize_answer("Apnar kurti ta sundor.", "bn-Latn", model) == "আপনার কুর্তিটা সুন্দর।"
    assert model.calls == 1


def test_english_customer_is_never_translated():
    model = Model(AIMessage(content="এটি ভুল"))
    assert localize_answer("Your order is ready.", "en", model) == "Your order is ready."
    assert model.calls == 0


def test_provider_failure_returns_the_grounded_answer():
    class Broken:
        def invoke(self, messages):
            raise RuntimeError("provider down")

    assert localize_answer(ANSWER, "bn", Broken()) == ANSWER


def test_list_typed_ai_message_content_is_extracted():
    model = Model(AIMessage(content=[{"type": "text", "text": TRANSLATED}]))
    assert localize_answer(ANSWER, "bn", model) == TRANSLATED


def test_numbers_currency_amounts_ids_and_citations_survive_translation():
    model = Model(AIMessage(content=TRANSLATED))
    assert localize_answer(ANSWER, "bn", model) == TRANSLATED
    assert preserves_grounding(ANSWER, TRANSLATED) is True


def test_translation_that_drops_a_number_falls_back():
    lossy = TRANSLATED.replace("১৯.৯৯", "১৯")
    assert localize_answer(ANSWER, "bn", Model(AIMessage(content=lossy))) == ANSWER


def test_translation_that_changes_currency_falls_back():
    lossy = TRANSLATED.replace("৳১৯.৯৯", "১৯.৯৯ ডলার")
    assert localize_answer(ANSWER, "bn", Model(AIMessage(content=lossy))) == ANSWER


def test_translation_that_drops_a_product_id_falls_back():
    lossy = TRANSLATED.replace("SKU_101", "পণ্য")
    assert localize_answer(ANSWER, "bn", Model(AIMessage(content=lossy))) == ANSWER


def test_translation_that_drops_a_citation_falls_back():
    lossy = TRANSLATED.replace("[product:SKU_101]", "")
    assert localize_answer(ANSWER, "bn", Model(AIMessage(content=lossy))) == ANSWER


def test_translation_without_bengali_script_falls_back():
    assert localize_answer(ANSWER, "bn", Model(AIMessage(content=ANSWER))) == ANSWER


def test_a_model_factory_is_instantiated_instead_of_passed_through():
    reply = AIMessage(content=TRANSLATED)
    calls = []

    def factory():
        calls.append("called")
        return Model(reply)

    assert localize_answer(ANSWER, "bn", factory) == TRANSLATED
    assert calls == ["called"]


def test_successful_translation_records_provider_usage():
    reply = AIMessage(content=TRANSLATED, usage_metadata={
        "input_tokens": 40, "output_tokens": 12, "total_tokens": 52,
    })
    usage = {}
    assert localize_answer(ANSWER, "bn", Model(reply), usage_sink=usage) == TRANSLATED
    assert usage["calls"] == 1
    assert usage["input_tokens"] == 40
    assert usage["output_tokens"] == 12
    assert usage["total_tokens"] == 52
