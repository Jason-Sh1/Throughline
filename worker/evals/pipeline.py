"""Run the production pipeline on one frozen story, without the database.

Same prompts, schemas, citation filter and verification as an edition, but each
step is a normal (synchronous) request instead of a batch, so an eval finishes in
minutes. Spend is reported at batch prices too, since that's what an edition pays.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable

from evals.stories import story_docs
from llm import extract, synthesize, verify
from llm.batch import message_text, usage_cost
from llm.citations import CardRejected, filter_card, filter_extraction
from llm.cost import cost_usd
from llm.inputs import render_sources
from llm.schemas import clamp_lengths


class Spend:
    def __init__(self):
        self.input_tokens = self.output_tokens = 0
        self.cost_standard = self.cost_batch = 0.0

    def add(self, message, model: str) -> None:
        tin, tout, cost = usage_cost(message, model, "standard")
        self.input_tokens += tin
        self.output_tokens += tout
        self.cost_standard += cost
        u = message.usage
        self.cost_batch += cost_usd(model, "batch", u.input_tokens, u.output_tokens,
                                    u.cache_read_input_tokens or 0,
                                    u.cache_creation_input_tokens or 0)  # fmt: skip

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost_standard, 6),
                "cost_usd_batch": round(self.cost_batch, 6)}  # fmt: skip


def call_json[T](client, params: dict, parse: Callable[[str], T], spend: Spend) -> T:
    """One request; on invalid output, one retry (CLAUDE.md), then ValueError."""
    err: Exception | None = None
    for _ in range(2):
        msg = client.messages.create(**params)
        spend.add(msg, params["model"])
        try:
            return parse(message_text(msg))
        except ValueError as e:
            err = e
    raise ValueError(f"after retry: {err}")


def models() -> dict:
    """Everything that changes the output: part of the eval cache key."""
    return {
        "extract": extract.extract_model(),
        "synthesize": synthesize.synth_model(),
        "synthesize_effort": synthesize.synth_effort(),
        "verify": verify.verify_model(),
        "verify_effort": verify.verify_effort(),
    }


def run_story(client, story: dict) -> dict:
    """Returns the card before and after verification plus what verification kept.
    `card` is None when the card failed (citation filter or verification)."""
    m = models()
    spend = Spend()
    t0 = time.monotonic()
    docs = story_docs(story)
    valid = {d.local_id for d in docs}
    out: dict = {"story_id": story["id"], "error": None, "pre_card": None, "card": None,
                 "kept": [], "verification": None, "transient": False}  # fmt: skip
    try:
        user = f"<sources>\n{render_sources(docs)}\n</sources>"
        ex = call_json(client, extract.request_params(user, m["extract"]),
                       extract.parse_extraction, spend)  # fmt: skip
        ex, _ = filter_extraction(ex, valid)

        params = synthesize.request_params(synthesize.user_message(docs, ex), m["synthesize"],
                                           m["synthesize_effort"])  # fmt: skip
        draft = call_json(client, params, synthesize.parse_draft, spend)
        draft, _ = filter_card(clamp_lengths(draft), valid)
        pre = draft.model_dump(mode="json")
        out["pre_card"] = pre

        card = copy.deepcopy(pre)
        its = verify.items(card)
        identity = {i: i for i in valid}
        # Same as production: the verifier only sees sources some sentence cites.
        cited = verify.cited_docs(docs, its)
        vparams = verify.request_params(verify.user_message(cited, its, identity), m["verify"],
                                        m["verify_effort"])  # fmt: skip
        res = call_json(client, vparams, lambda t: verify.parse_result(t, len(its)), spend)
        drop_ids, _ = verify.review(its, res)
        try:
            card, report = verify.apply(card, its, res)
            out["card"] = card
            out["kept"] = [it.key for it in its if id(it.ref) not in drop_ids]
        except verify.CardFailed as e:
            report = e.report
            out["error"] = f"verification: {e}"
        out["verification"] = report
    except CardRejected as e:
        out["error"] = f"citation filter: {e}"
    except ValueError as e:
        # Invalid JSON after the retry, a refusal or max_tokens: may succeed next time.
        out["error"] = str(e)
        out["transient"] = True
    out["seconds"] = round(time.monotonic() - t0, 2)
    out["spend"] = spend.as_dict()
    return out
