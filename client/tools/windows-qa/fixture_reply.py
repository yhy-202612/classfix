"""Supply a deterministic response to the text chat in the Windows QA build only."""
import json
from pathlib import Path

source = Path("desktop_goose/src/DeepSeek.cs")
original = 'using (var response = await client.SendAsync(request, cancel).ConfigureAwait(false))'
body = json.dumps({"choices": [{"message": {"content": (
    "Subtract 6 from both sides to keep the equation balanced.\n\n"
    "2x + 6 - 6 = 14 - 6\n2x = 8\n\nDivide both sides by 2 to get x = 4."
)}, "finish_reason": "stop"}]})
fixture = ("using (var response = new HttpResponseMessage(HttpStatusCode.OK) "
           "{ Content = new StringContent(" + json.dumps(body) + ") })")
contents = source.read_text(encoding="utf-8")
assert contents.count(original) == 1
source.write_text(contents.replace(original, fixture), encoding="utf-8")
