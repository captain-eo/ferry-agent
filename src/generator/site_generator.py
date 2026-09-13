"""
Site Generator for Static 1-Page Application.
Combines template.html with live analysis JSON, producing index.html, data.json, and favicon.svg.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any

TEMPLATE_PATH = Path(__file__).parent / "template.html"

# Browser Tab Favicon: Matches the header's emerald ship brand icon
FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
  <rect x="1" y="1" width="30" height="30" rx="8" fill="#0f172a" stroke="#059669" stroke-width="1.5"/>
  <g transform="translate(4, 4)" fill="none" stroke="#34d399" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M12 2v2" />
    <path d="M12 9.189V13" />
    <path d="M19 12V6a2 2 0 00-2-2H7a2 2 0 00-2 2v6" />
    <path d="M19.38 19A11.6 11.6 0 0021 13l-8.188-3.639a2 2 0 00-1.624 0L3 13.001a11.6 11.6 0 002.81 7.76" />
    <path d="M2 20c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1s1.2 1 2.5 1c2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" />
  </g>
</svg>
"""


def generate_static_site(analysis_data: Dict[str, Any], output_dir: str) -> Dict[str, str]:
    """
    Renders index.html and writes data.json and favicon.svg into output_dir.
    Returns dictionary with paths of generated files.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # 1. Write data.json
    data_json_path = out_path / "data.json"
    with open(data_json_path, "w", encoding="utf-8") as f:
        json.dump(analysis_data, f, indent=2)
        
    # 2. Write favicon.svg
    favicon_path = out_path / "favicon.svg"
    with open(favicon_path, "w", encoding="utf-8") as f:
        f.write(FAVICON_SVG.strip())

    # 3. Read template.html
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template_str = f.read()
        
    # 4. Inject initial data into HTML
    json_serialized = json.dumps(analysis_data).replace("</script>", "<\\/script>")
    rendered_html = template_str.replace("{{INITIAL_DATA_JSON}}", json_serialized)
    
    index_html_path = out_path / "index.html"
    with open(index_html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)
        
    return {
        "index_html": str(index_html_path),
        "data_json": str(data_json_path),
        "favicon_svg": str(favicon_path),
    }
