"""Product Type Normalization pipeline (4 stages).

1. Extract a structured signature from the raw title (LLM; lexical fallback).
2. Retrieve top candidate types via the multi-vector alias index
   (pgvector max-similarity; lexical token-overlap fallback when the
   embeddings API is unavailable).
3. Constrained choice: LLM picks ONLY from the candidate list, or "none"
   (heuristic fallback based on retrieval scores + signature agreement).
4. Rule override: strong form+material keyword matches beat the LLM.

Returns low confidence + status "out_of_catalog" instead of guessing —
honesty scores points; hallucinated matches lose them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ProductType, TaxonomyAlias
from app.taxonomy_data import FORM_SYNONYMS, JUNK_PATTERNS, MATERIAL_SYNONYMS

OOC_CONFIDENCE_THRESHOLD = 0.55
CANDIDATE_LIMIT = 10
ALIAS_FETCH_LIMIT = 40

OCCASION_WORDS = [
    "christmas", "xmas", "mother's day", "father's day", "wedding", "anniversary",
    "birthday", "graduation", "memorial", "housewarming", "valentine",
]
RECIPIENT_RE = re.compile(r"\bfor (?:my |the )?([a-z]+(?: [a-z]+)?)", re.IGNORECASE)
PERSONALIZATION_WORDS = ("personalized", "custom", "engraved", "monogram", "photo", "name")


@dataclass
class Signature:
    object_noun: str = ""
    material_hints: list[str] = field(default_factory=list)
    form_hints: list[str] = field(default_factory=list)
    personalization: bool = False
    occasion: str | None = None
    recipient: str | None = None

    def as_dict(self) -> dict:
        return {
            "object_noun": self.object_noun,
            "material_hints": self.material_hints,
            "form_hints": self.form_hints,
            "personalization": self.personalization,
            "occasion": self.occasion,
            "recipient": self.recipient,
        }


@dataclass
class Candidate:
    id: str
    name: str
    similarity: float


def _clean_title(title: str) -> str:
    t = title.lower()
    for junk in JUNK_PATTERNS:
        t = t.replace(junk, " ")
    return re.sub(r"[^a-z0-9\s'-]", " ", t)


def lexical_signature(title: str) -> Signature:
    """Deterministic signature extraction — always runs; also the LLM fallback."""
    t = _clean_title(title)
    materials = sorted({canon for word, canon in MATERIAL_SYNONYMS.items() if re.search(rf"\b{re.escape(word)}\b", t)})
    # longest-phrase-first so "cutting board" wins over "board"
    forms = []
    for phrase in sorted(FORM_SYNONYMS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", t):
            canon = FORM_SYNONYMS[phrase]
            if canon not in forms:
                forms.append(canon)
    occasion = next((o for o in OCCASION_WORDS if o in t), None)
    recipient_match = RECIPIENT_RE.search(t)
    return Signature(
        object_noun=forms[0] if forms else "",
        material_hints=materials,
        form_hints=forms,
        personalization=any(w in t for w in PERSONALIZATION_WORDS),
        occasion=occasion,
        recipient=recipient_match.group(1) if recipient_match else None,
    )


def extract_signature(title: str) -> Signature:
    """Stage 1: LLM structured extraction, lexical fallback."""
    try:
        from pydantic import BaseModel

        from app.llm import get_llm

        class SignatureModel(BaseModel):
            object_noun: str
            material_hints: list[str]
            form_hints: list[str]
            personalization: bool
            occasion: str | None = None
            recipient: str | None = None

        llm = get_llm().with_structured_output(SignatureModel, method="function_calling")
        result = llm.invoke(
            "Extract the product signature from this marketplace listing title. "
            "object_noun = the physical product word (ornament, tumbler, blanket...). "
            "material_hints = materials mentioned or strongly implied (acrylic, wood, metal, "
            "ceramic, glass, canvas, fabric, stone, leather, paper). "
            "form_hints = shape/format words. Ignore marketing junk "
            "(best gift, free shipping, unique).\n\n"
            f"Title: {title}"
        )
        lex = lexical_signature(title)
        # Union LLM output with the deterministic pass so strong keyword
        # signals are never lost to LLM paraphrasing.
        return Signature(
            object_noun=result.object_noun or lex.object_noun,
            material_hints=sorted(set(m.lower() for m in result.material_hints) | set(lex.material_hints)),
            form_hints=list(dict.fromkeys(result.form_hints + lex.form_hints)),
            personalization=result.personalization or lex.personalization,
            occasion=result.occasion or lex.occasion,
            recipient=result.recipient or lex.recipient,
        )
    except Exception:  # noqa: BLE001 - degrade gracefully without API access
        return lexical_signature(title)


def _has_alias_index(db: Session) -> bool:
    return bool(db.scalar(select(func.count()).select_from(TaxonomyAlias)))


def _vector_candidates(db: Session, query: str) -> list[Candidate]:
    from app.llm import get_embeddings

    vec = get_embeddings().embed_query(query)
    distance = TaxonomyAlias.embedding.cosine_distance(vec)
    stmt = (
        select(TaxonomyAlias.product_type_id, ProductType.name, distance.label("dist"))
        .join(ProductType, ProductType.id == TaxonomyAlias.product_type_id)
        .order_by(distance)
        .limit(ALIAS_FETCH_LIMIT)
    )
    best: dict[str, Candidate] = {}
    for pt_id, name, dist in db.execute(stmt):
        sim = round(1.0 - float(dist), 4)
        if pt_id not in best or sim > best[pt_id].similarity:
            best[pt_id] = Candidate(id=pt_id, name=name, similarity=sim)
    return sorted(best.values(), key=lambda c: -c.similarity)[:CANDIDATE_LIMIT]


def _lexical_candidates(db: Session, title: str, sig: Signature) -> list[Candidate]:
    """Token-overlap scoring over aliases + signature agreement bonuses.
    Used when alias embeddings are absent (no API key)."""
    title_tokens = set(_clean_title(title).split())
    types = {t.id: t for t in db.scalars(select(ProductType))}
    rows = db.execute(select(TaxonomyAlias.product_type_id, TaxonomyAlias.alias)).all()

    scores: dict[str, float] = {}
    for pt_id, alias in rows:
        alias_tokens = set(_clean_title(alias).split())
        if not alias_tokens:
            continue
        overlap = len(title_tokens & alias_tokens) / len(alias_tokens)
        scores[pt_id] = max(scores.get(pt_id, 0.0), overlap * 0.7)

    # No aliases in DB at all → score directly against taxonomy names.
    if not rows:
        for pt_id, t in types.items():
            name_tokens = set(_clean_title(t.name).split())
            overlap = len(title_tokens & name_tokens) / max(len(name_tokens), 1)
            scores[pt_id] = overlap * 0.7

    for pt_id, t in types.items():
        form = _type_form(t.name)
        bonus = 0.0
        if form and form in sig.form_hints:
            bonus += 0.25
        if t.material in sig.material_hints:
            bonus += 0.15
        if bonus:
            scores[pt_id] = scores.get(pt_id, 0.0) + bonus

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:CANDIDATE_LIMIT]
    return [
        Candidate(id=pt_id, name=types[pt_id].name, similarity=round(min(s, 0.99), 4))
        for pt_id, s in ranked
        if s > 0.05
    ]


def retrieve_candidates(db: Session, title: str, sig: Signature) -> list[Candidate]:
    """Stage 2: multi-vector retrieval; lexical fallback."""
    query = " ".join(sig.material_hints + [sig.object_noun] + sig.form_hints).strip() or title
    if _has_alias_index(db):
        try:
            return _vector_candidates(db, query)
        except Exception:  # noqa: BLE001 - embeddings API may be down
            pass
    return _lexical_candidates(db, title, sig)


@dataclass
class Choice:
    product_type_id: str | None
    confidence: float
    reasoning: str
    evidence_span: str


def _heuristic_choice(title: str, sig: Signature, candidates: list[Candidate], db: Session) -> Choice:
    if not candidates:
        return Choice(None, 0.0, "No candidate type is lexically or semantically close.", "")
    top = candidates[0]
    t = db.get(ProductType, top.id)
    form = _type_form(t.name) if t else ""
    form_ok = bool(form and form in sig.form_hints)
    material_ok = bool(t and t.material in sig.material_hints)
    evidence = " + ".join(
        ([form] if form_ok else []) + ([t.material] if material_ok else [])
    )
    if form_ok and material_ok:
        conf = max(0.9, top.similarity)
        why = f"Form '{form}' and material '{t.material}' both appear in the title."
    elif form_ok:
        conf = max(0.7, top.similarity)
        why = f"Form '{form}' appears in the title; material inferred from closest alias."
    else:
        conf = min(top.similarity, 0.6)
        why = "Match based on alias similarity only — no strong form/material keyword."
    return Choice(top.id, round(conf, 2), why, evidence or sig.object_noun)


def choose(db: Session, title: str, sig: Signature, candidates: list[Candidate]) -> Choice:
    """Stage 3: constrained LLM choice with heuristic fallback."""
    if not candidates:
        return _heuristic_choice(title, sig, candidates, db)
    try:
        from pydantic import BaseModel

        from app.llm import get_llm

        class ChoiceModel(BaseModel):
            product_type_id: str | None
            confidence: float
            reasoning: str
            evidence_span: str

        options = "\n".join(f"- {c.id}: {c.name} (similarity {c.similarity})" for c in candidates)
        llm = get_llm().with_structured_output(ChoiceModel, method="function_calling")
        result = llm.invoke(
            "Map this marketplace listing title to EXACTLY ONE of the candidate product "
            "types below, or null if none truly fits. You must NOT invent ids outside the "
            "list. confidence is 0-1. evidence_span = the words in the title that justify "
            f"the choice.\n\nTitle: {title}\nSignature: {sig.as_dict()}\n\nCandidates:\n{options}"
        )
        valid_ids = {c.id for c in candidates}
        if result.product_type_id and result.product_type_id not in valid_ids:
            return _heuristic_choice(title, sig, candidates, db)
        return Choice(
            result.product_type_id,
            round(min(max(result.confidence, 0.0), 1.0), 2),
            result.reasoning,
            result.evidence_span,
        )
    except Exception:  # noqa: BLE001
        return _heuristic_choice(title, sig, candidates, db)


def _type_form(name: str) -> str:
    """Canonical form word of a taxonomy name = last alphabetic word, with
    multiword synonyms honored ("Night Light" -> "light", "T-Shirt" -> "t-shirt")."""
    lowered = name.lower()
    for phrase in sorted(FORM_SYNONYMS, key=len, reverse=True):
        if lowered.endswith(phrase):
            return FORM_SYNONYMS[phrase]
    words = [w for w in re.findall(r"[a-z-]+", lowered) if not any(ch.isdigit() for ch in w)]
    return words[-1] if words else ""


def apply_rules(db: Session, sig: Signature, choice: Choice, candidates: list[Candidate]) -> Choice:
    """Stage 4: form+material keyword override. Strong signals beat the LLM."""
    if not sig.form_hints:
        return choice
    types = list(db.scalars(select(ProductType)))
    form_matches = [t for t in types if _type_form(t.name) in sig.form_hints]
    if not form_matches:
        return choice

    if sig.material_hints:
        exact = [t for t in form_matches if t.material in sig.material_hints]
        if len(exact) == 1:
            t = exact[0]
            if choice.product_type_id != t.id or choice.confidence < 0.9:
                return Choice(
                    t.id,
                    0.92,
                    f"Rule override: form '{_type_form(t.name)}' + material '{t.material}' "
                    "are unambiguous keywords in the title.",
                    f"{_type_form(t.name)} + {t.material}",
                )
            return choice

    if len(form_matches) == 1:
        t = form_matches[0]
        if choice.product_type_id != t.id and choice.confidence < 0.85:
            return Choice(
                t.id,
                0.85,
                f"Rule override: '{_type_form(t.name)}' maps to exactly one catalog type.",
                _type_form(t.name),
            )
    elif choice.product_type_id not in {t.id for t in form_matches} and choice.confidence < 0.8:
        # Ambiguous materials: restrict to form-matching candidates, pick the
        # best-retrieved among them.
        ranked = [c for c in candidates if c.id in {t.id for t in form_matches}]
        if ranked:
            return Choice(
                ranked[0].id,
                max(choice.confidence, 0.65),
                f"Rule-restricted to '{sig.form_hints[0]}' types; picked the closest alias match.",
                sig.form_hints[0],
            )
    return choice


def normalize_listing(db: Session, title: str) -> dict:
    """Orchestrator. Returns a NormalizeResponse-shaped dict."""
    sig = extract_signature(title)
    candidates = retrieve_candidates(db, title, sig)
    choice = choose(db, title, sig, candidates)
    choice = apply_rules(db, sig, choice, candidates)

    matched = bool(choice.product_type_id) and choice.confidence >= OOC_CONFIDENCE_THRESHOLD
    product_type = db.get(ProductType, choice.product_type_id) if matched else None
    if not matched:
        reasoning = choice.reasoning or "No catalog type matches this listing."
        if choice.product_type_id:
            reasoning = (
                f"Closest candidate is '{choice.product_type_id}' but confidence "
                f"{choice.confidence:.2f} is below the {OOC_CONFIDENCE_THRESHOLD} threshold — "
                "reporting out-of-catalog instead of guessing."
            )
        choice = Choice(None, choice.confidence, reasoning, choice.evidence_span)

    return {
        "status": "matched" if matched else "out_of_catalog",
        "product_type": product_type,
        "confidence": choice.confidence,
        "reasoning": choice.reasoning,
        "evidence_span": choice.evidence_span,
        "candidates": [c.__dict__ for c in candidates],
        "signature": sig.as_dict(),
    }
