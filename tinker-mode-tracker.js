#!/usr/bin/env node
// tinker — UserPromptSubmit hook to track which tinker mode is active
// Inspects user input for /tinker commands and writes mode to flag file

const fs = require('fs');
const path = require('path');
const os = require('os');
const { getDefaultMode, safeWriteFlag, readFlag } = require('./tinker-config');

const claudeDir = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude');
const flagPath = path.join(claudeDir, '.tinker-active');

let input = '';
process.stdin.on('data', chunk => { input += chunk; });
process.stdin.on('end', () => {
  try {
    const data = JSON.parse(input);
    const prompt = (data.prompt || '').trim().toLowerCase();

    // Natural language activation (e.g. "activate tinker", "turn on tinker mode",
    // "talk like tinker"). README tells users they can say these, but the hook
    // only matched /tinker commands — flag file and statusline stayed out of sync.
    if (/\b(activate|enable|turn on|start|talk like)\b.*\btinker\b/i.test(prompt) ||
        /\btinker\b.*\b(mode|activate|enable|turn on|start)\b/i.test(prompt)) {
      if (!/\b(stop|disable|turn off|deactivate)\b/i.test(prompt)) {
        const mode = getDefaultMode();
        if (mode !== 'off') {
          safeWriteFlag(flagPath, mode);
        }
      }
    }

    if (/\b(activate|enable|turn on|start|be a|act as)\b.*\b(tinker|mad tinker)\b/i.test(prompt) ||
        /\b(tinker|mad tinker)\b.*\b(mode|activate|enable|turn on|start)\b/i.test(prompt)) {
      if (!/\b(stop|disable|turn off|deactivate)\b/i.test(prompt)) {
        safeWriteFlag(flagPath, 'tinker');
      }
    }

    // Match /tinker and /tinker commands
    if (prompt.startsWith('/tinker') || prompt.startsWith('/tinker')) {
      const parts = prompt.split(/\s+/);
      const cmd = parts[0]; 
      const arg = parts[1] || '';

      let mode = null;

      if (cmd === '/tinker-commit') {
        mode = 'commit';
      } else if (cmd === '/tinker-review') {
        mode = 'review';
      } else if (cmd === '/tinker-compress' || cmd === '/tinker:tinker-compress') {
        mode = 'compress';
      } else if (cmd === '/tinker' || cmd === '/tinker:tinker') {
        if (arg === 'lite') mode = 'lite';
        else if (arg === 'ultra') mode = 'ultra';
        else mode = getDefaultMode();
      } else if (cmd === '/tinker') {
        if (arg === 'lite') mode = 'tinker-lite';
        else if (arg === 'ultra') mode = 'tinker-ultra';
        else if (arg === 'compressed') mode = 'tinker-compressed';
        else mode = 'tinker';
      }

      if (mode && mode !== 'off') {
        safeWriteFlag(flagPath, mode);
      } else if (mode === 'off') {
        try { fs.unlinkSync(flagPath); } catch (e) {}
      }
    }

    // Detect deactivation — natural language and slash commands
    if (/\b(stop|disable|deactivate|turn off)\b.*\b(tinker|tinker)\b/i.test(prompt) ||
        /\b(tinker|tinker)\b.*\b(stop|disable|deactivate|turn off)\b/i.test(prompt) ||
        /\bnormal mode\b/i.test(prompt)) {
      try { fs.unlinkSync(flagPath); } catch (e) {}
    }

    // Per-turn reinforcement: emit a structured reminder when active.
    const INDEPENDENT_MODES = new Set(['commit', 'review', 'compress']);
    const activeMode = readFlag(flagPath);
    if (activeMode && !INDEPENDENT_MODES.has(activeMode)) {
      let context = "";
      if (activeMode.startsWith('tinker')) {
        context = "MAD TINKER ACTIVE (" + activeMode + "). Expand ideas, overengineer for robustness, miss nothing. 3+ expansions per req.";
      } else {
        context = "TINKER MODE ACTIVE (" + activeMode + "). Drop articles/filler/pleasantries/hedging. Fragments OK. Code/commits/security: write normal.";
      }
      process.stdout.write(JSON.stringify({
        hookSpecificOutput: {
          hookEventName: "UserPromptSubmit",
          additionalContext: context
        }
      }));
    }
  } catch (e) {
    // Silent fail
  }
});
