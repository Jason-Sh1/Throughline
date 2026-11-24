"""LLM judge for the eval suite (not part of the pipeline).

It grades the card *before* verification, using the same sentence numbering as
the verifier, so one judge call gives both numbers: how faithful the final card
is (sentences verification kept) and how good verification was at removing the
bad ones (sentences it dropped).

The judge is a stronger model than the one being graded (default Claude Sonnet
5.5 vs Haiku 5.5), so the pipeline isn't marking its own homework. It never
produces gold labels: key facts come from the human-labeled gold set.
"""

from __future__ import annotations

import os
from enum import StrEnum

import anthropic

from evals.pipeline import Spend, call_json
from evals.stories import story_docs
from llm import batch, prompts, verify
from llm.inputs import render_sources
from llm.schemas import Strict

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "low"
MAX_TOKENS = 16000


def judge_model() -> str:
    return os.environ.get("EVAL_JUDGE_MODEL", DEFAULT_MODEL)


def judge_effort() -> str:
    return os.environ.get("EVAL_JUDGE_EFFORT", DEFAULT_EFFORT)


class JudgeVerdict(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


class CitedSupport(Strict):
    source_id: str
    supports: bool


class SentenceGrade(Strict):
    id: str
    verdict: JudgeVerdict
    reason: str
    cited_support: list[CitedSupport]
    uncited_support: list[str]


class FactGrade(Strict):
    id: str
    covered_by: list[str]


class JudgeResult(Strict):
    sentences: list[SentenceGrade]
    key_facts: list[FactGrade]


def user_message(story: dict, pre_card: dict, key_facts: list[str]) -> str:
    docs = story_docs(story)
    its = verify.items(pre_card)
    identity = {d.local_id: d.local_id for d in docs}
    facts = "\n".join(f"[K{i}] {f}" for i, f in enumerate(key_facts, 1)) or "(none)"
    return (
        f"<sources>\n{render_sources(docs)}\n</sources>\n\n"
        f"<sentences>\n{verify.render_sentences(its, identity)}\n</sentences>\n\n"
        f"<key_facts>\n{facts}\n</key_facts>\n\n"
        "Grade every sentence and every key fact."
    )


def parse(text: str, n_sentences: int) -> JudgeResult:
    try:
        res = JudgeResult.model_validate_json(text)
    except ValueError as e:
        raise ValueError(f"invalid judge JSON: {e}") from e
    if len({g.id for g in res.sentences}) < n_sentences:
        raise ValueError(f"judge graded {len(res.sentences)} of {n_sentences} sentences")
    return res


def judge(client, story: dict, pre_card: dict, key_facts: list[str]) -> dict:
    system, _ = prompts.load(prompts.JUDGE)
    schema = anthropic.transform_schema(JudgeResult)
    params = batch.params(system, user_message(story, pre_card, key_facts), schema,
                          judge_model(), judge_effort(), MAX_TOKENS)  # fmt: skip
    spend = Spend()
    n = len(verify.items(pre_card))
    res = call_json(client, params, lambda t: parse(t, n), spend)
    return {"result": res.model_dump(mode="json"), "spend": spend.as_dict()}
