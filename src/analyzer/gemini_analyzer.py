"""
Gemini Flash AI Analyzer for Fauntleroy-Vashon School Commute.
Leverages Google Gemini Flash (e.g. gemini-2.5-flash) to synthesize
raw vessel AIS telemetry, schedule, bulletins, Friday PDD rules, sports bus schedules,
and weekend lookahead to Monday morning.
"""

import json
import os
import urllib.request
from typing import Dict, Any, Optional

from src.config import DEFAULT_GEMINI_MODEL, GEMINI_API_KEY
from src.analyzer.heuristic_analyzer import evaluate_commute, get_current_pacific_time

GEMINI_API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"


def analyze_commute_with_gemini(
    telemetry: Dict[str, Any],
    bulletins_data: Dict[str, Any],
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    simulated_time: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Runs Gemini Flash on current ferry conditions and VISD commuter rules.
    Falls back gracefully to heuristic engine if API key is missing or call fails.
    """
    key = api_key or os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    model = model_name or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
    
    # First, run the deterministic baseline
    heuristic_result = evaluate_commute(telemetry, bulletins_data, simulated_time=simulated_time)
    
    if not key:
        heuristic_result["ai_provider"] = "deterministic_heuristic_engine"
        heuristic_result["ai_model"] = "heuristic_v1"
        return heuristic_result
        
    # Build prompt for Gemini Flash
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
            heuristic_result["executive_briefing"] = ai_data.get("executive_briefing", heuristic_result["executive_briefing"])
            heuristic_result["ai_insights"] = ai_data.get("insights", [])
            heuristic_result["ai_parent_action_items"] = ai_data.get("parent_action_items", [])
            heuristic_result["ai_provider"] = "google_gemini"
            heuristic_result["ai_model"] = model
            return heuristic_result
            
    except Exception as e:
        print(f"[WARNING] Gemini Flash API call failed ({e}); falling back to heuristic engine.")
        
    heuristic_result["ai_provider"] = "deterministic_heuristic_engine (fallback)"
    heuristic_result["ai_model"] = "heuristic_v1"
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
