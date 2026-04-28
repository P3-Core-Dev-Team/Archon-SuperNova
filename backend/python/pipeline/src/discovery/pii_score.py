"""
pii_score.py — Bayesian column score and span-overlap resolution.

Two responsibilities:

1. **Bayesian column score**

   ``column_pii_confidence(π_name, π_match, π_validate)`` combines three
   independent signals into ``P(column = PII_type)``::

       score = 1 - (1 - π_name) · (1 - π_match) · (1 - π_validate)

   * π_name     — column-name-prior weight (0.85 exact / 0.50 substring / 0)
   * π_match    — saturating regex match rate, ``min(1, rate / 0.30)``
   * π_validate — checksum/validator pass ratio,
                  ``validated_count / max(match_count, 1)``

   ``score >= 0.85`` is "confident", ``>= 0.50`` is "report".

2. **Span-overlap resolution**

   When multiple patterns match the same span (e.g. a 16-digit Luhn-valid
   credit card also matches the 10-digit ``PHONE_US`` regex on a substring),
   :func:`resolve_overlaps` picks the more *specific* pattern that **also**
   passed validation.  This is the direct fix for the ``card_number_raw``
   PHONE_US/CC_NUMBER collision documented in the upgrade plan.

These functions are pure (no I/O, no module-level state) so they can be
unit-tested without database fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from discovery.pii_patterns import SPECIFICITY


# ---------------------------------------------------------------------------
# Bayesian score
# ---------------------------------------------------------------------------


def _saturate_match_rate(regex_match_rate: float, saturation: float = 0.30) -> float:
    """Saturating π_match: rate ≥ ``saturation`` → 1.0, else linear."""
    if regex_match_rate <= 0.0:
        return 0.0
    return min(1.0, regex_match_rate / saturation)


def column_pii_confidence(
    name_prior_strength: float,
    regex_match_rate: float,
    validator_pass_rate: float,
    *,
    negative_prior: bool = False,
    negative_prior_dampen: float = 0.2,
    free_text_column: bool = False,
    free_text_dampen: float = 0.4,
) -> float:
    """Combine three signals into a single confidence score in ``[0, 1]``.

    Parameters
    ----------
    name_prior_strength
        π_name — how strongly the column name implies the type.  Use
        :func:`pii_priors.name_prior_strength`.
    regex_match_rate
        Fraction of scanned rows that produced *any* regex match for the
        candidate type.  This is the **raw** ratio; the function applies the
        saturating transform internally.
    validator_pass_rate
        Among rows that matched the regex, the fraction whose validator
        accepted them.  ``validated_count / max(match_count, 1)``.
    negative_prior
        When True, multiply the final combined score by ``negative_prior_dampen``
        (default 0.2) — used when the column name contains a known
        false-positive trigger token for this pii_type
        (e.g. ``phone`` column lighting up the bare-digit PESEL_PL regex).
    negative_prior_dampen
        Multiplier applied when ``negative_prior`` is True.  Default 0.2 keeps
        the score non-zero (so ``address.phone`` could still surface a real
        PESEL with high regex evidence) but knocks it well below threshold.
    free_text_column
        When True, the column name implies free-form prose
        (``description`` / ``comments`` / ``notes`` / ``body`` / ``title`` /
        etc. — see :func:`pii_priors.is_free_text_column_name`).  Such
        columns may incidentally contain PII-shaped substrings but the
        column itself is not a PII column structurally.  The dampener
        applies **only when there is no positive name-prior for the
        matched type**, so a column named ``email_notes`` still scores
        cleanly for EMAIL.
    free_text_dampen
        Multiplier applied when ``free_text_column`` triggers (default 0.4).
        A regex-saturating + validator-passing finding lands at
        ``score = 0.4`` instead of ``1.0`` — visible to the operator as
        "advisory" but well below the ``0.85`` confident threshold.

    Returns
    -------
    float
        Bayesian combined confidence, ``1 - (1 - πN)(1 - πM)(1 - πV)``,
        post-multiplied by any active dampeners.
    """
    pi_name = max(0.0, min(1.0, name_prior_strength))
    pi_match = _saturate_match_rate(regex_match_rate)
    pi_validate = max(0.0, min(1.0, validator_pass_rate))
    score = 1.0 - (1.0 - pi_name) * (1.0 - pi_match) * (1.0 - pi_validate)
    if negative_prior:
        dampen = max(0.0, min(1.0, negative_prior_dampen))
        score *= dampen
    # Free-text dampener: only when the column name looks like prose AND
    # there's no positive prior for THIS pii_type.  Skipping the dampen
    # when pi_name > 0 means a deliberate hit like ``email_note`` keeps
    # full confidence.
    if free_text_column and pi_name == 0.0:
        score *= max(0.0, min(1.0, free_text_dampen))
    return score


# ---------------------------------------------------------------------------
# Span-overlap resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    """A single (pattern_name, value, span, validated) tuple.

    The ``span`` is ``(start, end)`` half-open in the source string; the
    overlap resolver compares spans by inclusion / fractional overlap.
    """

    name: str
    value: str
    start: int
    end: int
    validated: bool

    @property
    def length(self) -> int:
        return max(0, self.end - self.start)


def _overlap_fraction(a: Match, b: Match) -> float:
    """Return the overlap of *a* and *b* as a fraction of the shorter span."""
    if a.length == 0 or b.length == 0:
        return 0.0
    overlap = max(0, min(a.end, b.end) - max(a.start, b.start))
    return overlap / min(a.length, b.length)


def _specificity(name: str) -> int:
    """Look up the specificity rank for a pattern name (default 50)."""
    return SPECIFICITY.get(name, 50)


def resolve_overlaps(
    matches: Iterable[Match],
    min_overlap_fraction: float = 0.5,
) -> list[Match]:
    """Resolve overlapping matches; keep the most-specific validated one.

    Algorithm
    ---------
    1. Sort matches by start, then by descending length.
    2. Group matches whose spans overlap by at least ``min_overlap_fraction``
       of the shorter span (50 % default per the plan).
    3. Within a group:
       * If any match has ``validated=True``, keep the one with the highest
         specificity among validated matches.
       * Otherwise, keep the highest-specificity match regardless.
    4. Drop the rest.

    Tie-breaks: longer span wins, then lexicographic name order.
    """
    sorted_matches = sorted(
        matches,
        key=lambda m: (m.start, -(m.end - m.start), m.name),
    )

    out: list[Match] = []

    for m in sorted_matches:
        # Find an existing kept match this one overlaps with.
        merged = False
        for i, prev in enumerate(out):
            if _overlap_fraction(prev, m) < min_overlap_fraction:
                continue
            # Same overlap group — keep the more-specific *validated* match.
            winner = _pick_winner(prev, m)
            out[i] = winner
            merged = True
            break
        if not merged:
            out.append(m)

    return out


def _pick_winner(a: Match, b: Match) -> Match:
    """Choose between two overlapping matches per the resolution rules."""
    # Validator dominance: prefer the validated match.
    if a.validated and not b.validated:
        return a
    if b.validated and not a.validated:
        return b

    # Both validated or both unvalidated: more specific wins.
    s_a, s_b = _specificity(a.name), _specificity(b.name)
    if s_a != s_b:
        return a if s_a > s_b else b

    # Specificity tied: longer span wins.
    if a.length != b.length:
        return a if a.length > b.length else b

    # Final tie-break: lexicographic.
    return a if a.name <= b.name else b
