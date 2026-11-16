// Windows 背景執行時，讓 child_process 開出的子程式（python、tailscale、AI 命令列…）預設不跳出黑色視窗。
// 在程式最前面 require 一次即可；非 Windows 不做任何事。已明確指定 windowsHide 的呼叫維持原值。
function patchArgs(a) {
  const args = Array.prototype.slice.call(a);
  const idx = args.findIndex((x, i) => i > 0 && x && typeof x === "object" && !Array.isArray(x));
  if (idx >= 0) { args[idx] = Object.assign({ windowsHide: true }, args[idx]); return args; }
  const cb = args.findIndex((x) => typeof x === "function");
  if (cb >= 0) args.splice(cb, 0, { windowsHide: true }); else args.push({ windowsHide: true });
  return args;
}
function install(force) {
  if (process.platform !== "win32" && !force) return false;
  const cp = require("child_process");
  if (cp.__zziHidden) return true;
  for (const k of ["spawn", "execFile", "spawnSync", "execFileSync"]) {
    const orig = cp[k];
    cp[k] = function () { return orig.apply(this, patchArgs(arguments)); };
  }
  cp.__zziHidden = true;
  return true;
}
install();
module.exports = { patchArgs, install };
