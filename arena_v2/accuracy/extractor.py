#!/usr/bin/env python3
"""StructuredExtractor — parse generator output for structured predictions.

Instead of fragile regex, uses section-based parsing from the generator's
output format (which includes structured stance/entry/stop/target sections).

The generator's analysis output follows a Fidelity-report format with:
  - ## Verdict / ## Thesis sections
  - Entry zone / Stop-loss / Price target references
  - Confidence expressed numerically or qualitatively

This parser extracts these structured fields reliably.
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("arena.accuracy.extractor")

# ─── Price extraction patterns ──────────────────────────────────────────────

_PRICE_PATTERN = re.compile(r"\$?(\d+[,.]?\d*(?:\.\d+)?)")

# Section markers in generator output
_ENTRY_SECTIONS = re.compile(
    r"(?:entry|entry\s+zone|entry\s+range|buy\s+at|buy\s+zone|accumulation\s+zone)",
    re.IGNORECASE,
)
_STOP_SECTIONS = re.compile(
    r"(?:stop[- ]?loss|stop|exit\s+stop|cut\s+loss|protective\s+stop|hard\s+stop)",
    re.IGNORECASE,
)
_TARGET_SECTIONS = re.compile(
    r"(?:target|price\s+target|profit\s+target|take\s+profit|upside\s+target|objective)",
    re.IGNORECASE,
)
_CONFIDENCE_PATTERN = re.compile(
    r"(?:confidence|conviction|certainty)\s*[:\-]?\s*(\d{1,3}(?:\.\d+)?)\s*%?",
    re.IGNORECASE,
)

# Direction/stance detection
_BULLISH_PATTERN = re.compile(
    r"(?:bullish|strong\s+buy|buy|long|accumulate|overweight|outperform)", re.IGNORECASE
)
_BEARISH_PATTERN = re.compile(
    r"(?:bearish|strong\s+sell|sell|short|avoid|reduce|underweight|underperform)", re.IGNORECASE
)


def _extract_prices(text: str) -> List[float]:
    """Extract all numeric prices from text."""
    return [float(p.replace(",", "")) for p in _PRICE_PATTERN.findall(text)]


def _find_price_after_section(text: str, section_re: re.Pattern) -> List[float]:
    """Find price values mentioned within ~3 lines after a section header."""
    lines = text.split("\n")
    prices = []
    for i, line in enumerate(lines):
        if section_re.search(line):
            # Look at this line + next 3 lines
            context = "\n".join(lines[i : i + 4])
            prices.extend(_extract_prices(context))
    return prices


class StructuredExtractor:
    """Extract structured predictions from generator output.

    The generator produces analysis content with structured sections
    following Fidelity-report standards. This parser extracts:
      - direction (bullish/bearish/neutral)
      - entry_min, entry_max (price range)
      - target price
      - stop-loss price
      - confidence (0-1)
    """

    def extract_from_generator_result(
        self, result: dict
    ) -> Optional[Dict]:
        """Extract structured prediction from a generator result dict.

        The generator result dict has keys:
          ticker, persona, success, content, word_count, error

        Returns None if extraction fails (e.g., content too short).
        """
        if not result.get("success"):
            return None

        content = result.get("content", "")
        if not content or len(content) < 100:
            return None

        ticker = result.get("ticker", "?")
        persona = result.get("persona", "?")

        return self.extract(content, ticker, persona)

    def extract(
        self, content: str, ticker: str, persona: str
    ) -> Optional[Dict]:
        """Extract structured prediction from raw analysis content."""
        direction = self._detect_direction(content)
        entry_min, entry_max = self._extract_entry(content)
        target = self._extract_target(content)
        stop = self._extract_stop(content)
        confidence = self._extract_confidence(content, direction)

        return {
            "ticker": ticker,
            "persona": persona,
            "direction": direction,
            "entry_min": entry_min,
            "entry_max": entry_max,
            "target": target,
            "stop": stop,
            "confidence": round(confidence, 4),
        }

    def _detect_direction(self, content: str) -> str:
        """Detect bullish/bearish/neutral from content.

        Uses section header analysis (## Verdict, ## Thesis) plus
        weighted keyword scoring for robustness.
        """
        lower = content.lower()

        # Check verdict section specifically
        verdict_section = ""
        v_match = re.split(r"##\s*verdict", lower, maxsplit=1, flags=re.IGNORECASE)
        if len(v_match) > 1:
            # Take next ~500 chars after ## Verdict
            verdict_section = v_match[1][:500]

        # Score based on verdict section (weighted higher)
        bull_score = 0
        bear_score = 0

        if verdict_section:
            bull_score += sum(2 for w in _BULLISH_PATTERN.findall(verdict_section))
            bear_score += sum(2 for w in _BEARISH_PATTERN.findall(verdict_section))

        # Full content scoring
        bull_score += sum(1 for w in _BULLISH_PATTERN.findall(lower))
        bear_score += sum(1 for w in _BEARISH_PATTERN.findall(lower))

        if bull_score > bear_score + 1:
            return "bullish"
        elif bear_score > bull_score + 1:
            return "bearish"
        return "neutral"

    def _extract_entry(self, content: str) -> Tuple[Optional[float], Optional[float]]:
        """Extract entry price range from content.

        Looks for patterns like:
          "Entry: $45-$48"
          "Entry Zone: $45 to $48"
          "Buy between $45 and $48"
          "Entry: $46.50"
        """
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if _ENTRY_SECTIONS.search(line):
                context = "\n".join(lines[max(0, i - 1) : i + 3])

                # Prefer explicit range patterns first
                range_patterns = [
                    r"(?:between|from)\s*\$?(\d+[,.]?\d*)\s*(?:to|-|–|and)\s*\$?(\d+[,.]?\d*)",
                    r"(?:entry|buy)\s*(?:zone|range|price)?\s*[:\-]?\s*\$?(\d+[,.]?\d*)\s*(?:to|-|–)\s*\$?(\d+[,.]?\d*)",
                    r"\$?(\d+[,.]?\d*)\s*(?:to|-|–)\s*\$?(\d+[,.]?\d*)",
                ]
                for pat in range_patterns:
                    m = re.search(pat, context, re.IGNORECASE)
                    if m:
                        p1 = float(m.group(1).replace(",", ""))
                        p2 = float(m.group(2).replace(",", ""))
                        if p1 > 0 and p2 > 0 and p1 != p2:
                            return (min(p1, p2), max(p1, p2))

                # Single price entry
                single = re.search(
                    r"(?:entry|buy)\s*(?:price|point|at)?\s*[:\-]?\s*\$?(\d+[,.]?\d*(?:\.\d+)?)",
                    context, re.IGNORECASE,
                )
                if single:
                    p = float(single.group(1).replace(",", ""))
                    return (p * 0.98, p * 1.02)

        return (None, None)

    def _extract_target(self, content: str) -> Optional[float]:
        """Extract target price from content."""
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if _TARGET_SECTIONS.search(line):
                context = "\n".join(lines[i : i + 3])
                prices = _extract_prices(context)
                if prices:
                    # Target is typically the highest price in the context
                    # (or check for "target: $X" specifically)
                    target_match = re.search(
                        r"(?:target|objective)\s*[:\-]?\s*\$?(\d+[,.]?\d*(?:\.\d+)?)",
                        context, re.IGNORECASE,
                    )
                    if target_match:
                        return float(target_match.group(1).replace(",", ""))
                    # Return highest price in context as target
                    return max(prices)
        return None

    def _extract_stop(self, content: str) -> Optional[float]:
        """Extract stop-loss price from content."""
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if _STOP_SECTIONS.search(line):
                context = "\n".join(lines[i : i + 3])
                prices = _extract_prices(context)
                if prices:
                    # Stop is typically the lowest price in the context
                    stop_match = re.search(
                        r"(?:stop|stop[- ]?loss|cut)\s*(?:loss\s*)?(?:at\s*)?[:\-]?\s*\$?(\d+[,.]?\d*(?:\.\d+)?)",
                        context, re.IGNORECASE,
                    )
                    if stop_match:
                        return float(stop_match.group(1).replace(",", ""))
                    return min(prices)
        return None

    def _extract_confidence(self, content: str, direction: str) -> float:
        """Extract numerical confidence from content.

        Returns 0-1 value. Default 0.5 for neutral, 0.6 for directional signals.
        """
        # Try explicit confidence percentage
        conf_matches = _CONFIDENCE_PATTERN.findall(content)
        if conf_matches:
            val = float(conf_matches[0])
            if val > 1:
                val /= 100.0
            return max(0.0, min(1.0, val))

        # Try qualitative markers
        lower = content.lower()
        high_confidence = ["high confidence", "strong conviction", "very confident",
                          "high conviction", "strongly believe"]
        medium_confidence = ["moderate confidence", "fairly confident", "likely",
                            "reasonably confident", "expect"]
        low_confidence = ["low confidence", "uncertain", "speculative", "risky",
                         "unclear", "not confident"]

        for word in high_confidence:
            if word in lower:
                return 0.8
        for word in medium_confidence:
            if word in lower:
                return 0.6
        for word in low_confidence:
            if word in lower:
                return 0.4

        # Default by direction
        if direction == "bullish" or direction == "bearish":
            return 0.6
        return 0.5
