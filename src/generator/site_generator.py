"""
Site Generator for Static 1-Page Application.
Combines template.html with live analysis JSON, producing index.html and data.json.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any

TEMPLATE_PATH = Path(__file__).parent / "template.html"


def generate_static_site(analysis_data: Dict[str, Any], output_dir: str) -> Dict[str, str]:
    """
    Renders index.html and writes data.json into output_dir.
    Returns dictionary with paths of generated files.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # 1. Write data.json
    data_json_path = out_path / "data.json"
    with open(data_json_path, "w", encoding="utf-8") as f:
        json.dump(analysis_data, f, indent=2)
        
    # 2. Read template.html
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template_str = f.read()
        
    # 3. Inject initial data into HTML
    json_serialized = json.dumps(analysis_data).replace("</script>", "<\\/script>")
    rendered_html = template_str.replace("{{INITIAL_DATA_JSON}}", json_serialized)
    
    index_html_path = out_path / "index.html"
    with open(index_html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)
        
    return {
        "index_html": str(index_html_path),
        "data_json": str(data_json_path),
    }
