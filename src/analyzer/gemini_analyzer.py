"""
Gemini Flash AI Analyzer for Fauntleroy-Vashon School Commute.
Leverages Google Gemini Flash (e.g. gemini-3.8-flash) to synthesize
raw vessel AIS telemetry, schedule, bulletins, Friday PDD rules, sports bus schedules,
and weekend lookahead to Monday morning.

Includes state fingerprinting and caching: only invokes Gemini when bulletins,
operating schedule mode (2-boat vs 3-boat), or key conditions change.
"""

import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional

from src.config import DEFAULT_GEMINI_MODEL, GEMINI_API_KEY
from src.analyzer.heuristic_analyzer import evaluate_commute, get_current_pacific_time

GEMINI_API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
LOCAL_CACHE_PATH = Path("/tmp/gemini_commute_cache.json")
MAX_CACHE_TTL_SECONDS = 4 * 3600  # 4 hours max before refreshing briefing text


def compute_schedule_state_fingerprint(
    heuristic_data: Dict[str, Any],
    bulletins_data: Dict[str, Any]
) -> str:
    """
    Computes a deterministic hash of all bulletin and schedule factors:
    - Active bulletins (IDs, titles, text contents)
    - Schedule operating mode (2-boat vs 3-boat)
    - Target commute day & Friday PDD status
    - Key target sailing statuses
    """
    bulletin_signatures = [
        f"{b.get('bulletin_id', '')}:{b.get('title', '')}:{b.get('content', '')[:100]}"
        for b in bulletins_data.get("triangle_bulletins", [])
    ]
    
    targets = heuristic_data.get("am_commute", {}).get("evaluated_targets", {})
    target_sig = [
        f"{k}:{v.get('status')}:{v.get('delay_minutes', 0)}"
        for k, v in targets.items()
    ]
    
    fingerprint_obj = {
        "schedule_mode": heuristic_data.get("schedule_mode"),
        "bulletin_mode_reason": heuristic_data.get("bulletin_mode_reason"),
        "bulletins": sorted(bulletin_signatures),
        "target_commute_day": heuristic_data.get("target_commute_day"),
        "is_friday_pdd": heuristic_data.get("is_friday_pdd"),
        "targets": sorted(target_sig),
    }
    
    serialized = json.dumps(fingerprint_obj, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _load_disk_cache() -> Optional[Dict[str, Any]]:
    """Loads cached AI briefing from /tmp if available."""
    try:
        if LOCAL_CACHE_PATH.exists():
            with open(LOCAL_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_disk_cache(cache_data: Dict[str, Any]) -> None:
    """Saves AI briefing to /tmp."""
    try:
        LOCAL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCAL_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)
    except Exception as e:
        print(f"[DEBUG] Could not save cache to {LOCAL_CACHE_PATH}: {e}")


def analyze_commute_with_gemini(
    telemetry: Dict[str, Any],
    bulletins_data: Dict[str, Any],
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    simulated_time: Optional[Any] = None,
    cached_ai_state: Optional[Dict[str, Any]] = None,
    force_ai_refresh: bool = False
) -> Dict[str, Any]:
    """
    Runs Gemini Flash on current ferry conditions and VISD commuter rules.
    Only calls the Gemini API if bulletins or schedule modes change, reusing
    cached AI advisory otherwise while keeping vessel telemetry fully real-time.
    """
    key = api_key or os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    model = model_name or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
    
    # 1. Run deterministic baseline calculation
    heuristic_result = evaluate_commute(telemetry, bulletins_data, simulated_time=simulated_time)
    
    # Compute current condition fingerprint
    current_fingerprint = compute_schedule_state_fingerprint(heuristic_result, bulletins_data)
    heuristic_result["state_fingerprint"] = current_fingerprint
    
    if not key:
        heuristic_result["ai_provider"] = "deterministic_heuristic_engine"
        heuristic_result["ai_model"] = "heuristic_v1"
        heuristic_result["ai_cached"] = False
        return heuristic_result

    # 2. Check if cached AI output can be reused (schedule & bulletins unchanged)
    effective_cache = cached_ai_state or _load_disk_cache()
    now_epoch = time.time()
    
    if not force_ai_refresh and effective_cache:
        cached_fingerprint = effective_cache.get("fingerprint")
        cached_time = effective_cache.get("timestamp_epoch", 0)
        cache_age = now_epoch - cached_time
        
        if cached_fingerprint == current_fingerprint and cache_age < MAX_CACHE_TTL_SECONDS:
            print(f"[INFO] Ferry schedule & bulletins unchanged (fingerprint: {current_fingerprint[:8]}). Reusing cached Gemini advisory.")
            heuristic_result["executive_briefing"] = effective_cache.get("executive_briefing", heuristic_result["executive_briefing"])
            heuristic_result["ai_insights"] = effective_cache.get("ai_insights", [])
            heuristic_result["ai_parent_action_items"] = effective_cache.get("ai_parent_action_items", [])
            heuristic_result["ai_provider"] = "google_gemini (cached)"
            heuristic_result["ai_model"] = effective_cache.get("ai_model", model)
            heuristic_result["ai_cached"] = True
            heuristic_result["ai_cache_age_minutes"] = round(cache_age / 60.0, 1)
            return heuristic_result

    # 3. If fingerprint changed or cache expired, call Gemini Flash
    print(f"[INFO] State changed or cache expired (fingerprint: {current_fingerprint[:8]}). Invoking Gemini ({model})...")
    prompt_payload = _build_gemini_prompt(heuristic_result, telemetry, bulletins_data)
    
    try:
        url = GEMINI_API_ENDPOINT.format(model=model, api_key=key)
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=json.dumps(prompt_payload).encode("utf-8"), headers=headers)
        
        with urllib.request.urlopen(req, timeout=20) as response:
            res_json = json.loads(response.read().decode("utf-8"))
            
        ai_text = (
            res_json.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        
        ai_data = _parse_json_from_llm(ai_text)
        if ai_data:
            briefing = ai_data.get("executive_briefing", heuristic_result["executive_briefing"])
            insights = ai_data.get("insights", [])
            action_items = ai_data.get("parent_action_items", [])

            heuristic_result["executive_briefing"] = briefing
            heuristic_result["ai_insights"] = insights
            heuristic_result["ai_parent_action_items"] = action_items
            heuristic_result["ai_provider"] = "google_gemini"
            heuristic_result["ai_model"] = model
            heuristic_result["ai_cached"] = False

            # Persist to disk cache
            _save_disk_cache({
                "fingerprint": current_fingerprint,
                "executive_briefing": briefing,
                "ai_insights": insights,
                "ai_parent_action_items": action_items,
                "ai_model": model,
                "timestamp_epoch": now_epoch,
            })
            return heuristic_result
            
    except Exception as e:
        print(f"[WARNING] Gemini Flash API call failed ({e}); falling back to heuristic engine.")
        
    heuristic_result["ai_provider"] = "deterministic_heuristic_engine (fallback)"
    heuristic_result["ai_model"] = "heuristic_v1"
    heuristic_result["ai_cached"] = False
    return heuristic_result


def _build_gemini_prompt(heuristic_data: Dict[str, Any], telemetry: Dict[str, Any], bulletins_data: Dict[str, Any]) -> Dict[str, Any]:
    """Prepares structured prompt and schema for Gemini Flash."""
    system_instruction = (
        "You are an expert, reassuring, and highly accurate AI commuter advisor for students and families "
        "traveling from Fauntleroy (West Seattle) to Vashon Island Public Schools (Vashon High, McMurray Middle, "
        "and Chautauqua Elementary).\n\n"
        "Key Commute Rules:\n"
        "1. Vashon school district provides buses waiting at the Vashon north dock to meet the commuter ferries.\n"
        "2. WEEKEND RULE: If today is Saturday or Sunday, school is NOT in session today. You MUST clearly state "
        "   that you are presenting the upcoming MONDAY MORNING school commute plan. Explain whether Monday is expected "
        "   to be in 3-boat (Normal) or 2-boat service based on bulletins, and list the Monday morning target sailings "
        "   (7:05 AM for VHS/McM, 8:05 AM for CES on 3-boat; or 7:20 AM / 8:15 AM on 2-boat).\n"
        "3. Friday is ALWAYS Late Start (PDD - Professional Development Day). Departures shift to 8:25 AM (VHS/McM) and 9:30 AM (CES).\n"
        "4. Students doing after-school sports or clubs take the late sports bus connecting with the 4:40 PM ferry.\n"
        "5. K-5 Elementary students (CES) have an official school chaperone on the 8:05 AM (or 8:15 AM on 2-boat) and 4:40 PM ferries.\n\n"
        "Output ONLY a raw JSON object with no markdown fences, conforming to:\n"
        "{\n"
        "  \"executive_briefing\": \"Clear 2-3 paragraph synthesis explaining today's/Monday's schedule mode, exact target ferries, bus catchability, and sports bus info.\",\n"
        "  \"insights\": [\"bullet 1\", \"bullet 2\"],\n"
        "  \"parent_action_items\": [\"action 1\", \"action 2\"]\n"
        "}"
    )
    
    user_context = {
        "current_time": heuristic_data.get("current_time_display"),
        "is_weekend": heuristic_data.get("is_weekend"),
        "target_commute_day": heuristic_data.get("target_commute_day"),
        "target_commute_title": heuristic_data.get("target_commute_title"),
        "schedule_mode": heuristic_data.get("schedule_mode"),
        "bulletin_mode_reason": heuristic_data.get("bulletin_mode_reason"),
        "active_bulletins": [b.get("title") + ": " + b.get("content") for b in bulletins_data.get("triangle_bulletins", [])],
        "heuristic_evaluated_targets": heuristic_data.get("am_commute", {}).get("evaluated_targets", {}),
    }

    return {
        "contents": [
            {
                "parts": [
                    {"text": f"System Context & Rules:\n{system_instruction}\n\nToday's Live Commute Telemetry & Targets:\n{json.dumps(user_context, indent=2)}"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1000,
            "responseMimeType": "application/json"
        }
    }


def _parse_json_from_llm(raw_text: str) -> Optional[Dict[str, Any]]:
    """Extracts JSON dict from raw LLM output."""
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except Exception:
        return None
