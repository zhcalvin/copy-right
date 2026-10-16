"""What a render would cost before it runs (docs/PLAN.md M6 "费用估算").

Walks the storyboard the way :mod:`app.pipeline.render` would: which provider calls each
treatment makes, how many seconds, images or characters, and which shots an earlier
render already made (those are free). Prices are the registry's list prices; a call
whose model has no price is counted as unknown rather than guessed.
"""

import math
from typing import Literal

from pydantic import BaseModel
from sqlmodel import Session

from app.capabilities.base import Capability
from app.models import Render
from app.pipeline import render as pipeline
from app.pipeline import render_plan as plan
from app.providers.registry import Price, Registry, get_registry
from app.providers.settings import CapabilityUnavailable, selection_for
from app.storyboard.schema import Shot, Treatment
from app.storyboard.versions import latest_doc

Quality = Literal["preview", "final"]
FRAME_STEPS = (Treatment.MOTION, Treatment.REGENERATE, Treatment.NEW)


class CostPart(BaseModel):
    capability: Capability
    provider: str
    model: str | None
    quantity: float
    unit: str
    amount: float | None  # None: the model has no price in the registry
    currency: str | None = None


class ShotCost(BaseModel):
    shot_id: str
    treatment: Treatment
    reused: bool
    parts: list[CostPart]


class Estimate(BaseModel):
    currency: str | None
    total: float
    unknown: int  # calls whose price is unknown
    reused: int  # shots an earlier render already made
    shots: list[ShotCost]


def price_of(price: Price | None, quantity: float, quality: Quality) -> float | None:
    if price is None:
        return None
    if price.whole_seconds:
        quantity = math.ceil(quantity)
    quantity = max(quantity, price.min_quantity) * price.factor
    unit_price = price.final if quality == "final" and price.final is not None else price.amount
    if price.unit == "10k_chars":
        return round(quantity / 10_000 * unit_price, 4)
    return round(quantity * unit_price, 4)


class _Pricer:
    def __init__(self, session: Session, registry: Registry, quality: Quality) -> None:
        self.session = session
        self.registry = registry
        self.quality = quality

    def selection(self, capability: Capability) -> tuple[str, str | None] | None:
        try:
            chosen = selection_for(self.session, capability, self.registry)
        except CapabilityUnavailable:
            return None
        return chosen.provider or "", chosen.model

    def part(
        self, capability: Capability, quantity: float, unit: str, model: str | None = None
    ) -> CostPart | None:
        chosen = self.selection(capability)
        if chosen is None:
            return None
        provider, selected = chosen
        model = model or selected
        option = next(
            (
                m
                for m in self.registry.providers[provider].models.get(capability, [])
                if m.id == model
            ),
            None,
        )
        price = option.price if option else None
        return CostPart(
            capability=capability,
            provider=provider,
            model=model,
            quantity=round(quantity, 3),
            unit=unit,
            amount=price_of(price, quantity, self.quality),
            currency=price.currency if price else None,
        )


def _picture_parts(pricer: _Pricer, shot: Shot, seconds: float) -> list[CostPart | None]:
    treatment = shot.edit.treatment
    parts: list[CostPart | None] = []
    if treatment in FRAME_STEPS:
        parts.append(pricer.part(Capability.IMAGE_EDIT, 1, "image"))
    if treatment in (Treatment.SWAP, Treatment.EDIT, Treatment.MOTION):
        chosen = pricer.selection(Capability.VIDEO_EDIT_V2V)
        choice = (
            plan.choose_video_model(pricer.registry, chosen[0], chosen[1], treatment.value)
            if chosen
            else None
        )
        if choice is not None:
            billed = max(seconds, choice.min_seconds or 0)
            parts.append(pricer.part(Capability.VIDEO_EDIT_V2V, billed, "second", choice.model))
    if treatment in (Treatment.REGENERATE, Treatment.NEW):
        parts.append(pricer.part(Capability.IMAGE_TO_VIDEO, seconds, "second"))
    return parts


def _voice_parts(pricer: _Pricer, shot: Shot, seconds: float) -> list[CostPart | None]:
    text = " ".join(line.text for line in shot.narrative.dialogue).strip()
    if not text:
        return []
    return [
        pricer.part(Capability.TTS, len(text), "character"),
        pricer.part(Capability.LIPSYNC, seconds, "second"),
    ]


def estimate(
    session: Session, project_id: str, quality: Quality, registry: Registry | None = None
) -> Estimate:
    reg = registry or get_registry()
    doc = latest_doc(session, project_id)
    draft = Render(project_id=project_id, storyboard_version=doc.version, quality=quality)
    ctx = pipeline._Context(session, draft, reg)
    pricer = _Pricer(session, reg, quality)
    shots: list[ShotCost] = []
    for shot in doc.shots:
        treatment = shot.edit.treatment
        voiced = shot.edit.voice_changed and treatment != Treatment.REMOVED
        if treatment not in pipeline.PAID and not voiced:
            continue
        picture_sig, voice_sig = pipeline.signatures(ctx, shot)
        earlier = [e for e in ctx.earlier(shot.shot_id) if e.signature == picture_sig and e.picture]
        reused_picture = bool(earlier)
        reused_voice = any(e.voice_signature == voice_sig and e.audio for e in earlier)
        seconds = pipeline._shot_duration(shot)
        parts = (
            []
            if reused_picture or treatment not in pipeline.PAID
            else _picture_parts(pricer, shot, seconds)
        )
        if voiced and not reused_voice:
            parts += _voice_parts(pricer, shot, seconds)
        shots.append(
            ShotCost(
                shot_id=shot.shot_id,
                treatment=treatment,
                reused=reused_picture and (not voiced or reused_voice),
                parts=[p for p in parts if p is not None],
            )
        )
    priced = [p for s in shots for p in s.parts if p.amount is not None]
    currencies = {p.currency for p in priced}
    return Estimate(
        currency=currencies.pop() if len(currencies) == 1 else None,
        total=round(sum(p.amount or 0 for p in priced), 2),
        unknown=sum(1 for s in shots for p in s.parts if p.amount is None),
        reused=sum(1 for s in shots if s.reused),
        shots=shots,
    )
