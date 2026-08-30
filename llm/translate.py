"""
Entitlement translation.

`PYX_DISPENSE_CTRL_SUB` means nothing to the ward manager asked to attest to
it, and a reviewer who cannot read an entitlement approves it. Translation is
the one place a language model is unambiguously the right tool: the input is a
code and some metadata, the output is a sentence, and the sentence can be
checked against the metadata that produced it.

Every translation is validated before use and rejected if it misstates the
application or contradicts the catalogue's risk rating. Rejected translations
fall back to a deterministic template, which is duller but cannot be wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .client import LLMClient
from .validate import validate_translation, extract_json

SYSTEM = """You translate access-control entitlement codes into plain English \
for hospital managers reviewing who has access to what.

Rules:
- Describe what the access permits. Do not judge whether anyone should hold it.
- Name only the application you are told the entitlement belongs to.
- Do not characterise the risk level; it is supplied separately.
- One sentence, under 25 words, no jargon, no restating the code.
- Return JSON only: {"code": "...", "description": "..."}"""


@dataclass
class Translation:
    code: str
    description: str
    source: str            # "model" | "template" | "rejected"
    failures: list


def template(code: str, meta: dict) -> str:
    """A deterministic rendering, used when the model is absent or wrong.

    Readable rather than good. Its purpose is to guarantee that a reviewer
    always sees something truthful, not to compete with the model.
    """
    words = code.replace("_", " ").lower().split()
    skip = {"grp", "ent", "cern", "sec", "ora", "pyx", "lis", "pacs"}
    body = " ".join(w for w in words if w not in skip) or code.lower()
    return f"{meta['app']} access: {body} ({meta['risk'].lower()} risk)"


def translate_one(client: LLMClient, code: str, meta: dict) -> Translation:
    if not client.available:
        return Translation(code, template(code, meta), "template", [])

    prompt = (
        f"Entitlement code: {code}\n"
        f"Application: {meta['app']}\n"
        f"Layer: {meta['layer']}\n\n"
        f"Translate this into one plain sentence for a hospital manager."
    )
    r = client.complete(prompt, system=SYSTEM, max_tokens=200)
    if not r.ok:
        return Translation(code, template(code, meta), "template",
                           [r.error or "model call failed"])

    data = extract_json(r.text)
    text = (data or {}).get("description", "") if isinstance(data, dict) else ""
    if not text:
        text = r.text.strip()

    v = validate_translation(code, meta, text)
    if not v.ok:
        return Translation(code, template(code, meta), "rejected", v.failures)
    return Translation(code, text.strip(), "model", v.warnings)


def translate_catalogue(client: LLMClient, catalogue: dict, *,
                        only_risky: bool = True) -> dict:
    """Translate the catalogue, highest risk first.

    By default only elevated-risk entitlements are sent to the model. The
    low-risk directory groups are numerous, uninteresting, and translate
    perfectly well from a template — spending model calls on them adds cost
    and latency without adding anything a reviewer needs.
    """
    items = catalogue.items()
    if only_risky:
        items = [(k, m) for k, m in items if m["risk"] in ("CRITICAL", "HIGH")]

    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    items = sorted(items, key=lambda kv: order.get(kv[1]["risk"], 9))

    out = {}
    for code, meta in items:
        t = translate_one(client, code, meta)
        out[code] = {
            "description": t.description,
            "application": meta["app"],
            "risk": meta["risk"],
            "aggregated_by_iga": meta["aggregated_by_iga"],
            "source": t.source,
            "notes": t.failures,
        }
    return out
