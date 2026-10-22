"""Make an authored Responses fixture. This is NOT an actual LLM response."""
import json
from pathlib import Path
import sys

request = json.loads(Path(sys.argv[1]).read_text())
guard = {"source_sha256": request["source_sha256"], "region_id": request["region_id"],
         "guard_code": "if (length > 4096) return 0;", "rationale": "Enforce request resource quota.",
         "security_condition": "Reject length above 4096."}
response = {"id": "authored-fixture", "model": "fixture-not-a-model", "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(guard)}]}],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}
with Path(sys.argv[2]).open("x") as stream:
    json.dump(response, stream, indent=2)
    stream.write("\n")
