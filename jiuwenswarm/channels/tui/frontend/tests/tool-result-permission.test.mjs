import assert from "node:assert/strict";

import {
  applyToolResult,
  createToolCallDisplay,
} from "../dist/core/history-parser.js";
import {
  isPermissionDeniedToolResult,
  summarizePermissionDeniedToolResult,
} from "../dist/core/tool-result-permission.js";
import { summarizeToolResultByKind } from "../dist/ui/components/tools/tool-render-shared.js";

assert.equal(
  isPermissionDeniedToolResult(
    "[PERMISSION_DENIED] User rejected the tool call. The operation was NOT performed.",
  ),
  true,
);
assert.equal(isPermissionDeniedToolResult("[PERMISSION_REJECTED] User rejected."), true);
assert.equal(isPermissionDeniedToolResult("edit applied"), false);

assert.equal(
  summarizePermissionDeniedToolResult(
    "[PERMISSION_DENIED] User rejected the tool call. The operation was NOT performed.",
  ),
  "permission denied",
);
assert.equal(
  summarizePermissionDeniedToolResult(
    "[PERMISSION_DENIED] User rejected the tool call. The operation was NOT performed. User feedback: dont do nothing",
  ),
  "permission denied · dont do nothing",
);
assert.equal(
  summarizePermissionDeniedToolResult(
    "[PERMISSION_DENIED] 用户拒绝了该工具调用，操作未执行。用户说明：改用 Read",
  ),
  "permission denied · 改用 Read",
);

const baseTool = createToolCallDisplay({
  tool_call: { id: "call-1", name: "write_file", arguments: { file_path: "main.py" } },
});
const deniedTool = applyToolResult(baseTool, {
  tool_result: {
    tool_call_id: "call-1",
    tool_name: "write_file",
    result:
      "[PERMISSION_DENIED] User rejected the tool call. The operation was NOT performed. User feedback: dont do nothing",
  },
});
assert.equal(deniedTool.isError, true);
assert.equal(deniedTool.status, "error");
assert.equal(deniedTool.summary, "permission denied · dont do nothing");

assert.equal(
  summarizeToolResultByKind(
    "write_file",
    "[PERMISSION_DENIED] User rejected the tool call. The operation was NOT performed.",
  ),
  "permission denied",
);
assert.equal(summarizeToolResultByKind("write_file", "Wrote 2 lines"), "edit applied");

console.log("tool result permission tests passed");
