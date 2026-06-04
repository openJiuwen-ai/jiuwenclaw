import { execFileSync } from "node:child_process";

function tryClipboard(command: string, args: string[], text: string): boolean {
  try {
    execFileSync(command, args, { input: text, stdio: ["pipe", "ignore", "ignore"] });
    return true;
  } catch {
    return false;
  }
}

function tryClipboardUtf8OnWindows(text: string): boolean {
  try {
    // PowerShell handles UTF-8 correctly on Windows, unlike the `clip` command
    // which silently re-encodes input as ANSI (GBK), breaking Chinese characters.
    const script = `$null = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Set-Content -Path (Join-Path $env:TEMP "clip-tmp.txt") -Value ([System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("${Buffer.from(text, "utf-8").toString("base64")}"))) -Encoding UTF8; Get-Content -Path (Join-Path $env:TEMP "clip-tmp.txt") -Encoding UTF8 -Raw | Set-Clipboard; Remove-Item (Join-Path $env:TEMP "clip-tmp.txt") -Force`;
    execFileSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], {
      stdio: ["pipe", "ignore", "ignore"],
      timeout: 5000,
    });
    return true;
  } catch {
    return false;
  }
}

function isTmux(): boolean {
  return !!process.env.TMUX;
}

function isScreen(): boolean {
  return !!process.env.STYLE;
}

function tryOsc52(text: string): boolean {
  try {
    const base64 = Buffer.from(text, "utf-8").toString("base64");
    let osc52 = `\x1b]52;c;${base64}\x07`;
    if (isTmux()) {
      osc52 = `\x1bPtmux;${osc52}\x1b\\`;
    } else if (isScreen()) {
      osc52 = `\x1bP${osc52}\x1b\\`;
    }
    process.stdout.write(osc52);
    return true;
  } catch {
    return false;
  }
}

export function copyToClipboard(text: string): boolean {
  if (!text) return false;

  if (process.platform === "darwin") {
    return tryClipboard("pbcopy", [], text);
  }

  if (process.platform === "win32") {
    return tryClipboardUtf8OnWindows(text);
  }

  if (process.env.WAYLAND_DISPLAY && tryClipboard("wl-copy", [], text)) {
    return true;
  }

  if (process.env.DISPLAY && tryClipboard("xclip", ["-selection", "clipboard"], text)) {
    return true;
  }

  if (process.env.DISPLAY && tryClipboard("xsel", ["--clipboard", "--input"], text)) {
    return true;
  }

  if (tryOsc52(text)) {
    return true;
  }

  return false;
}
