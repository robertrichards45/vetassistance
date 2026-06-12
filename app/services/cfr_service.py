import json
import os
import re
from typing import Dict, Any, List, Optional


class CFRService:
    """
    Loads CFR rules from a local JSON file now.
    Later: swap load method to pull from an API (and cache locally).
    """

    def __init__(self, base_dir: str, json_rel_path: str = "cfr_data/cfr38_rules.json"):
        self.base_dir = base_dir
        self.json_path = os.path.join(base_dir, json_rel_path)
        self._data: Dict[str, Any] = {}
        self._rules: List[Dict[str, Any]] = []

    def load(self) -> None:
        if not os.path.exists(self.json_path):
            self._data = {"version": "missing", "rules": []}
            self._rules = []
            return

        with open(self.json_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

        self._rules = self._data.get("rules", [])

    @property
    def version(self) -> str:
        return str(self._data.get("version", "unknown"))

    def find_best_match(self, condition_name: str) -> Optional[Dict[str, Any]]:
        """
        Match a scan finding condition name to CFR rule by:
        - direct key match
        - alias match (substring)
        """
        if not condition_name:
            return None

        q = condition_name.strip().lower()

        # direct key
        for r in self._rules:
            if r.get("key", "").lower() == q:
                return r

        # alias substring
        best = None
        best_score = 0
        for r in self._rules:
            aliases = [a.lower() for a in r.get("aliases", [])]
            score = 0
            for a in aliases:
                if a and a in q:
                    score += max(3, len(a))
            if score > best_score:
                best_score = score
                best = r

        # also try reverse containment (finding name in alias)
        if not best:
            for r in self._rules:
                aliases = [a.lower() for a in r.get("aliases", [])]
                score = 0
                for a in aliases:
                    if q and q in a:
                        score += max(3, len(q))
                if score > best_score:
                    best_score = score
                    best = r

        return best


    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        if not self._rules:
            self.load()

        tokens = [t for t in re.split(r"\W+", q) if t]
        scored = []
        for rule in self._rules:
            hay = " ".join([
                str(rule.get("key") or ""),
                str(rule.get("title") or ""),
                str(rule.get("citation") or ""),
                str(rule.get("text") or ""),
            ]).lower()
            overlap = sum(1 for t in tokens if t and t in hay)
            if overlap == 0 and q not in hay:
                continue
            score = overlap + (5 if q in hay else 0)
            scored.append((score, rule))
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, rule in scored[:limit]:
            text = (rule.get("text") or "").strip()
            summary = text[:700] + ("…" if len(text) > 700 else "")
            results.append({
                "key": rule.get("key"),
                "title": rule.get("title") or rule.get("key"),
                "citation": rule.get("citation") or "",
                "summary": summary,
            })
        return results

    def enrich_findings(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Add CFR fields to finding dicts:
        - cfr
        - diagnostic_code
        - rating_criteria
        - evidence_tips
        """
        enriched = []
        for f in findings:
            name = (f.get("condition_name") or f.get("name") or "").strip()
            rule = self.find_best_match(name)

            item = dict(f)
            if rule:
                item["cfr"] = rule.get("cfr")
                item["diagnostic_code"] = rule.get("diagnostic_code")
                item["rating_criteria"] = rule.get("rating_criteria", [])
                item["evidence_tips"] = rule.get("evidence_tips", [])
                item["cfr_title"] = rule.get("title")
                item["cfr_key"] = rule.get("key")
            else:
                item["cfr"] = None
                item["diagnostic_code"] = None
                item["rating_criteria"] = []
                item["evidence_tips"] = []
                item["cfr_title"] = None
                item["cfr_key"] = None

            enriched.append(item)

        return enriched
